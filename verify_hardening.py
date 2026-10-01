import os
import json
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"
PARQUET_PATH = PROJECT_ROOT / "data" / "transactions.parquet"
CSV_PATH = PROJECT_ROOT / "VoidHacks8_MuleAccount_2M_Transactions.csv"
FINGERPRINT_PATH = PROJECT_ROOT / "data" / "rebuild1_fingerprint.json"

def verify():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    
    print("=================================================================")
    print("  OPERATION ABHEDYA-CHAKRA — STEP 0 HARDENING VERIFICATION")
    print("=================================================================")

    # 1. Row counts comparison across all 3 formats
    csv_rows = con.execute(f"SELECT COUNT(*) FROM read_csv_auto('{CSV_PATH.as_posix()}')").fetchone()[0]
    db_rows = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    pq_rows = con.execute(f"SELECT COUNT(*) FROM read_parquet('{PARQUET_PATH.as_posix()}')").fetchone()[0]

    print("1. ROW COUNT TRIPLE-CHECK:")
    print(f"   CSV Rows:     {csv_rows:,}")
    print(f"   DuckDB Rows:  {db_rows:,}")
    print(f"   Parquet Rows: {pq_rows:,}")
    print(f"   Match Status: {'ALL MATCH (PASS)' if (csv_rows == db_rows == pq_rows) else 'MISMATCH (FAIL)'}")

    # 2. Row ID density, uniqueness, and bounds
    rid_stats = con.execute("""
        SELECT 
            COUNT(DISTINCT row_id) AS distinct_rids,
            MIN(row_id) AS min_rid,
            MAX(row_id) AS max_rid,
            COUNT(CASE WHEN row_id IS NULL THEN 1 END) AS null_rids
        FROM transactions
    """).fetchone()

    print("\n2. ROW_ID INTEGRITY:")
    print(f"   Distinct row_id: {rid_stats[0]:,}")
    print(f"   Min row_id:      {rid_stats[1]}")
    print(f"   Max row_id:      {rid_stats[2]:,}")
    print(f"   Null row_ids:    {rid_stats[3]}")
    rid_pass = (rid_stats[0] == db_rows and rid_stats[1] == 1 and rid_stats[2] == db_rows and rid_stats[3] == 0)
    print(f"   Integrity Status:{'DENSE & STRICTLY UNIQUE (PASS)' if rid_pass else 'FAIL'}")

    # 3. Tie Detection in ORDER BY
    ties_query = """
        SELECT COUNT(*) 
        FROM (
            SELECT 
                Timestamp, Transaction_ID, Sender_Account, Receiver_Account, 
                Receiver_IFSC, Sender_IFSC, Amount, Payment_Mode, 
                Narration, IP_Address, Device_Type, COUNT(*) as c
            FROM transactions
            GROUP BY ALL
            HAVING c > 1
        )
    """
    ties = con.execute(ties_query).fetchone()[0]
    print(f"\n3. TIE DETECTION IN ORDER BY (11 Columns):")
    print(f"   Tied Rows Count: {ties} (Zero ties -> 100% deterministic ordering guaranteed)")

    # 4. Fingerprint comparison against Rebuild 1
    print("\n4. DETERMINISTIC STABILITY CHECK ACROSS REBUILDS:")
    with open(FINGERPRINT_PATH, "r") as f:
        r1_samples = json.load(f)

    all_matched = True
    for str_rid, r1 in r1_samples.items():
        rid = int(str_rid)
        row = con.execute("SELECT row_id, Transaction_ID, Sender_Account, Receiver_Account, Amount, Timestamp FROM transactions WHERE row_id = ?", [rid]).fetchone()
        r2 = {
            "row_id": row[0],
            "txn_id": row[1],
            "sender": row[2],
            "receiver": row[3],
            "amount": row[4],
            "ts": str(row[5])
        }
        match = (r1 == r2)
        if not match:
            all_matched = False
        print(f"   row_id {rid:>7}: Match={match} | {r2['txn_id']} | {r2['sender']} -> {r2['receiver']} | Rs. {r2['amount']} | {r2['ts']}")
    print(f"   Stability Verdict: {'100% DETERMINISTIC ACROSS REBUILDS (PASS)' if all_matched else 'FAIL'}")

    # 5. Unique account count & Null counts
    total_unique_acc = con.execute("""
        SELECT COUNT(DISTINCT acc) FROM (
            SELECT Sender_Account AS acc FROM transactions
            UNION ALL
            SELECT Receiver_Account AS acc FROM transactions
        )
    """).fetchone()[0]

    q_stats = con.execute("""
        SELECT
            COUNT(CASE WHEN Transaction_ID IS NULL THEN 1 END),
            COUNT(CASE WHEN Sender_Account IS NULL THEN 1 END),
            COUNT(CASE WHEN Receiver_Account IS NULL THEN 1 END),
            COUNT(CASE WHEN Amount IS NULL THEN 1 END),
            COUNT(CASE WHEN Timestamp IS NULL THEN 1 END)
        FROM transactions
    """).fetchone()

    print("\n5. UNIQUE ACCOUNTS & NULL COUNTS (Must match Phase 1 baseline):")
    print(f"   Total Unique Accounts: {total_unique_acc:,} (Baseline: 24,873)")
    print(f"   Null Txn IDs:          {q_stats[0]}")
    print(f"   Null Senders:          {q_stats[1]}")
    print(f"   Null Receivers:        {q_stats[2]}")
    print(f"   Null Amounts:          {q_stats[3]}")
    print(f"   Null Timestamps:       {q_stats[4]}")

    con.close()
    print("=================================================================")

if __name__ == "__main__":
    verify()
