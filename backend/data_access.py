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

def get_connected_accounts(account_id: str, limit: int = 15) -> Dict[str, Any]:
    """
    Retrieves top incoming senders and outgoing receivers for a target account,
    along with total connected accounts count.
    """
    with get_db_cursor(read_only=True) as con:
        in_rows = con.execute("""
            SELECT 
                Sender_Account as counterparty,
                count(*) as txn_count,
                round(sum(Amount), 2) as total_amount,
                min(Timestamp) as first_seen,
                max(Timestamp) as last_seen
            FROM transactions
            WHERE Receiver_Account = ?
            GROUP BY Sender_Account
            ORDER BY sum(Amount) DESC
        """, [account_id]).fetchall()

        out_rows = con.execute("""
            SELECT 
                Receiver_Account as counterparty,
                count(*) as txn_count,
                round(sum(Amount), 2) as total_amount,
                min(Timestamp) as first_seen,
                max(Timestamp) as last_seen
            FROM transactions
            WHERE Sender_Account = ?
            GROUP BY Receiver_Account
            ORDER BY sum(Amount) DESC
        """, [account_id]).fetchall()

        in_list = [
            {
                "account_id": r[0],
                "bank_prefix": r[0][:4],
                "txn_count": int(r[1]),
                "total_amount": float(r[2] or 0.0),
                "first_seen": str(r[3]),
                "last_seen": str(r[4])
            }
            for r in in_rows[:limit]
        ]

        out_list = [
            {
                "account_id": r[0],
                "bank_prefix": r[0][:4],
                "txn_count": int(r[1]),
                "total_amount": float(r[2] or 0.0),
                "first_seen": str(r[3]),
                "last_seen": str(r[4])
            }
            for r in out_rows[:limit]
        ]

        return {
            "account_id": account_id,
            "total_incoming_accounts": len(in_rows),
            "total_outgoing_accounts": len(out_rows),
            "total_connected_accounts": len(in_rows) + len(out_rows),
            "incoming": in_list,
            "outgoing": out_list,
            "incoming_connected": in_list,
            "outgoing_connected": out_list
        }

