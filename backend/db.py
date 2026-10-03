import os
import duckdb
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import contextmanager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

config = load_config()
_DEFAULT_DB_RELATIVE = config.get("paths", {}).get("duckdb_database", "data/transactions.duckdb")
_DEFAULT_DB_PATH = PROJECT_ROOT / _DEFAULT_DB_RELATIVE
_DEFAULT_PQ_RELATIVE = config.get("paths", {}).get("parquet_file", "data/transactions.parquet")
_DEFAULT_PQ_PATH = PROJECT_ROOT / _DEFAULT_PQ_RELATIVE

# Default reference DB_PATH export for backwards compatibility
DB_PATH = _DEFAULT_DB_PATH
PARQUET_PATH = _DEFAULT_PQ_PATH

# Global Active Dataset State
_ACTIVE_DATASET: Dict[str, Any] = {
    "dataset_id": "default",
    "name": "Default 20-Lakh Dataset",
    "source_file": "VoidHacks8_MuleAccount_2M_Transactions.csv",
    "db_path": _DEFAULT_DB_PATH,
    "parquet_path": _DEFAULT_PQ_PATH,
    "ml_cache_path": str(PROJECT_ROOT / "data" / "ml_anomaly_cache.json"),
    "is_default": True,
    "row_count": 2000000,
    "columns_mapped": {
        "Transaction_ID": "Transaction_ID",
        "Sender_Account": "Sender_Account",
        "Receiver_Account": "Receiver_Account",
        "Sender_IFSC": "Sender_IFSC",
        "Receiver_IFSC": "Receiver_IFSC",
        "Amount": "Amount",
        "Timestamp": "Timestamp",
        "Payment_Mode": "Payment_Mode",
        "Narration": "Narration",
        "IP_Address": "IP_Address",
        "Device_Type": "Device_Type"
    },
    "unsupported_indicators": [],
    "limitations": []
}

def get_active_dataset() -> Dict[str, Any]:
    """Returns metadata about the active dataset."""
    return _ACTIVE_DATASET

def get_db_path() -> Path:
    """Returns the DuckDB database path for the CURRENT active dataset."""
    return Path(_ACTIVE_DATASET["db_path"])

def set_active_dataset(dataset_info: Dict[str, Any]):
    """Switches active dataset and invalidates all system caches."""
    global _ACTIVE_DATASET
    _ACTIVE_DATASET = dataset_info
    invalidate_all_caches()

def reset_to_default_dataset():
    """Resets active dataset back to original 20-lakh transaction database."""
    global _ACTIVE_DATASET
    _ACTIVE_DATASET = {
        "dataset_id": "default",
        "name": "Default 20-Lakh Dataset",
        "source_file": "VoidHacks8_MuleAccount_2M_Transactions.csv",
        "db_path": _DEFAULT_DB_PATH,
        "parquet_path": _DEFAULT_PQ_PATH,
        "ml_cache_path": str(PROJECT_ROOT / "data" / "ml_anomaly_cache.json"),
        "is_default": True,
        "row_count": 2000000,
        "columns_mapped": {
            "Transaction_ID": "Transaction_ID",
            "Sender_Account": "Sender_Account",
            "Receiver_Account": "Receiver_Account",
            "Sender_IFSC": "Sender_IFSC",
            "Receiver_IFSC": "Receiver_IFSC",
            "Amount": "Amount",
            "Timestamp": "Timestamp",
            "Payment_Mode": "Payment_Mode",
            "Narration": "Narration",
            "IP_Address": "IP_Address",
            "Device_Type": "Device_Type"
        },
        "unsupported_indicators": [],
        "limitations": []
    }
    invalidate_all_caches()

def invalidate_all_caches():
    """Flushes in-memory caches across all modules upon dataset switch."""
    try:
        from . import data_access
        data_access._SCORED_ACCOUNTS_CACHE = None
    except Exception:
        pass

    try:
        from . import main
        main._case_overview_cache = None
        main._detection_engine = None
    except Exception:
        pass

    try:
        from . import ml_anomaly
        ml_anomaly._IN_MEMORY_CACHE = None
    except Exception:
        pass

def get_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """
    Returns a DuckDB connection to the CURRENT ACTIVE DATASET.
    Defaults to read_only=True for concurrent web server access.
    """
    db_p = get_db_path()
    if not db_p.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {db_p}. "
            "Please run data ingestion first."
        )
    return duckdb.connect(str(db_p), read_only=read_only)

@contextmanager
def get_db_cursor(read_only: bool = True):
    """
    Context manager for safe query execution with automatic connection closing.
    """
    con = get_connection(read_only=read_only)
    try:
        yield con
    finally:
        con.close()

def get_database_stats() -> dict:
    """
    Returns high-level statistics about the DuckDB storage for the active dataset.
    """
    db_p = get_db_path()
    if not db_p.exists():
        return {"status": "missing"}
    
    with get_db_cursor(read_only=True) as con:
        count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        min_ts, max_ts = con.execute("SELECT MIN(Timestamp), MAX(Timestamp) FROM transactions").fetchone()
        
    pq_path = _ACTIVE_DATASET.get("parquet_path")
    pq_exists = (pq_path and Path(pq_path).exists())
    pq_size = Path(pq_path).stat().st_size if pq_exists else 0

    return {
        "status": "ready",
        "dataset_id": _ACTIVE_DATASET.get("dataset_id", "default"),
        "dataset_name": _ACTIVE_DATASET.get("name", "Default Dataset"),
        "is_default": _ACTIVE_DATASET.get("is_default", False),
        "source_file": _ACTIVE_DATASET.get("source_file", ""),
        "row_count": count,
        "min_timestamp": str(min_ts),
        "max_timestamp": str(max_ts),
        "db_size_bytes": db_p.stat().st_size,
        "parquet_exists": pq_exists,
        "parquet_size_bytes": pq_size,
        "unsupported_indicators": _ACTIVE_DATASET.get("unsupported_indicators", []),
        "limitations": _ACTIVE_DATASET.get("limitations", [])
    }

