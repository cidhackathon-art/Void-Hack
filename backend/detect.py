"""
Operation Abhedya-Chakra -- Detection, Risk Score & Evidence Engine
Phase 2 - Step 2 (Fix): Refined deterministic scoring and evidence generation.

Strict Constraints:
- Read-only access to transactions data.
- NO hardcoded account-ID patterns or narration text.
- Deterministic 0-100 risk score based on config.yaml weights.
- Descriptive findings only; never use "confirmed fraud" or "accuracy".
  Always say "matches the measured pattern".
"""

import os
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
import yaml
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"

def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    path = config_path or CONFIG_PATH
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class DetectionEngine:
    def __init__(self, db_path: Optional[Union[str, Path]] = None, config: Optional[Dict[str, Any]] = None):
        from .db import get_db_path
        self.db_path = str(db_path or get_db_path())
        self.config = config or load_config()
        self.indicators_cfg = self.config.get("detection_indicators", {})

    def _get_connection(self, con: Optional[duckdb.DuckDBPyConnection] = None) -> duckdb.DuckDBPyConnection:
        if con is not None:
            return con
        return duckdb.connect(self.db_path, read_only=True)

    def score_account(self, account_id: str, con: Optional[duckdb.DuckDBPyConnection] = None, max_evidence_items: int = 10) -> Dict[str, Any]:
        """
        Calculates deterministic 0-100 risk score and evidence for a single account.
        Edge cases handled:
        - Non-existent/invalid account: returns score 0, exists=False.
        - Zero incoming / Zero outgoing: handles division by zero safely.
        - Sink account: correctly detects in_degree >= 1 and out_degree == 0.
        - Cycles and repeated Transaction_IDs: handled deterministically via row_id.
        """
        owns_conn = con is None
        conn = self._get_connection(con)
        try:
            # 1. Fetch account basic statistics
            stats_query = """
                WITH in_s AS (
                    SELECT 
                        count(*) as in_deg,
                        coalesce(sum(Amount), 0.0) as in_amt,
                        count(distinct Sender_Account) as in_senders
                    FROM transactions
                    WHERE Receiver_Account = ?
                ),
                out_s AS (
                    SELECT 
                        count(*) as out_deg,
                        coalesce(sum(Amount), 0.0) as out_amt,
                        count(distinct Receiver_Account) as out_recvs,
                        count(distinct IP_Address) as distinct_ips,
                        sum(CASE WHEN Device_Type IN ('Linux_Script', 'Web_Emulator') OR IP_Address LIKE '185.%' OR IP_Address LIKE '194.%' THEN 1 ELSE 0 END) as rare_infra_cnt
                    FROM transactions
                    WHERE Sender_Account = ?
                )
                SELECT 
                    in_s.in_deg, in_s.in_amt, in_s.in_senders,
                    out_s.out_deg, out_s.out_amt, out_s.out_recvs, out_s.distinct_ips,
                    out_s.rare_infra_cnt
                FROM in_s, out_s
            """
            row = conn.execute(stats_query, [account_id, account_id]).fetchone()

            in_deg, in_amt, in_senders, out_deg, out_amt, out_recvs, distinct_ips, rare_infra_cnt = row
            total_activity = in_deg + out_deg

            if total_activity == 0:
                return {
                    "account_id": account_id,
                    "account_exists": False,
                    "risk_score": 0,
                    "matched_indicators": [],
                    "evidence": [],
                    "evidence_summary": "Account has zero recorded transactions in dataset.",
                    "metrics": {
                        "in_degree": 0,
                        "out_degree": 0,
                        "in_amount": 0.0,
                        "out_amount": 0.0,
                        "out_in_ratio": None,
                        "largest_in_amount": None,
                        "forwarded_within_window_amount": None,
                        "forwarded_share": None
                    }
                }

            # 2. Check Fast Pass-Through on Largest Incoming Transaction
            fpt_cfg = self.indicators_cfg.get("fast_pass_through", {})
            window_s = fpt_cfg.get("window_seconds", 900)
            min_fwd_share = fpt_cfg.get("min_forward_share", 0.50)

            max_in_row = conn.execute("""
                SELECT row_id, Amount, Timestamp
                FROM transactions
                WHERE Receiver_Account = ?
                ORDER BY Amount DESC, Timestamp ASC
                LIMIT 1
            """, [account_id]).fetchone()

            largest_in_amt = float(max_in_row[1]) if max_in_row else 0.0
            forwarded_amt = 0.0
            forwarded_share = 0.0

            if max_in_row and out_deg > 0:
                max_ts = max_in_row[2]
                fwd_row = conn.execute(f"""
                    SELECT coalesce(sum(Amount), 0.0)
                    FROM transactions
                    WHERE Sender_Account = ?
                      AND Timestamp > ?
                      AND Timestamp <= ? + INTERVAL {window_s} SECOND
                """, [account_id, max_ts, max_ts]).fetchone()
                forwarded_amt = float(fwd_row[0]) if fwd_row else 0.0
                forwarded_share = (forwarded_amt / largest_in_amt) if largest_in_amt > 0 else 0.0

            # 3. Evaluate Indicators
            matched = []
            score = 0.0

            ratio = (out_amt / in_amt) if in_amt > 0 else None

            # Indicator A: Pass-Through Amount Ratio (Measured Envelope)
            pt_cfg = self.indicators_cfg.get("pass_through", {})
            if ratio is not None and pt_cfg.get("min_ratio", 0.9750) <= ratio <= pt_cfg.get("max_ratio", 0.9850):
                weight = pt_cfg.get("weight", 30)
                score += weight
                matched.append({
                    "indicator": "pass_through",
                    "contribution": weight,
                    "basis": f"Cumulative out/in amount ratio {ratio:.4f} matches the measured pattern envelope [{pt_cfg.get('min_ratio')}, {pt_cfg.get('max_ratio')}]"
                })

            # Indicator B: Fan-Out vs Fan-In
            fo_cfg = self.indicators_cfg.get("fan_out", {})
            if out_deg >= fo_cfg.get("min_out_degree", 3) and in_deg <= fo_cfg.get("max_in_degree", 8) and out_deg > in_deg:
                weight = fo_cfg.get("weight", 20)
                score += weight
                matched.append({
                    "indicator": "fan_out",
                    "contribution": weight,
                    "basis": f"Out-degree ({out_deg}) exceeds in-degree ({in_deg}); fans out to {out_recvs} distinct receivers"
                })

            # Indicator C: Fast Pass-Through on Largest Incoming
            if forwarded_share >= min_fwd_share:
                weight = fpt_cfg.get("weight", 20)
                score += weight
                matched.append({
                    "indicator": "fast_pass_through",
                    "contribution": weight,
                    "basis": f"Forwarded {forwarded_share*100:.1f}% (INR {forwarded_amt:,.2f}) of largest incoming txn within {window_s}s (>= {min_fwd_share*100:.0f}%)"
                })

            # Indicator D: Rare Infrastructure (Merged Device & IP)
            rare_cfg = self.indicators_cfg.get("rare_infrastructure", {})
            if rare_infra_cnt and rare_infra_cnt > 0:
                weight = rare_cfg.get("weight", 30)
                score += weight
                matched.append({
                    "indicator": "rare_infrastructure",
                    "contribution": weight,
                    "basis": f"Executed {rare_infra_cnt} transactions using measured rare devices (Linux_Script, Web_Emulator) or IP subnets (185.*, 194.*)"
                })

            # Indicator E: Terminal Sink Account
            sink_cfg = self.indicators_cfg.get("sink", {})
            if in_deg >= sink_cfg.get("min_in_degree", 1) and out_deg == sink_cfg.get("max_out_degree", 0):
                weight = sink_cfg.get("weight", 40)
                score += weight
                matched.append({
                    "indicator": "sink",
                    "contribution": weight,
                    "basis": f"Terminal sink account: {in_deg} incoming transactions, exactly 0 outgoing transactions across dataset"
                })

            # Deterministic Score (0 - 100)
            final_score = min(100, int(round(score)))

            # 4. Grouped Evidence per Matched Indicator
            top_n = self.config.get("evidence", {}).get("top_n_per_indicator", 5)
            evidence_by_indicator = {}

            # Populate evidence ONLY for indicators that actually matched
            for ind_item in matched:
                ind_name = ind_item["indicator"]

                if ind_name == "pass_through":
                    # All incoming and outgoing transactions contributing to cumulative ratio
                    pt_txns = conn.execute("""
                        SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp
                        FROM transactions
                        WHERE Sender_Account = ? OR Receiver_Account = ?
                        ORDER BY Amount DESC, Timestamp ASC
                    """, [account_id, account_id]).fetchall()
                    m_total = len(pt_txns)
                    shown_txns = []
                    for r_id, s_acc, r_acc, amt, ts in pt_txns[:top_n]:
                        reason = "pass_through: outflow contributing to cumulative ratio" if s_acc == account_id else "pass_through: inflow contributing to cumulative ratio"
                        shown_txns.append({
                            "row_id": r_id,
                            "sender": s_acc,
                            "receiver": r_acc,
                            "amount": float(amt),
                            "timestamp": str(ts),
                            "reason": reason
                        })
                    ev_obj = {
                        "indicator": "pass_through",
                        "total_contributing": m_total,
                        "showing": len(shown_txns),
                        "summary": f"showing {len(shown_txns)} of {m_total}",
                        "transactions": shown_txns
                    }
                    ind_item["evidence"] = ev_obj
                    evidence_by_indicator["pass_through"] = ev_obj

                elif ind_name == "fan_out":
                    # Outgoing transactions fanning out to distinct counterparties
                    fo_txns = conn.execute("""
                        SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp
                        FROM transactions
                        WHERE Sender_Account = ?
                        ORDER BY Amount DESC, Timestamp ASC
                    """, [account_id]).fetchall()
                    m_total = len(fo_txns)
                    shown_txns = []
                    for r_id, s_acc, r_acc, amt, ts in fo_txns[:top_n]:
                        shown_txns.append({
                            "row_id": r_id,
                            "sender": s_acc,
                            "receiver": r_acc,
                            "amount": float(amt),
                            "timestamp": str(ts),
                            "reason": f"fan_out: outgoing transaction to distinct counterparty {r_acc}"
                        })
                    ev_obj = {
                        "indicator": "fan_out",
                        "total_contributing": m_total,
                        "showing": len(shown_txns),
                        "summary": f"showing {len(shown_txns)} of {m_total}",
                        "transactions": shown_txns
                    }
                    ind_item["evidence"] = ev_obj
                    evidence_by_indicator["fan_out"] = ev_obj

                elif ind_name == "fast_pass_through":
                    # Largest incoming seed transaction PLUS outgoing transactions within window
                    max_ts = max_in_row[2]
                    fpt_outs = conn.execute(f"""
                        SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp
                        FROM transactions
                        WHERE Sender_Account = ?
                          AND Timestamp > ?
                          AND Timestamp <= ? + INTERVAL {window_s} SECOND
                        ORDER BY Amount DESC, Timestamp ASC
                    """, [account_id, max_ts, max_ts]).fetchall()

                    all_fpt = [
                        (max_in_row[0], conn.execute("SELECT Sender_Account FROM transactions WHERE row_id = ?", [max_in_row[0]]).fetchone()[0],
                         account_id, largest_in_amt, str(max_ts), "fast_pass_through: largest incoming transaction")
                    ]
                    for r_id, s_acc, r_acc, amt, ts in fpt_outs:
                        gap_s = (ts - max_ts).total_seconds() if hasattr(ts - max_ts, 'total_seconds') else 0
                        all_fpt.append((r_id, s_acc, r_acc, float(amt), str(ts), f"fast_pass_through: forwarded within {window_s}s window ({gap_s:.0f}s after largest in)"))

                    m_total = len(all_fpt)
                    shown_txns = []
                    for r_id, s_acc, r_acc, amt, ts_str, reason in all_fpt[:top_n]:
                        shown_txns.append({
                            "row_id": r_id,
                            "sender": s_acc,
                            "receiver": r_acc,
                            "amount": amt,
                            "timestamp": ts_str,
                            "reason": reason
                        })
                    ev_obj = {
                        "indicator": "fast_pass_through",
                        "total_contributing": m_total,
                        "showing": len(shown_txns),
                        "summary": f"showing {len(shown_txns)} of {m_total}",
                        "transactions": shown_txns
                    }
                    ind_item["evidence"] = ev_obj
                    evidence_by_indicator["fast_pass_through"] = ev_obj

                elif ind_name == "rare_infrastructure":
                    # Outgoing transactions with Linux_Script, Web_Emulator, or 185.*/194.* IP
                    rare_txns = conn.execute("""
                        SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp, Device_Type, IP_Address
                        FROM transactions
                        WHERE Sender_Account = ?
                          AND (Device_Type IN ('Linux_Script', 'Web_Emulator') OR IP_Address LIKE '185.%' OR IP_Address LIKE '194.%')
                        ORDER BY Amount DESC, Timestamp ASC
                    """, [account_id]).fetchall()
                    m_total = len(rare_txns)
                    shown_txns = []
                    for r_id, s_acc, r_acc, amt, ts, dev, ip in rare_txns[:top_n]:
                        shown_txns.append({
                            "row_id": r_id,
                            "sender": s_acc,
                            "receiver": r_acc,
                            "amount": float(amt),
                            "timestamp": str(ts),
                            "reason": f"rare_infrastructure: device={dev}, ip={ip}"
                        })
                    ev_obj = {
                        "indicator": "rare_infrastructure",
                        "total_contributing": m_total,
                        "showing": len(shown_txns),
                        "summary": f"showing {len(shown_txns)} of {m_total}",
                        "transactions": shown_txns
                    }
                    ind_item["evidence"] = ev_obj
                    evidence_by_indicator["rare_infrastructure"] = ev_obj

                elif ind_name == "sink":
                    # Terminal incoming transactions
                    sink_txns = conn.execute("""
                        SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp
                        FROM transactions
                        WHERE Receiver_Account = ?
                        ORDER BY Amount DESC, Timestamp ASC
                    """, [account_id]).fetchall()
                    m_total = len(sink_txns)
                    shown_txns = []
                    for r_id, s_acc, r_acc, amt, ts in sink_txns[:top_n]:
                        shown_txns.append({
                            "row_id": r_id,
                            "sender": s_acc,
                            "receiver": r_acc,
                            "amount": float(amt),
                            "timestamp": str(ts),
                            "reason": "sink: incoming transaction to terminal sink account (0 outgoing)"
                        })
                    ev_obj = {
                        "indicator": "sink",
                        "total_contributing": m_total,
                        "showing": len(shown_txns),
                        "summary": f"showing {len(shown_txns)} of {m_total}",
                        "transactions": shown_txns
                    }
                    ind_item["evidence"] = ev_obj
                    evidence_by_indicator["sink"] = ev_obj

            # Combined flat evidence list of all transactions supporting any matched indicator
            flat_evidence = []
            for ev_item in evidence_by_indicator.values():
                flat_evidence.extend(ev_item.get("transactions", []))

            return {
                "account_id": account_id,
                "account_exists": True,
                "risk_score": final_score,
                "matched_indicators": matched,
                "evidence": flat_evidence,
                "evidence_by_indicator": evidence_by_indicator,
                "metrics": {
                    "in_degree": in_deg,
                    "out_degree": out_deg,
                    "in_amount": round(float(in_amt), 2),
                    "out_amount": round(float(out_amt), 2),
                    "out_in_ratio": round(float(ratio), 4) if ratio is not None else None,
                    "largest_in_amount": round(largest_in_amt, 2),
                    "forwarded_within_window_amount": round(forwarded_amt, 2),
                    "forwarded_share": round(forwarded_share, 4)
                }
            }

        finally:
            if owns_conn:
                conn.close()

    def score_all_accounts_fast(self, con: Optional[duckdb.DuckDBPyConnection] = None) -> List[Dict[str, Any]]:
        """
        Computes detection indicators and deterministic risk scores across all 24,873 accounts
        in a single pass.
        """
        owns_conn = con is None
        conn = self._get_connection(con)
        try:
            pt_min = self.indicators_cfg.get("pass_through", {}).get("min_ratio", 0.9750)
            pt_max = self.indicators_cfg.get("pass_through", {}).get("max_ratio", 0.9850)
            fo_min_out = self.indicators_cfg.get("fan_out", {}).get("min_out_degree", 3)
            fo_max_in = self.indicators_cfg.get("fan_out", {}).get("max_in_degree", 8)
            fpt_window = self.indicators_cfg.get("fast_pass_through", {}).get("window_seconds", 900)
            min_fwd_share = self.indicators_cfg.get("fast_pass_through", {}).get("min_forward_share", 0.50)

            w_pt = self.indicators_cfg.get("pass_through", {}).get("weight", 30)
            w_fo = self.indicators_cfg.get("fan_out", {}).get("weight", 20)
            w_fpt = self.indicators_cfg.get("fast_pass_through", {}).get("weight", 20)
            w_rare = self.indicators_cfg.get("rare_infrastructure", {}).get("weight", 30)
            w_sink = self.indicators_cfg.get("sink", {}).get("weight", 40)

            query = f"""
                WITH all_accounts AS (
                    SELECT Sender_Account as acc FROM transactions
                    UNION
                    SELECT Receiver_Account as acc FROM transactions
                ),
                in_stats AS (
                    SELECT 
                        Receiver_Account as acc,
                        count(*) as in_deg,
                        sum(Amount) as in_amt
                    FROM transactions
                    GROUP BY Receiver_Account
                ),
                out_stats AS (
                    SELECT 
                        Sender_Account as acc,
                        count(*) as out_deg,
                        sum(Amount) as out_amt,
                        count(distinct Receiver_Account) as distinct_recvs,
                        sum(CASE WHEN Device_Type IN ('Linux_Script', 'Web_Emulator') OR IP_Address LIKE '185.%' OR IP_Address LIKE '194.%' THEN 1 ELSE 0 END) as rare_infra_cnt
                    FROM transactions
                    GROUP BY Sender_Account
                ),
                max_in AS (
                    SELECT Receiver_Account as acc, Amount, Timestamp,
                           row_number() over (partition by Receiver_Account order by Amount desc, Timestamp asc) as rn
                    FROM transactions
                ),
                largest_in AS (
                    SELECT acc, Amount as in_amt, Timestamp as in_ts
                    FROM max_in WHERE rn = 1
                ),
                window_outs AS (
                    SELECT 
                        lin.acc,
                        lin.in_amt,
                        sum(tout.Amount) as sum_out
                    FROM largest_in lin
                    JOIN transactions tout
                      ON lin.acc = tout.Sender_Account
                     AND tout.Timestamp > lin.in_ts
                     AND tout.Timestamp <= lin.in_ts + INTERVAL {fpt_window} SECOND
                    GROUP BY lin.acc, lin.in_amt
                )
                SELECT 
                    a.acc,
                    coalesce(i.in_deg, 0) as in_deg,
                    coalesce(o.out_deg, 0) as out_deg,
                    coalesce(i.in_amt, 0.0) as in_amt,
                    coalesce(o.out_amt, 0.0) as out_amt,
                    coalesce(o.out_amt, 0.0) / nullif(coalesce(i.in_amt, 0.0), 0) as ratio,
                    lin.in_amt as largest_in_amt,
                    coalesce(wo.sum_out, 0.0) as sum_fwd_amt,
                    -- Indicators
                    CASE WHEN coalesce(o.out_amt, 0.0) / nullif(coalesce(i.in_amt, 0.0), 0) BETWEEN {pt_min} AND {pt_max} THEN 1 ELSE 0 END as ind_pt,
                    CASE WHEN coalesce(o.out_deg, 0) >= {fo_min_out} AND coalesce(i.in_deg, 0) <= {fo_max_in} AND coalesce(o.out_deg, 0) > coalesce(i.in_deg, 0) THEN 1 ELSE 0 END as ind_fo,
                    CASE WHEN wo.sum_out IS NOT NULL AND wo.sum_out >= {min_fwd_share} * lin.in_amt THEN 1 ELSE 0 END as ind_fpt,
                    CASE WHEN coalesce(o.rare_infra_cnt, 0) > 0 THEN 1 ELSE 0 END as ind_rare,
                    CASE WHEN coalesce(i.in_deg, 0) >= 1 AND coalesce(o.out_deg, 0) == 0 THEN 1 ELSE 0 END as ind_sink
                FROM all_accounts a
                LEFT JOIN in_stats i ON a.acc = i.acc
                LEFT JOIN out_stats o ON a.acc = o.acc
                LEFT JOIN largest_in lin ON a.acc = lin.acc
                LEFT JOIN window_outs wo ON a.acc = wo.acc
            """
            rows = conn.execute(query).fetchall()

            results = []
            for r in rows:
                acc, in_deg, out_deg, in_amt, out_amt, ratio, lin_amt, fwd_amt, ind_pt, ind_fo, ind_fpt, ind_rare, ind_sink = r

                score = (
                    ind_pt * w_pt +
                    ind_fo * w_fo +
                    ind_fpt * w_fpt +
                    ind_rare * w_rare +
                    ind_sink * w_sink
                )
                final_score = min(100, int(round(score)))

                results.append({
                    "account_id": acc,
                    "risk_score": final_score,
                    "in_deg": in_deg,
                    "out_deg": out_deg,
                    "ratio": round(float(ratio), 4) if ratio is not None else None,
                    "largest_in_amt": round(float(lin_amt), 2) if lin_amt is not None else None,
                    "sum_fwd_amt": round(float(fwd_amt), 2) if fwd_amt is not None else None,
                    "ind_pt": ind_pt,
                    "ind_fo": ind_fo,
                    "ind_fpt": ind_fpt,
                    "ind_rare": ind_rare,
                    "ind_sink": ind_sink
                })

            return results
        finally:
            if owns_conn:
                conn.close()
