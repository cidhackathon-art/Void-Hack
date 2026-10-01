import os
import duckdb
import yaml
from pathlib import Path
from contextlib import contextmanager

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

config = load_config()
DB_RELATIVE_PATH = config.get("paths", {}).get("duckdb_database", "data/transactions.duckdb")
DB_PATH = PROJECT_ROOT / DB_RELATIVE_PATH
PARQUET_RELATIVE_PATH = config.get("paths", {}).get("parquet_file", "data/transactions.parquet")
PARQUET_PATH = PROJECT_ROOT / PARQUET_RELATIVE_PATH

def get_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """
    Returns a DuckDB connection.
    Defaults to read_only=True for concurrent web server access.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {DB_PATH}. "
            "Please run data ingestion first."
        )
    return duckdb.connect(str(DB_PATH), read_only=read_only)

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
    Returns high-level statistics about the DuckDB storage.
    """
    if not DB_PATH.exists():
        return {"status": "missing"}
    
    with get_db_cursor(read_only=True) as con:
        count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        min_ts, max_ts = con.execute("SELECT MIN(Timestamp), MAX(Timestamp) FROM transactions").fetchone()
        
    return {
        "status": "ready",
        "row_count": count,
        "min_timestamp": str(min_ts),
        "max_timestamp": str(max_ts),
        "db_size_bytes": DB_PATH.stat().st_size,
        "parquet_exists": PARQUET_PATH.exists(),
        "parquet_size_bytes": PARQUET_PATH.stat().st_size if PARQUET_PATH.exists() else 0
    }
