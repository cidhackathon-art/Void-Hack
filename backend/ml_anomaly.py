"""
Operation Abhedya-Chakra -- Controlled ML Anomaly Detection Capability
Module: backend.ml_anomaly

Architectural Guarantees:
1. DuckDB + deterministic forensic logic remain the sole SOURCE OF TRUTH.
2. Unsupervised Isolation Forest (Liu, Ting, Zhou, 2008) trained strictly on account-level aggregates.
3. Completely read-only with respect to DuckDB, Parquet, and CSV storage.
4. Exposes statistical deviation from learned baseline without asserting fraud, guilt, or criminal intent.
5. Cached to disk to prevent re-aggregating 2M rows on per-request calls.
"""

import os
import json
import time
from typing import Dict, Any, Optional, List
import numpy as np
import duckdb

def c_factor(n: int) -> float:
    """Average path length of unsuccessful search in a Binary Search Tree (BST)."""
    if n <= 1:
        return 1.0
    if n == 2:
        return 1.0
    return 2.0 * (np.log(n - 1) + 0.5772156649) - (2.0 * (n - 1) / n)

class IsolationTreeNode:
    __slots__ = ("left", "right", "split_feature", "split_value", "size", "is_leaf")
    def __init__(self, left=None, right=None, split_feature=None, split_value=None, size=1):
        self.left = left
        self.right = right
        self.split_feature = split_feature
        self.split_value = split_value
        self.size = size
        self.is_leaf = left is None and right is None

class IsolationTree:
    def __init__(self, max_depth: int, rng: np.random.RandomState):
        self.max_depth = max_depth
        self.rng = rng
        self.root: Optional[IsolationTreeNode] = None

    def fit(self, X: np.ndarray, depth: int = 0) -> IsolationTreeNode:
        n_samples, n_features = X.shape
        if depth >= self.max_depth or n_samples <= 1:
            return IsolationTreeNode(size=n_samples)

        feat = int(self.rng.randint(0, n_features))
        feat_min = float(X[:, feat].min())
        feat_max = float(X[:, feat].max())
        if feat_min == feat_max:
            return IsolationTreeNode(size=n_samples)

        split_val = float(self.rng.uniform(feat_min, feat_max))
        left_mask = X[:, feat] < split_val
        right_mask = ~left_mask

        if left_mask.sum() == 0 or right_mask.sum() == 0:
            return IsolationTreeNode(size=n_samples)

        left_child = self.fit(X[left_mask], depth + 1)
        right_child = self.fit(X[right_mask], depth + 1)
        return IsolationTreeNode(
            left=left_child,
            right=right_child,
            split_feature=feat,
            split_value=split_val,
            size=n_samples
        )

    def path_length(self, x: np.ndarray, node: IsolationTreeNode, current_depth: int = 0) -> float:
        if node.is_leaf:
            return current_depth + c_factor(node.size)
        if x[node.split_feature] < node.split_value:
            return self.path_length(x, node.left, current_depth + 1)
        else:
            return self.path_length(x, node.right, current_depth + 1)

class IsolationForestEngine:
    """
    Unsupervised Isolation Forest algorithm for statistical anomaly detection.
    Guarantees pure NumPy execution immune to OS DLL security blocks.
    """
    def __init__(self, n_estimators: int = 100, max_samples: int = 256, random_state: int = 42):
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.random_state = random_state
        self.trees: List[IsolationTree] = []
        self.c_val = c_factor(max_samples)

    def fit(self, X: np.ndarray):
        rng = np.random.RandomState(self.random_state)
        n_samples = X.shape[0]
        subsample_size = min(self.max_samples, n_samples)
        max_depth = int(np.ceil(np.log2(max(subsample_size, 2))))
        self.c_val = c_factor(subsample_size)
        self.trees = []

        for _ in range(self.n_estimators):
            tree_rng = np.random.RandomState(int(rng.randint(0, 1000000)))
            indices = tree_rng.choice(n_samples, size=subsample_size, replace=False)
            sub_X = X[indices]
            tree = IsolationTree(max_depth=max_depth, rng=tree_rng)
            tree.root = tree.fit(sub_X)
            self.trees.append(tree)
        return self

    def compute_anomaly_score_single(self, x: np.ndarray) -> float:
        avg_path = np.mean([tree.path_length(x, tree.root) for tree in self.trees])
        return float(2.0 ** (- (avg_path / self.c_val)))

    def compute_anomaly_score_batch(self, X: np.ndarray) -> np.ndarray:
        n_samples = X.shape[0]
        total_paths = np.zeros(n_samples, dtype=np.float64)

        for tree in self.trees:
            def traverse(node: IsolationTreeNode, indices: np.ndarray, depth: int):
                if len(indices) == 0:
                    return
                if node.is_leaf:
                    total_paths[indices] += depth + c_factor(node.size)
                    return
                vals = X[indices, node.split_feature]
                left = vals < node.split_value
                traverse(node.left, indices[left], depth + 1)
                traverse(node.right, indices[~left], depth + 1)

            traverse(tree.root, np.arange(n_samples), 0)

        avg_paths = total_paths / len(self.trees)
        return 2.0 ** (- (avg_paths / self.c_val))

