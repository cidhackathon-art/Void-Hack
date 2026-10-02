import time
import threading
from typing import Optional, List, Dict, Any, Set, Tuple
from pathlib import Path
import duckdb

# Disclaimer as required by specification
POSSIBLE_FLOW_DISCLAIMER = (
    "All identified paths represent possible onward flow only. "
    "No proof of funds movement or fraud is asserted."
)

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

def trace_onward_flow(
    start_row_id: int,
    con: Optional[duckdb.DuckDBPyConnection] = None,
    max_hops: int = 4,
    cap: Optional[int] = None,
    verbose: bool = False,
    abort_event: Optional[threading.Event] = None,
    tracker: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Traces possible onward flow starting from an explicit transaction row_id.
    
    Hop Definition:
    - H1 = starting transaction, size 1 (path length = 1 transaction)
    - H2 = L1 onward transactions (path length = 2 transactions)
    - H3 = L2 onward transactions (path length = 3 transactions)
    - H4 = L3 onward transactions (path length = 4 transactions)
    Maximum trace = 4 transactions total. A 5th hop never executes.
    
    Traversal Rules:
    - Input is an explicit start_row_id.
    - Next transaction timestamp must be strictly later (Timestamp > prev_timestamp).
    - Same-second transactions (Timestamp == prev_timestamp) are NOT followed.
    - Uses DuckDB frontier queries level-by-level.
    - Prevents repeated row_id and account cycles (including returning to victim).
    - Records skipped cycles and timestamp ties separately as evidence.
    - Returns possible onward flow only.
    """
    t_start = time.perf_counter()

    close_con = False
    if con is None:
        from .db import get_connection
        con = get_connection(read_only=True)
        close_con = True

    try:
        # Check abort signal
        if abort_event is not None and abort_event.is_set():
            raise duckdb.InterruptException("Interrupted due to safety timeout")

        # Step 1: Fetch starting transaction by row_id
        q_start = f"SELECT {', '.join(COLUMNS)} FROM transactions WHERE row_id = ? LIMIT 1"
        start_row = con.execute(q_start, [start_row_id]).fetchone()

        if not start_row:
            raise ValueError(f"Starting transaction row_id {start_row_id} not found in database.")

        start_txn = _row_to_dict(start_row)
        victim_account = start_txn["sender_account"]
        first_hop_account = start_txn["receiver_account"]
        start_ts = start_txn["timestamp"]

        initial_path = {
            "current_account": first_hop_account,
            "current_timestamp": start_ts,
            "path_row_ids": [start_row_id],
            "path_accounts": [victim_account, first_hop_account],
            "edge_transactions": [start_txn]
        }

        current_frontier = [initial_path]
        paths_by_hop: Dict[str, List[Dict[str, Any]]] = {
            "H1": [initial_path],
            "H2": [],
            "H3": [],
            "H4": []
        }
        frontier_sizes: Dict[Any, int] = {
            "H1": 1,
            "H2": 0,
            "H3": 0,
            "H4": 0,
            1: 1,
            2: 0,
            3: 0,
            4: 0
        }
        skipped_cycles: List[Dict[str, Any]] = []
        skipped_timestamp_ties = 0

        if tracker is not None:
            tracker["frontier_sizes"] = frontier_sizes
            tracker["current_hop"] = "H1"
            tracker["start_row_id"] = start_row_id

        # Total transactions in path strictly capped at 4:
        # H1: start transaction (1 txn)
        # H2: L1 (2 txns)
        # H3: L2 (3 txns)
        # H4: L3 (4 txns)
        effective_max_txns = min(max_hops, 4)

        # Expansion loop: hop_num corresponds to H2, H3, H4
        for hop_num in range(2, effective_max_txns + 1):
            hop_key = f"H{hop_num}"
            if tracker is not None:
                tracker["current_hop"] = hop_key

            if not current_frontier:
                frontier_sizes[hop_key] = 0
                frontier_sizes[hop_num] = 0
                if tracker is not None:
                    tracker["frontier_sizes"] = frontier_sizes
                if verbose:
                    print(f"{hop_key}: Frontier size = 0 (Terminated early)")
                break

            if abort_event is not None and abort_event.is_set():
                raise duckdb.InterruptException("Interrupted due to safety timeout")

            # Collect unique accounts in current frontier
            frontier_accounts = list({item["current_account"] for item in current_frontier})
            min_ts = min(item["current_timestamp"] for item in current_frontier)

            # DuckDB Frontier Query:
            # Fetch outgoing transactions for all accounts in frontier occurring >= min_ts
            placeholders = ", ".join(["?"] * len(frontier_accounts))
            q_frontier = f"""
                SELECT {', '.join(COLUMNS)} 
                FROM transactions 
                WHERE Sender_Account IN ({placeholders})
                  AND Timestamp >= ?
                ORDER BY Timestamp ASC, row_id ASC
            """
            candidates = con.execute(q_frontier, frontier_accounts + [min_ts]).fetchall()

            if abort_event is not None and abort_event.is_set():
                raise duckdb.InterruptException("Interrupted due to safety timeout")

            # Group candidates by Sender_Account
            candidates_by_sender: Dict[str, List[Dict[str, Any]]] = {}
            for c_row in candidates:
                c_dict = _row_to_dict(c_row)
                candidates_by_sender.setdefault(c_dict["sender_account"], []).append(c_dict)

            next_frontier: List[Dict[str, Any]] = []

            for path_state in current_frontier:
                if abort_event is not None and abort_event.is_set():
                    raise duckdb.InterruptException("Interrupted due to safety timeout")

                curr_acc = path_state["current_account"]
                curr_ts = path_state["current_timestamp"]
                path_rids = set(path_state["path_row_ids"])
                path_accs = set(path_state["path_accounts"])

                for cand in candidates_by_sender.get(curr_acc, []):
                    cand_ts = cand["timestamp"]
                    cand_rid = cand["row_id"]
                    cand_receiver = cand["receiver_account"]

                    # 1. Temporal ordering check
                    if cand_ts < curr_ts:
                        continue  # Candidate occurred before money arrived

                    if cand_ts == curr_ts:
                        # Equal timestamp tie: NOT followed as per rule
                        skipped_timestamp_ties += 1
                        continue

                    # cand_ts is strictly > curr_ts

                    # 2. Cycle prevention checks
                    if cand_rid in path_rids:
                        skipped_cycles.append({
                            "hop": hop_key,
                            "reason": "row_id_cycle",
                            "candidate_row_id": cand_rid,
                            "sender_account": curr_acc,
                            "receiver_account": cand_receiver,
                            "timestamp": cand_ts,
                            "path_accounts": list(path_state["path_accounts"])
                        })
                        continue

                    if cand_receiver in path_accs:
                        skipped_cycles.append({
                            "hop": hop_key,
                            "reason": "account_cycle",
                            "candidate_row_id": cand_rid,
                            "sender_account": curr_acc,
                            "receiver_account": cand_receiver,
                            "timestamp": cand_ts,
                            "returning_to_victim": (cand_receiver == victim_account),
                            "path_accounts": list(path_state["path_accounts"])
                        })
                        continue

                    # 3. Valid onward flow candidate
                    next_item = {
                        "current_account": cand_receiver,
                        "current_timestamp": cand_ts,
                        "path_row_ids": path_state["path_row_ids"] + [cand_rid],
                        "path_accounts": path_state["path_accounts"] + [cand_receiver],
                        "edge_transactions": path_state["edge_transactions"] + [cand]
                    }

                    next_frontier.append(next_item)

                    if cap is not None and len(next_frontier) >= cap:
                        break

                if cap is not None and len(next_frontier) >= cap:
                    break

            current_frontier = next_frontier
            paths_by_hop[hop_key] = current_frontier
            frontier_sizes[hop_key] = len(current_frontier)
            frontier_sizes[hop_num] = len(current_frontier)

            if tracker is not None:
                tracker["frontier_sizes"] = frontier_sizes

            if verbose:
                print(f"{hop_key}: Frontier size = {len(current_frontier)}")

        duration_ms = (time.perf_counter() - t_start) * 1000

        # Assemble summary of all valid onward paths found
        all_paths = []
        for h_key in ["H2", "H3", "H4"]:
            for p in paths_by_hop.get(h_key, []):
                all_paths.append({
                    "hop_level": h_key,
                    "transaction_count": len(p["path_row_ids"]),
                    "onward_hops": len(p["path_row_ids"]) - 1,
                    "account_path": p["path_accounts"],
                    "row_id_path": p["path_row_ids"],
                    "edges": [
                        {
                            "row_id": e["row_id"],
                            "transaction_id": e["transaction_id"],
                            "from_account": e["sender_account"],
                            "to_account": e["receiver_account"],
                            "amount": e["amount"],
                            "timestamp": e["timestamp"],
                            "payment_mode": e["payment_mode"]
                        }
                        for e in p["edge_transactions"]
                    ]
                })

        # Ensure frontier_sizes contains clean string keys for serialization
        clean_frontier_sizes = {
            "H1": frontier_sizes["H1"],
            "H2": frontier_sizes["H2"],
            "H3": frontier_sizes["H3"],
            "H4": frontier_sizes["H4"]
        }
        # Also preserve integer keys for callers accessing by int
        for k in [1, 2, 3, 4]:
            clean_frontier_sizes[k] = frontier_sizes[k]

        return {
            "start_row_id": start_row_id,
            "victim_account": victim_account,
            "start_transaction": start_txn,
            "max_hops_configured": effective_max_txns,
            "total_paths_found": len(all_paths),
            "frontier_sizes": clean_frontier_sizes,
            "skipped_cycles_count": len(skipped_cycles),
            "skipped_cycles": skipped_cycles,
            "skipped_timestamp_ties": skipped_timestamp_ties,
            "duration_ms": round(duration_ms, 2),
            "paths": all_paths,
            "disclaimer": POSSIBLE_FLOW_DISCLAIMER
        }

    finally:
        if close_con:
            con.close()
