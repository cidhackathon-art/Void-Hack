from typing import Optional, List, Dict, Any
from .db import get_db_cursor

COLUMNS = [
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

def _row_to_dict(row: tuple) -> Dict[str, Any]:
    return {
        "row_id": int(row[0]),
        "transaction_id": row[1],
        "sender_account": row[2],
        "receiver_account": row[3],
        "sender_ifsc": row[4],
        "receiver_ifsc": row[5],
        "amount": float(row[6]) if row[6] is not None else 0.0,
        "timestamp": str(row[7]),
        "payment_mode": row[8],
        "narration": row[9],
        "ip_address": row[10],
        "device_type": row[11]
    }

def account_exists(account_id: str) -> bool:
    """
    Checks if an account ID exists as either sender or receiver.
    """
    with get_db_cursor(read_only=True) as con:
        query = """
            SELECT 1 FROM transactions 
            WHERE Sender_Account = ? OR Receiver_Account = ? 
            LIMIT 1
        """
        row = con.execute(query, [account_id, account_id]).fetchone()
        return row is not None

def get_account_summary(account_id: str) -> Optional[Dict[str, Any]]:
    """
    Returns high-level statistics for an account.
    """
    if not account_exists(account_id):
        return None

    with get_db_cursor(read_only=True) as con:
        # Outgoing aggregate
        out_query = """
            SELECT 
                COUNT(*) AS out_count,
                COALESCE(SUM(Amount), 0.0) AS out_total,
                MIN(Timestamp) AS first_out,
                MAX(Timestamp) AS last_out,
                COUNT(DISTINCT Receiver_Account) AS distinct_beneficiaries
            FROM transactions 
            WHERE Sender_Account = ?
        """
        out_row = con.execute(out_query, [account_id]).fetchone()

        # Incoming aggregate
        in_query = """
            SELECT 
                COUNT(*) AS in_count,
                COALESCE(SUM(Amount), 0.0) AS in_total,
                MIN(Timestamp) AS first_in,
                MAX(Timestamp) AS last_in,
                COUNT(DISTINCT Sender_Account) AS distinct_remitters
            FROM transactions 
            WHERE Receiver_Account = ?
        """
        in_row = con.execute(in_query, [account_id]).fetchone()

        # Distinct IFSC codes
        ifsc_query = """
            SELECT DISTINCT ifsc FROM (
                SELECT Sender_IFSC AS ifsc FROM transactions WHERE Sender_Account = ?
                UNION
                SELECT Receiver_IFSC AS ifsc FROM transactions WHERE Receiver_Account = ?
            ) WHERE ifsc IS NOT NULL
        """
        ifscs = [r[0] for r in con.execute(ifsc_query, [account_id, account_id]).fetchall()]

    return {
        "account_id": account_id,
        "ifsc_codes": ifscs,
        "incoming": {
            "count": in_row[0],
            "total_amount": float(in_row[1]),
            "first_seen": str(in_row[2]) if in_row[2] else None,
            "last_seen": str(in_row[3]) if in_row[3] else None,
            "distinct_senders": in_row[4]
        },
        "outgoing": {
            "count": out_row[0],
            "total_amount": float(out_row[1]),
            "first_seen": str(out_row[2]) if out_row[2] else None,
            "last_seen": str(out_row[3]) if out_row[3] else None,
            "distinct_receivers": out_row[4]
        },
        "net_flow": float(in_row[1] - out_row[1])
    }

def get_incoming_transactions(
    account_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Retrieves transactions where account is the receiver, ordered chronologically.
    """
    with get_db_cursor(read_only=True) as con:
        conditions = ["Receiver_Account = ?"]
        params = [account_id]

        if start_time:
            conditions.append("Timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("Timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT {', '.join(COLUMNS)} 
            FROM transactions 
            WHERE {where_clause}
            ORDER BY Timestamp ASC, row_id ASC
            LIMIT ?
        """
        params.append(limit)
        rows = con.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]

def get_outgoing_transactions(
    account_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Retrieves transactions where account is the sender, ordered chronologically.
    """
    with get_db_cursor(read_only=True) as con:
        conditions = ["Sender_Account = ?"]
        params = [account_id]

        if start_time:
            conditions.append("Timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("Timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT {', '.join(COLUMNS)} 
            FROM transactions 
            WHERE {where_clause}
            ORDER BY Timestamp ASC, row_id ASC
            LIMIT ?
        """
        params.append(limit)
        rows = con.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]

def get_account_transactions(
    account_id: str,
    direction: str = "all",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 200
) -> List[Dict[str, Any]]:
    """
    Fetches incoming, outgoing, or bidirectional transactions for an account.
    direction options: 'in', 'out', 'all'
    """
    if direction.lower() == "in":
        return get_incoming_transactions(account_id, start_time, end_time, limit)
    elif direction.lower() == "out":
        return get_outgoing_transactions(account_id, start_time, end_time, limit)
    
    with get_db_cursor(read_only=True) as con:
        conditions = ["(Sender_Account = ? OR Receiver_Account = ?)"]
        params = [account_id, account_id]

        if start_time:
            conditions.append("Timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("Timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT {', '.join(COLUMNS)} 
            FROM transactions 
            WHERE {where_clause}
            ORDER BY Timestamp ASC, row_id ASC
            LIMIT ?
        """
        params.append(limit)
        rows = con.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]

def get_transaction_by_row_id(row_id: int) -> Optional[Dict[str, Any]]:
    """
    Uniquely identifies and retrieves a transaction by its permanent stable row_id.
    """
    with get_db_cursor(read_only=True) as con:
        query = f"""
            SELECT {', '.join(COLUMNS)} 
            FROM transactions 
            WHERE row_id = ?
            LIMIT 1
        """
        row = con.execute(query, [row_id]).fetchone()
        return _row_to_dict(row) if row else None

def get_transactions_by_txn_id(transaction_id: str) -> List[Dict[str, Any]]:
    """
    Retrieves transactions by Transaction_ID.
    Returns a list because Transaction_IDs may repeat (2,252 non-unique instances in dataset).
    """
    with get_db_cursor(read_only=True) as con:
        query = f"""
            SELECT {', '.join(COLUMNS)} 
            FROM transactions 
            WHERE Transaction_ID = ?
            ORDER BY Timestamp ASC, row_id ASC
        """
        rows = con.execute(query, [transaction_id]).fetchall()
        return [_row_to_dict(r) for r in rows]

_SCORED_ACCOUNTS_CACHE = None

def _get_score_lookup():
    global _SCORED_ACCOUNTS_CACHE
    if _SCORED_ACCOUNTS_CACHE is None:
        from .detect import DetectionEngine
        engine = DetectionEngine()
        all_scores = engine.score_all_accounts_fast()
        _SCORED_ACCOUNTS_CACHE = {a["account_id"]: a for a in all_scores}
    return _SCORED_ACCOUNTS_CACHE

def get_connected_accounts(account_id: str, limit: int = 15) -> Dict[str, Any]:
    """
    Retrieves top incoming senders and outgoing receivers for a target account,
    along with total connected accounts count and representative transaction details.
    """
    with get_db_cursor(read_only=True) as con:
        txns = con.execute("""
            SELECT 
                row_id,
                Sender_Account,
                Receiver_Account,
                Amount,
                Timestamp,
                Payment_Mode
            FROM transactions
            WHERE Sender_Account = ? OR Receiver_Account = ?
            ORDER BY Timestamp ASC, row_id ASC
        """, [account_id, account_id]).fetchall()

    in_txns = [r for r in txns if r[2] == account_id]
    out_txns = [r for r in txns if r[1] == account_id]

    # Map time gap to preceding incoming transaction for each outgoing transaction
    out_time_gaps = {}
    for ot in out_txns:
        prior_in = [it for it in in_txns if it[4] < ot[4]]
        if prior_in:
            out_time_gaps[ot[0]] = int((ot[4] - prior_in[-1][4]).total_seconds())

    # Group incoming by Sender_Account
    in_grouped = {}
    for r in in_txns:
        in_grouped.setdefault(r[1], []).append(r)

    # Group outgoing by Receiver_Account
    out_grouped = {}
    for r in out_txns:
        out_grouped.setdefault(r[2], []).append(r)

    in_list = []
    for cp_id, t_list in in_grouped.items():
        rep = max(t_list, key=lambda x: x[3])
        tot = round(sum(x[3] for x in t_list), 2)
        in_list.append({
            "account_id": cp_id,
            "bank_prefix": cp_id[:4],
            "direction": "incoming",
            "txn_count": len(t_list),
            "total_amount": tot,
            "first_seen": str(min(x[4] for x in t_list)),
            "last_seen": str(max(x[4] for x in t_list)),
            "sample_row_id": rep[0],
            "sample_amount": round(rep[3], 2),
            "sample_timestamp": str(rep[4]),
            "sample_payment_mode": rep[5],
            "time_gap_seconds": None
        })
    in_list.sort(key=lambda x: -x["total_amount"])

    out_list = []
    for cp_id, t_list in out_grouped.items():
        rep = max(t_list, key=lambda x: x[3])
        tot = round(sum(x[3] for x in t_list), 2)
        out_list.append({
            "account_id": cp_id,
            "bank_prefix": cp_id[:4],
            "direction": "outgoing",
            "txn_count": len(t_list),
            "total_amount": tot,
            "first_seen": str(min(x[4] for x in t_list)),
            "last_seen": str(max(x[4] for x in t_list)),
            "sample_row_id": rep[0],
            "sample_amount": round(rep[3], 2),
            "sample_timestamp": str(rep[4]),
            "sample_payment_mode": rep[5],
            "time_gap_seconds": out_time_gaps.get(rep[0])
        })
    out_list.sort(key=lambda x: -x["total_amount"])

    return {
        "account_id": account_id,
        "total_incoming_accounts": len(in_grouped),
        "total_outgoing_accounts": len(out_grouped),
        "total_connected_accounts": len(in_grouped) + len(out_grouped),
        "incoming": in_list[:limit],
        "outgoing": out_list[:limit],
        "incoming_connected": in_list[:limit],
        "outgoing_connected": out_list[:limit]
    }


def get_connected_summary(account_id: str, display_limit: int = 10) -> Dict[str, Any]:
    """
    Builds a complete Connected Accounts Summary from SQL over the transaction table.
    Summary counts always reflect the COMPLETE SQL result.
    The displayed neighbor list is capped at display_limit, ordered by total transaction amount.
    Risk classifications come from the existing deterministic detection engine only.
    """
    with get_db_cursor(read_only=True) as con:
        txns = con.execute("""
            SELECT 
                row_id,
                Sender_Account,
                Receiver_Account,
                Amount,
                Timestamp,
                Payment_Mode
            FROM transactions
            WHERE Sender_Account = ? OR Receiver_Account = ?
            ORDER BY Timestamp ASC, row_id ASC
        """, [account_id, account_id]).fetchall()

    in_txns = [r for r in txns if r[2] == account_id]
    out_txns = [r for r in txns if r[1] == account_id]

    # Calculate time gaps for outgoing transactions that follow an incoming transaction
    out_time_gaps = {}
    for ot in out_txns:
        prior_in = [it for it in in_txns if it[4] < ot[4]]
        if prior_in:
            out_time_gaps[ot[0]] = int((ot[4] - prior_in[-1][4]).total_seconds())

    # Group incoming by Sender_Account
    in_grouped = {}
    for r in in_txns:
        in_grouped.setdefault(r[1], []).append(r)

    # Group outgoing by Receiver_Account
    out_grouped = {}
    for r in out_txns:
        out_grouped.setdefault(r[2], []).append(r)

    total_incoming_accounts = len(in_grouped)
    total_outgoing_accounts = len(out_grouped)
    all_counterparties = set(in_grouped.keys()) | set(out_grouped.keys())
    distinct_connected = len(all_counterparties)
    total_observed_transactions = len(txns)

    total_incoming_inr = round(sum(r[3] for r in in_txns), 2)
    total_outgoing_inr = round(sum(r[3] for r in out_txns), 2)

    score_lookup = _get_score_lookup()

    flagged_accounts = []
    clean_accounts = []
    tier_counts = {"Layer 1": 0, "Layer 2": 0, "Sinks": 0}
    flagged_in_inr = 0.0
    flagged_out_inr = 0.0
    clean_in_inr = 0.0
    clean_out_inr = 0.0

    in_amt_map = {cp: sum(r[3] for r in t_list) for cp, t_list in in_grouped.items()}
    out_amt_map = {cp: sum(r[3] for r in t_list) for cp, t_list in out_grouped.items()}

    for cp_id in all_counterparties:
        cp_score_data = score_lookup.get(cp_id)
        risk_score = cp_score_data["risk_score"] if cp_score_data else 0
        cp_in_amt = in_amt_map.get(cp_id, 0.0)
        cp_out_amt = out_amt_map.get(cp_id, 0.0)

        if risk_score >= 30:
            flagged_accounts.append(cp_id)
            flagged_in_inr += cp_in_amt
            flagged_out_inr += cp_out_amt
            if cp_score_data:
                ind_sink = cp_score_data.get("ind_sink", 0)
                if risk_score == 100:
                    tier_counts["Layer 1"] += 1
                elif ind_sink or risk_score == 40:
                    tier_counts["Sinks"] += 1
                else:
                    tier_counts["Layer 2"] += 1
        else:
            clean_accounts.append(cp_id)
            clean_in_inr += cp_in_amt
            clean_out_inr += cp_out_amt

    # Build neighbor list
    all_neighbors = []
    for cp_id, t_list in in_grouped.items():
        rep = max(t_list, key=lambda x: x[3])
        cp_score_data = score_lookup.get(cp_id, {})
        cp_score = cp_score_data.get("risk_score", 0)
        tot_amt = sum(x[3] for x in t_list)
        if cp_score == 100:
            classification = "Layer 1 Transit Core"
        elif cp_score_data.get("ind_sink") or cp_score == 40:
            classification = "Terminal Cash-Out Sink"
        elif cp_score >= 30:
            classification = "Layer 2 Dispersal & Routing"
        else:
            classification = "Clean Normal Baseline"

        all_neighbors.append({
            "account_id": cp_id,
            "bank_prefix": cp_id[:4],
            "direction": "incoming",
            "txn_count": len(t_list),
            "total_amount": round(tot_amt, 2),
            "first_seen": str(min(x[4] for x in t_list)),
            "last_seen": str(max(x[4] for x in t_list)),
            "risk_score": cp_score,
            "classification": classification,
            "is_flagged": cp_score >= 30,
            "row_id": rep[0],
            "amount": round(rep[3], 2),
            "timestamp": str(rep[4]),
            "payment_mode": rep[5],
            "time_gap_seconds": None
        })

    for cp_id, t_list in out_grouped.items():
        rep = max(t_list, key=lambda x: x[3])
        cp_score_data = score_lookup.get(cp_id, {})
        cp_score = cp_score_data.get("risk_score", 0)
        tot_amt = sum(x[3] for x in t_list)
        if cp_score == 100:
            classification = "Layer 1 Transit Core"
        elif cp_score_data.get("ind_sink") or cp_score == 40:
            classification = "Terminal Cash-Out Sink"
        elif cp_score >= 30:
            classification = "Layer 2 Dispersal & Routing"
        else:
            classification = "Clean Normal Baseline"

        all_neighbors.append({
            "account_id": cp_id,
            "bank_prefix": cp_id[:4],
            "direction": "outgoing",
            "txn_count": len(t_list),
            "total_amount": round(tot_amt, 2),
            "first_seen": str(min(x[4] for x in t_list)),
            "last_seen": str(max(x[4] for x in t_list)),
            "risk_score": cp_score,
            "classification": classification,
            "is_flagged": cp_score >= 30,
            "row_id": rep[0],
            "amount": round(rep[3], 2),
            "timestamp": str(rep[4]),
            "payment_mode": rep[5],
            "time_gap_seconds": out_time_gaps.get(rep[0])
        })

    all_neighbors.sort(key=lambda x: -x["total_amount"])
    displayed_neighbors = all_neighbors[:display_limit]

    return {
        "account_id": account_id,
        "summary": {
            "distinct_connected_accounts": distinct_connected,
            "incoming_connected_accounts": total_incoming_accounts,
            "outgoing_connected_accounts": total_outgoing_accounts,
            "total_observed_transactions": total_observed_transactions,
            "connected_flagged_count": len(flagged_accounts),
            "connected_clean_count": len(clean_accounts),
            "flagged_tier_breakdown": tier_counts,
            "flagged_incoming_inr": round(flagged_in_inr, 2),
            "flagged_outgoing_inr": round(flagged_out_inr, 2),
            "clean_incoming_inr": round(clean_in_inr, 2),
            "clean_outgoing_inr": round(clean_out_inr, 2),
            "total_incoming_inr": total_incoming_inr,
            "total_outgoing_inr": total_outgoing_inr
        },
        "display_limit": display_limit,
        "total_neighbors": len(all_neighbors),
        "displayed_count": len(displayed_neighbors),
        "neighbors": displayed_neighbors
    }
