"""
Operation Abhedya-Chakra -- Dynamic Dataset Ingestion & Schema Mapping Engine
Supports CSV and Excel transaction datasets, unambiguous schema mapping,
isolated DuckDB storage per dataset, data quality validation, and dynamic activation.
"""

import os
import re
import time
import json
import duckdb
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATASETS_DIR = DATA_DIR / "datasets"
REGISTRY_FILE = DATASETS_DIR / "registry.json"

# Canonical schema definition
CANONICAL_COLUMNS = [
    "row_id",
    "Transaction_ID",
    "Sender_Account",
    "Receiver_Account",
    "Sender_IFSC",
    "Receiver_IFSC",
    "Amount",
    "Timestamp",
    "Payment_Mode",
    "Narration",
    "IP_Address",
    "Device_Type"
]

# Unambiguous header synonym dictionary (case-insensitive & stripped)
COLUMN_SYNONYMS: Dict[str, List[str]] = {
    "Transaction_ID": [
        "transaction_id", "transactionid", "txn_id", "txnid",
        "reference_no", "ref_no", "utr", "trans_id", "tx_id", "id"
    ],
    "Sender_Account": [
        "sender_account", "senderaccount", "sender", "from_account",
        "fromaccount", "remitter_account", "remitter", "source_account",
        "source", "sender_acc", "from_acc", "debit_account"
    ],
    "Receiver_Account": [
        "receiver_account", "receiveraccount", "receiver", "to_account",
        "toaccount", "beneficiary_account", "beneficiary", "payee",
        "dest_account", "receiver_acc", "to_acc", "credit_account"
    ],
    "Sender_IFSC": [
        "sender_ifsc", "senderifsc", "from_ifsc", "fromifsc",
        "remitter_ifsc", "source_ifsc", "sender_bank_code"
    ],
    "Receiver_IFSC": [
        "receiver_ifsc", "receiverifsc", "to_ifsc", "toifsc",
        "beneficiary_ifsc", "dest_ifsc", "receiver_bank_code"
    ],
    "Amount": [
        "amount", "amt", "inr", "txn_amount", "transaction_amount",
        "txn_amt", "value", "total_amount"
    ],
    "Timestamp": [
        "timestamp", "txn_timestamp", "date_time", "datetime",
        "txn_date_time", "transaction_timestamp", "time", "date", "txn_date"
    ],
    "Payment_Mode": [
        "payment_mode", "paymentmode", "mode", "txn_mode",
        "channel", "type", "payment_channel", "payment_type"
    ],
    "Narration": [
        "narration", "remarks", "description", "memo", "notes", "remark"
    ],
    "IP_Address": [
        "ip_address", "ipaddress", "ip", "client_ip", "source_ip", "user_ip"
    ],
    "Device_Type": [
        "device_type", "devicetype", "device", "client_device", "channel_device"
    ]
}

def normalize_column_name(col: Any) -> str:
    """Trims whitespace, strips quotes, and normalizes harmless casing and separators."""
    if col is None:
        return ""
    s = str(col).strip().strip("'\"").strip()
    s = s.replace("\ufeff", "").replace("\u00a0", " ")
    clean = re.sub(r'[\s\-_./]+', '_', s.lower()).strip('_')
    return clean

def detect_column_mapping(columns: List[Any], verbose: bool = True) -> Tuple[Dict[str, str], List[str], List[str]]:
    """
    Detects unambiguous column mapping from uploaded column headers to canonical columns.
    Normalizes column names before mandatory-column validation:
    - trim whitespace
    - normalize harmless casing differences
    - preserve the canonical column names
    Returns:
    (mapped_dict, missing_optional_fields, missing_mandatory_fields)
    """
    normalized_incoming: Dict[str, str] = {}
    for col in columns:
        clean = normalize_column_name(col)
        if clean:
            normalized_incoming[clean] = str(col).strip()

    mapping: Dict[str, str] = {}
    unmapped_canonical: List[str] = []

    for canonical, synonyms in COLUMN_SYNONYMS.items():
        found = False
        clean_canon = normalize_column_name(canonical)
        # 1. Direct canonical check
        if clean_canon in normalized_incoming:
            mapping[canonical] = normalized_incoming[clean_canon]
            found = True
        else:
            # 2. Synonym / alias check
            for syn in synonyms:
                clean_syn = normalize_column_name(syn)
                if clean_syn in normalized_incoming:
                    mapping[canonical] = normalized_incoming[clean_syn]
                    found = True
                    break
        if not found:
            unmapped_canonical.append(canonical)

    mandatory = ["Sender_Account", "Receiver_Account", "Amount"]
    missing_mandatory = [m for m in mandatory if m not in mapping]
    missing_optional = [m for m in unmapped_canonical if m not in mandatory]

    if verbose:
        print(f"[Schema Mapping] Original columns        : {list(columns)}")
        print(f"[Schema Mapping] Normalized columns      : {list(normalized_incoming.keys())}")
        print(f"[Schema Mapping] Resolved sender column  : {mapping.get('Sender_Account')}")
        print(f"[Schema Mapping] Resolved receiver column: {mapping.get('Receiver_Account')}")
        print(f"[Schema Mapping] Resolved amount column  : {mapping.get('Amount')}")

    return mapping, missing_optional, missing_mandatory

