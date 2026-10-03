"""
Operation Abhedya-Chakra -- Evidence Context Builder
Phase 3 - Step 1: Self-contained context generator for explainability and downstream reporting.

Strict Constraints:
- Read-only access to DuckDB.
- Imports backend/detect.py without modifying it.
- Consumes detect.py evidence AS-IS.
- No LLM calls.
"""

from pathlib import Path
from typing import Optional, Dict, Any, List, Union
import duckdb
from backend.detect import DetectionEngine, load_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"

LIMITATIONS = [
    "No ground-truth labels exist in dataset; all findings represent observed behavioral patterns only.",
    "Possible flow is not proven movement of physical funds.",
    "Unconfigured indicators were excluded from scoring.",
    "All cutoffs and indicator weights represent documented design choices."
]

def build_context(
    account_id: str,
    con: Optional[duckdb.DuckDBPyConnection] = None,
    db_path: Optional[Union[str, Path]] = None,
    config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Constructs a complete, self-contained JSON context object for an account.
    Consumes detect.py detection results and adds deterministic SQL observed paths.
    """
    cfg = config or load_config()
    from .db import get_db_path
    db_p = str(db_path or get_db_path())
    engine = DetectionEngine(db_path=db_p, config=cfg)

    owns_conn = con is None
    conn = con if con is not None else duckdb.connect(db_p, read_only=True)

    try:
        # 1. Consume detection output AS-IS from detect.py
        detect_res = engine.score_account(account_id, con=conn)

        if not detect_res["account_exists"]:
            return {
                "account_id": account_id,
                "account_exists": False,
                "risk_score": 0,
                "account_summary": {
                    "in_degree": 0,
                    "out_degree": 0,
                    "total_incoming_amount": 0.0,
                    "total_outgoing_amount": 0.0,
                    "out_in_ratio": None,
                    "largest_in_amount": 0.0
                },
                "matched_indicators": [],
                "evidence_by_indicator": {},
                "observed_paths": [],
                "device_ip_summary": {
                    "outgoing_devices": [],
                    "incoming_devices": [],
                    "distinct_ips": 0,
                    "sample_ips": []
                },
                "limitations": LIMITATIONS
            }

        # 2. Extract Account Summary
        metrics = detect_res["metrics"]
        account_summary = {
            "in_degree": metrics["in_degree"],
            "out_degree": metrics["out_degree"],
            "total_incoming_amount": metrics["in_amount"],
            "total_outgoing_amount": metrics["out_amount"],
            "out_in_ratio": metrics["out_in_ratio"],
            "largest_in_amount": metrics["largest_in_amount"],
            "forwarded_within_window_amount": metrics["forwarded_within_window_amount"],
            "forwarded_share": metrics["forwarded_share"]
        }

        # 3. Deterministic Observed Paths (Labeled exactly "observed path")
        # Finds concrete onward paths passing through this account
        observed_paths = []
        if metrics["in_degree"] > 0 and metrics["out_degree"] > 0:
            path_rows = conn.execute("""
                SELECT 
                    tin.row_id as in_row_id,
                    tout.row_id as out_row_id,
                    tin.Sender_Account as source_account,
                    tin.Receiver_Account as pivot_account,
                    tout.Receiver_Account as destination_account,
                    tin.Amount as in_amount,
                    tout.Amount as out_amount,
                    tout.Amount / nullif(tin.Amount, 0) as amount_ratio,
                    tin.Timestamp as in_timestamp,
                    tout.Timestamp as out_timestamp,
                    epoch(tout.Timestamp) - epoch(tin.Timestamp) as time_gap_seconds
                FROM transactions tin
                ASOF JOIN transactions tout
                  ON tin.Receiver_Account = tout.Sender_Account
                 AND tin.Timestamp < tout.Timestamp
                WHERE tin.Receiver_Account = ?
                ORDER BY tout.Amount DESC, tout.Timestamp ASC
                LIMIT 5
            """, [account_id]).fetchall()

            for pr in path_rows:
                observed_paths.append({
                    "label": "observed path",
                    "in_row_id": pr[0],
                    "out_row_id": pr[1],
                    "source_account": pr[2],
                    "pivot_account": pr[3],
                    "destination_account": pr[4],
                    "in_amount": float(pr[5]),
                    "out_amount": float(pr[6]),
                    "amount_ratio": round(float(pr[7]), 4) if pr[7] is not None else None,
                    "in_timestamp": str(pr[8]),
                    "out_timestamp": str(pr[9]),
                    "time_gap_seconds": float(pr[10])
                })

        # 4. Device and IP Summary
        dev_out = [r[0] for r in conn.execute("SELECT distinct Device_Type FROM transactions WHERE Sender_Account = ?", [account_id]).fetchall()]
        dev_in = [r[0] for r in conn.execute("SELECT distinct Device_Type FROM transactions WHERE Receiver_Account = ?", [account_id]).fetchall()]
        ips = [r[0] for r in conn.execute("SELECT distinct IP_Address FROM transactions WHERE Sender_Account = ? LIMIT 5", [account_id]).fetchall()]
        total_ips_cnt = conn.execute("SELECT count(distinct IP_Address) FROM transactions WHERE Sender_Account = ?", [account_id]).fetchone()[0]

        device_ip_summary = {
            "outgoing_devices": dev_out,
            "incoming_devices": dev_in,
            "distinct_ips": total_ips_cnt,
            "sample_ips": ips
        }

        # 5. Return one self-contained JSON context object
        return {
            "account_id": account_id,
            "account_exists": True,
            "risk_score": detect_res["risk_score"],
            "account_summary": account_summary,
            "matched_indicators": detect_res["matched_indicators"],
            "evidence_by_indicator": detect_res["evidence_by_indicator"],
            "observed_paths": observed_paths,
            "device_ip_summary": device_ip_summary,
            "limitations": LIMITATIONS
        }

    finally:
        if owns_conn:
            conn.close()