FEATURE_COLUMNS = [
    'in_txn_count',
    'out_txn_count',
    'total_txn_count',
    'total_incoming_amount',
    'total_outgoing_amount',
    'out_in_ratio',
    'unique_senders',
    'unique_receivers',
    'unique_counterparties',
    'avg_txn_amount',
    'max_txn_amount',
    'std_txn_amount',
    'unique_ips',
    'unique_devices'
]

CACHE_FILE_PATH = os.path.join("data", "ml_anomaly_cache.json")
DEFAULT_DB_PATH = os.path.join("data", "transactions.duckdb")

_IN_MEMORY_CACHE: Optional[Dict[str, Any]] = None

def extract_account_features(con: duckdb.DuckDBPyConnection):
    """
    Extracts high-dimensional account-level statistical features
    from 2,000,000 raw transactions in a single columnar pass.
    """
    query = """
    WITH in_stats AS (
        SELECT 
            Receiver_Account as acc,
            count(*) as in_txn_count,
            sum(Amount) as total_incoming_amount,
            count(distinct Sender_Account) as unique_senders,
            max(Amount) as max_in_amount,
            stddev_samp(Amount) as std_in_amount,
            count(distinct IP_Address) as in_ips,
            count(distinct Device_Type) as in_devices
        FROM transactions
        GROUP BY Receiver_Account
    ),
    out_stats AS (
        SELECT 
            Sender_Account as acc,
            count(*) as out_txn_count,
            sum(Amount) as total_outgoing_amount,
            count(distinct Receiver_Account) as unique_receivers,
            max(Amount) as max_out_amount,
            stddev_samp(Amount) as std_out_amount,
            count(distinct IP_Address) as out_ips,
            count(distinct Device_Type) as out_devices
        FROM transactions
        GROUP BY Sender_Account
    ),
    all_accs AS (
        SELECT acc FROM in_stats
        UNION
        SELECT acc FROM out_stats
    )
    SELECT 
        a.acc as account_id,
        coalesce(i.in_txn_count, 0) as in_txn_count,
        coalesce(o.out_txn_count, 0) as out_txn_count,
        (coalesce(i.in_txn_count, 0) + coalesce(o.out_txn_count, 0)) as total_txn_count,
        coalesce(i.total_incoming_amount, 0.0) as total_incoming_amount,
        coalesce(o.total_outgoing_amount, 0.0) as total_outgoing_amount,
        CASE 
            WHEN coalesce(i.total_incoming_amount, 0.0) > 0 
            THEN coalesce(o.total_outgoing_amount, 0.0) / i.total_incoming_amount 
            ELSE 0.0 
        END as out_in_ratio,
        coalesce(i.unique_senders, 0) as unique_senders,
        coalesce(o.unique_receivers, 0) as unique_receivers,
        (coalesce(i.unique_senders, 0) + coalesce(o.unique_receivers, 0)) as unique_counterparties,
        (coalesce(i.total_incoming_amount, 0.0) + coalesce(o.total_outgoing_amount, 0.0)) / 
            greatest(1, (coalesce(i.in_txn_count, 0) + coalesce(o.out_txn_count, 0))) as avg_txn_amount,
        greatest(coalesce(i.max_in_amount, 0.0), coalesce(o.max_out_amount, 0.0)) as max_txn_amount,
        greatest(coalesce(i.std_in_amount, 0.0), coalesce(o.std_out_amount, 0.0)) as std_txn_amount,
        greatest(coalesce(i.in_ips, 0), coalesce(o.out_ips, 0)) as unique_ips,
        greatest(coalesce(i.in_devices, 0), coalesce(o.out_devices, 0)) as unique_devices
    FROM all_accs a
    LEFT JOIN in_stats i ON a.acc = i.acc
    LEFT JOIN out_stats o ON a.acc = o.acc
    """
    return con.execute(query).df()