def _ensure_datasets_dir():
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    if not REGISTRY_FILE.exists():
        default_registry = {
            "default": {
                "dataset_id": "default",
                "name": "Default 20-Lakh Dataset",
                "source_file": "VoidHacks8_MuleAccount_2M_Transactions.csv",
                "db_path": str(DATA_DIR / "transactions.duckdb"),
                "is_default": True,
                "row_count": 2000000,
                "columns_mapped": {c: c for c in CANONICAL_COLUMNS if c != "row_id"},
                "unsupported_indicators": [],
                "limitations": [],
                "created_at": "2026-10-02 01:24:00"
            }
        }
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(default_registry, f, indent=2)

def load_dataset_registry() -> Dict[str, Any]:
    _ensure_datasets_dir()
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_dataset_registry(reg: Dict[str, Any]):
    _ensure_datasets_dir()
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)

def ingest_dataset_file(
    file_path: Path,
    dataset_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ingests any valid CSV or Excel transaction file into a separate isolated DuckDB.
    Validates quality, applies canonical schema mapping, generates permanent row_id,
    creates high-performance indexes, trains isolated ML anomaly model, and registers dataset.
    """
    _ensure_datasets_dir()
    t_start = time.perf_counter()

    ext = file_path.suffix.lower()
    if ext not in [".csv", ".xlsx", ".xls"]:
        raise ValueError(f"Unsupported file format '{ext}'. Only CSV and Excel (.xlsx, .xls) are supported.")

    # 1. Inspect headers
    if ext == ".csv":
        # Read header only
        sample_df = pd.read_csv(file_path, nrows=5)
    else:
        sample_df = pd.read_excel(file_path, nrows=5)

    incoming_columns = list(sample_df.columns)
    mapping, missing_optional, missing_mandatory = detect_column_mapping(incoming_columns)

    if missing_mandatory:
        raise ValueError(
            f"Uploaded dataset is missing mandatory columns: {missing_mandatory}. "
            "Financial flow tracing requires at least sender account, receiver account, and amount."
        )

    # 2. Generate isolated dataset ID and paths
    safe_name = dataset_name or file_path.stem
    slug = re.sub(r'[^a-zA-Z0-9_]+', '_', safe_name).strip('_').lower()
    dataset_id = f"ds_{slug}_{int(time.time())}"
    target_db_path = DATASETS_DIR / f"{dataset_id}.duckdb"
    ml_cache_path = DATASETS_DIR / f"{dataset_id}_ml_cache.json"

    # 3. Build DuckDB selection expression
    select_exprs = []
    # For deterministic ordering of row_id
    order_by_cols = []

    for canon in CANONICAL_COLUMNS:
        if canon == "row_id":
            continue
        if canon in mapping:
            orig = mapping[canon]
            # Handle timestamps and types cleanly
            if canon == "Amount":
                expr = f"CAST(\"{orig}\" AS DOUBLE) AS Amount"
            elif canon == "Timestamp":
                expr = f"CAST(\"{orig}\" AS TIMESTAMP) AS Timestamp"
                order_by_cols.append("Timestamp")
            else:
                expr = f"CAST(\"{orig}\" AS VARCHAR) AS {canon}"
                order_by_cols.append(canon)
            select_exprs.append(expr)
        else:
            # Missing optional field: DO NOT FABRICATE DATA, set NULL
            if canon == "Amount":
                select_exprs.append("CAST(NULL AS DOUBLE) AS Amount")
            elif canon == "Timestamp":
                select_exprs.append("CAST(NULL AS TIMESTAMP) AS Timestamp")
            else:
                select_exprs.append(f"CAST(NULL AS VARCHAR) AS {canon}")

    if not order_by_cols:
        order_by_cols = ["1"]
    order_by_clause = ", ".join(order_by_cols)

    # 4. Ingest into isolated DuckDB
    con = duckdb.connect(str(target_db_path))
    try:
        if ext == ".csv":
            source_table = f"read_csv_auto('{file_path.as_posix()}')"
        else:
            # Excel: load with pandas and register as view
            excel_df = pd.read_excel(file_path)
            con.register("raw_excel_source", excel_df)
            source_table = "raw_excel_source"

        create_sql = f"""
            CREATE TABLE transactions AS 
            SELECT 
                ROW_NUMBER() OVER (ORDER BY {order_by_clause}) AS row_id,
                Transaction_ID,
                Sender_Account,
                Receiver_Account,
                Sender_IFSC,
                Receiver_IFSC,
                Amount,
                Timestamp,
                Payment_Mode,
                Narration,
                IP_Address,
                Device_Type
            FROM (
                SELECT {', '.join(select_exprs)}
                FROM {source_table}
            )
        """
        con.execute(create_sql)

        # Create indexes
        con.execute("CREATE UNIQUE INDEX idx_row_id ON transactions(row_id)")
        con.execute("CREATE INDEX idx_sender ON transactions(Sender_Account)")
        con.execute("CREATE INDEX idx_receiver ON transactions(Receiver_Account)")
        if "Timestamp" in mapping:
            con.execute("CREATE INDEX idx_timestamp ON transactions(Timestamp)")

        # Verify ingested stats
        row_count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        acc_count = con.execute("""
            SELECT COUNT(DISTINCT acc) FROM (
                SELECT Sender_Account AS acc FROM transactions WHERE Sender_Account IS NOT NULL
                UNION
                SELECT Receiver_Account AS acc FROM transactions WHERE Receiver_Account IS NOT NULL
            )
        """).fetchone()[0]

    finally:
        con.close()

    # 5. Determine limitations and unsupported indicators
    unsupported_indicators = []
    limitations = []

    if "Timestamp" not in mapping:
        unsupported_indicators.append("fast_pass_through")
        limitations.append("Missing Timestamp: Fast pass-through velocity rule (<15m window) is not evaluated.")

    if "IP_Address" not in mapping and "Device_Type" not in mapping:
        unsupported_indicators.append("rare_infrastructure")
        limitations.append("Missing IP_Address & Device_Type: Rare infrastructure indicator is not evaluated.")

    if missing_optional:
        limitations.append(f"Optional fields unmapped: {', '.join(missing_optional)} (preserved as NULL without data fabrication).")

    # 6. Train and pre-cache ML Anomaly model for this dataset
    from .ml_anomaly import train_and_cache_ml_anomaly
    try:
        train_and_cache_ml_anomaly(db_path=str(target_db_path), cache_file=str(ml_cache_path))
    except Exception as e:
        limitations.append(f"ML baseline training note: {str(e)}")

    duration_s = round(time.perf_counter() - t_start, 2)

    dataset_meta = {
        "dataset_id": dataset_id,
        "name": safe_name,
        "source_file": file_path.name,
        "db_path": str(target_db_path),
        "ml_cache_path": str(ml_cache_path),
        "is_default": False,
        "row_count": row_count,
        "accounts_count": acc_count,
        "columns_mapped": mapping,
        "missing_optional_fields": missing_optional,
        "unsupported_indicators": unsupported_indicators,
        "limitations": limitations,
        "ingest_duration_seconds": duration_s,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    # Register in registry
    registry = load_dataset_registry()
    registry[dataset_id] = dataset_meta
    save_dataset_registry(registry)

    return dataset_meta

def activate_dataset_by_id(dataset_id: str) -> Dict[str, Any]:
    """
    Switches active analysis dataset to the specified dataset_id.
    Validates existence, updates backend/db.py state, and flushes all caches.
    """
    from .db import set_active_dataset, reset_to_default_dataset, _DEFAULT_DB_PATH, _DEFAULT_PQ_PATH

    if dataset_id in ["default", "default_20l_master"]:
        reset_to_default_dataset()
        from .db import get_active_dataset
        return get_active_dataset()

    registry = load_dataset_registry()
    if dataset_id not in registry:
        raise ValueError(f"Dataset ID '{dataset_id}' not found in registry.")

    meta = registry[dataset_id]
    target_db = Path(meta["db_path"])
    if not target_db.exists():
        raise FileNotFoundError(f"Database file for dataset '{dataset_id}' not found at {target_db}.")

    active_info = {
        "dataset_id": dataset_id,
        "name": meta["name"],
        "source_file": meta["source_file"],
        "db_path": target_db,
        "parquet_path": None,
        "ml_cache_path": meta.get("ml_cache_path"),
        "is_default": False,
        "row_count": meta["row_count"],
        "accounts_count": meta.get("accounts_count"),
        "columns_mapped": meta.get("columns_mapped", {}),
        "unsupported_indicators": meta.get("unsupported_indicators", []),
        "limitations": meta.get("limitations", [])
    }

    set_active_dataset(active_info)
    return active_info
