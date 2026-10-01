import os
from pathlib import Path
from typing import Dict, Any
import duckdb
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def run_validation(csv_path: str = None, db_path: str = None) -> Dict[str, Any]:
    """
    Validates dataset integrity:
    1. Source file exists
    2. Required columns exist
    3. Row count verification between CSV and DuckDB (exact match)
    4. Duplicate Transaction_ID count
    5. Stable row_id verification (1 to N, strictly unique)
    6. Null/malformed timestamp detection
    7. Malformed/negative amount detection
    """
    config_path = PROJECT_ROOT / "config.yaml"
    expected_cols = [
        "Transaction_ID", "Sender_Account", "Receiver_Account",
        "Sender_IFSC", "Receiver_IFSC", "Amount", "Timestamp",
        "Payment_Mode", "Narration", "IP_Address", "Device_Type"
    ]
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            expected_cols = cfg.get("dataset_meta", {}).get("expected_columns", expected_cols)

    if not csv_path:
        csv_path = str(PROJECT_ROOT / "VoidHacks8_MuleAccount_2M_Transactions.csv")
    if not db_path:
        db_path = str(PROJECT_ROOT / "data" / "transactions.duckdb")

    report = {
        "source_file_exists": os.path.exists(csv_path),
        "source_file_path": csv_path,
        "source_file_size_bytes": os.path.getsize(csv_path) if os.path.exists(csv_path) else 0,
        "db_file_exists": os.path.exists(db_path),
        "db_file_path": db_path,
        "db_file_size_bytes": os.path.getsize(db_path) if os.path.exists(db_path) else 0,
        "column_check": {},
        "row_counts": {},
        "row_id_verification": {},
        "quality_metrics": {},
        "status": "PASS"
    }

    if not report["source_file_exists"]:
        report["status"] = "FAIL"
        report["error"] = f"Source CSV not found at {csv_path}"
        return report

    if not report["db_file_exists"]:
        report["status"] = "FAIL"
        report["error"] = f"DuckDB database not found at {db_path}"
        return report

    # 1. Compare actual CSV rows vs DB rows directly
    con_csv = duckdb.connect()
    csv_rows = con_csv.execute(f"SELECT COUNT(*) FROM read_csv_auto('{csv_path}')").fetchone()[0]
    con_csv.close()

    db_con = duckdb.connect(db_path, read_only=True)
    db_rows = db_con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    report["row_counts"] = {
        "csv_rows": csv_rows,
        "db_rows": db_rows,
        "counts_match": (csv_rows == db_rows)
    }
    if csv_rows != db_rows:
        report["status"] = "FAIL"

    # 2. Column check on DB table
    actual_cols = [c[0] for c in db_con.execute("DESCRIBE SELECT * FROM transactions").fetchall()]
    missing_cols = [c for c in expected_cols if c not in actual_cols]
    report["column_check"] = {
        "expected_columns": expected_cols,
        "actual_columns": actual_cols,
        "has_row_id": "row_id" in actual_cols,
        "missing_columns": missing_cols,
        "all_required_present": len(missing_cols) == 0
    }
    if missing_cols or "row_id" not in actual_cols:
        report["status"] = "FAIL"

    # 3. Stable row_id verification
    rid_stats = db_con.execute("""
        SELECT 
            COUNT(DISTINCT row_id) AS distinct_rids,
            MIN(row_id) AS min_rid,
            MAX(row_id) AS max_rid,
            COUNT(CASE WHEN row_id IS NULL THEN 1 END) AS null_rids
        FROM transactions
    """).fetchone()

    report["row_id_verification"] = {
        "distinct_row_ids": rid_stats[0],
        "min_row_id": rid_stats[1],
        "max_row_id": rid_stats[2],
        "null_row_ids": rid_stats[3],
        "is_strictly_dense_and_unique": (rid_stats[0] == db_rows and rid_stats[1] == 1 and rid_stats[2] == db_rows and rid_stats[3] == 0)
    }
    if not report["row_id_verification"]["is_strictly_dense_and_unique"]:
        report["status"] = "FAIL"

    # 4. Quality Metrics on the records
    q_query = """
    SELECT
        COUNT(*) - COUNT(DISTINCT Transaction_ID) AS duplicate_txns,
        COUNT(CASE WHEN Transaction_ID IS NULL THEN 1 END) AS null_txnid,
        COUNT(CASE WHEN Sender_Account IS NULL THEN 1 END) AS null_sender,
        COUNT(CASE WHEN Receiver_Account IS NULL THEN 1 END) AS null_receiver,
        COUNT(CASE WHEN Amount IS NULL THEN 1 END) AS null_amount,
        COUNT(CASE WHEN Amount <= 0 THEN 1 END) AS non_positive_amount,
        COUNT(CASE WHEN Timestamp IS NULL THEN 1 END) AS null_timestamp,
        MIN(Amount) AS min_amount,
        MAX(Amount) AS max_amount,
        MIN(Timestamp) AS min_timestamp,
        MAX(Timestamp) AS max_timestamp
    FROM transactions
    """
    row = db_con.execute(q_query).fetchone()

    report["quality_metrics"] = {
        "duplicate_transaction_ids": row[0],
        "null_transaction_ids": row[1],
        "null_sender_accounts": row[2],
        "null_receiver_accounts": row[3],
        "null_amounts": row[4],
        "non_positive_amounts": row[5],
        "null_timestamps": row[6],
        "min_amount": float(row[7]),
        "max_amount": float(row[8]),
        "min_timestamp": str(row[9]),
        "max_timestamp": str(row[10])
    }

    db_con.close()
    return report

if __name__ == "__main__":
    import json
    res = run_validation()
    print(json.dumps(res, indent=2))