def train_and_cache_ml_anomaly(db_path: str = DEFAULT_DB_PATH, cache_file: str = CACHE_FILE_PATH) -> Dict[str, Any]:
    """
    Trains the unsupervised Isolation Forest and serializes inferred account-level scores.
    """
    global _IN_MEMORY_CACHE
    t0 = time.time()

    con = duckdb.connect(db_path, read_only=True)
    try:
        df = extract_account_features(con)
    finally:
        con.close()

    extract_time = time.time() - t0

    accounts = df['account_id'].values.tolist()
    X = df[FEATURE_COLUMNS].fillna(0.0).values

    t_train = time.time()
    model = IsolationForestEngine(n_estimators=100, max_samples=256, random_state=42)
    model.fit(X)
    train_duration = time.time() - t_train

    t_inf = time.time()
    scores = model.compute_anomaly_score_batch(X)
    inf_duration = time.time() - t_inf

    sorted_scores = np.sort(scores)
    n_total = len(sorted_scores)

    results_map = {}
    for i, acc in enumerate(accounts):
        sc = float(scores[i])
        pct = float((np.searchsorted(sorted_scores, sc) / n_total) * 100.0)
        if sc >= 0.60:
            level = "High deviation from learned baseline"
        elif sc >= 0.45:
            level = "Moderate deviation from learned baseline"
        else:
            level = "Low deviation from learned baseline"

        results_map[acc] = {
            "score": round(sc, 4),
            "percentile": round(pct, 1),
            "level": level
        }

    cache_data = {
        "metadata": {
            "model": "Isolation Forest",
            "algorithm": "Unsupervised Tree Isolation (Liu et al. 2008)",
            "random_state": 42,
            "n_estimators": 100,
            "max_samples": 256,
            "accounts_evaluated": len(accounts),
            "feature_columns": FEATURE_COLUMNS,
            "extract_time_seconds": round(extract_time, 2),
            "train_time_seconds": round(train_duration, 4),
            "batch_inference_seconds": round(inf_duration, 4),
            "score_stats": {
                "min": round(float(scores.min()), 4),
                "max": round(float(scores.max()), 4),
                "mean": round(float(scores.mean()), 4),
                "median": round(float(np.median(scores)), 4)
            }
        },
        "accounts": results_map
    }

    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache_data, f)

    _IN_MEMORY_CACHE = cache_data
    return cache_data

def get_ml_anomaly_cache(cache_file: str = CACHE_FILE_PATH, db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    """
    Returns cached ML results, loading from disk or computing once if missing.
    """
    global _IN_MEMORY_CACHE
    if _IN_MEMORY_CACHE is not None:
        return _IN_MEMORY_CACHE

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                _IN_MEMORY_CACHE = json.load(f)
                return _IN_MEMORY_CACHE
        except Exception:
            pass

    return train_and_cache_ml_anomaly(db_path=db_path, cache_file=cache_file)

def get_account_ml_anomaly(account_id: str, db_path: str = DEFAULT_DB_PATH) -> Optional[Dict[str, Any]]:
    """
    Retrieves the verified ML Anomaly Signal for a target account.
    Returns None if account does not exist.
    """
    try:
        cache = get_ml_anomaly_cache(db_path=db_path)
        accounts_map = cache.get("accounts", {})

        if account_id not in accounts_map:
            return None

        acc_data = accounts_map[account_id]
        meta = cache.get("metadata", {})

        return {
            "account_id": account_id,
            "ml_anomaly_score": acc_data["score"],
            "ml_anomaly_percentile": acc_data["percentile"],
            "ml_anomaly_level": acc_data["level"],
            "model": meta.get("model", "Isolation Forest"),
            "status": "available",
            "disclaimer": "ML anomaly signal indicates statistical deviation from learned transaction patterns and is not a fraud determination."
        }
    except Exception as e:
        # Graceful degradation: ML error must never break deterministic engine
        return {
            "account_id": account_id,
            "ml_anomaly_score": None,
            "ml_anomaly_percentile": None,
            "ml_anomaly_level": "Unavailable",
            "model": "Isolation Forest",
            "status": "error",
            "error_detail": str(e),
            "disclaimer": "ML anomaly signal indicates statistical deviation from learned transaction patterns and is not a fraud determination."
        }
