import time
import os
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CSV_PATH = PROJECT_ROOT / "VoidHacks8_MuleAccount_2M_Transactions.csv"
DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"
PARQUET_PATH = PROJECT_ROOT / "data" / "transactions.parquet"

def run_benchmark():
    print("=" * 65)
    print("  OPERATION ABHEDYA-CHAKRA — PHASE 1 BENCHMARK SUITE")
    print("=" * 65)

    if not DB_PATH.exists():
        print(f"Error: DuckDB not found at {DB_PATH}")
        return

    con = duckdb.connect(str(DB_PATH), read_only=True)

    # 1. Total row count & integrity
    t0 = time.perf_counter()
    row_count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    count_latency = (time.perf_counter() - t0) * 1000

    # 2. Unique Accounts
    t0 = time.perf_counter()
    unique_accs = con.execute("""
        SELECT COUNT(DISTINCT acc) FROM (
            SELECT Sender_Account AS acc FROM transactions
            UNION ALL
            SELECT Receiver_Account AS acc FROM transactions
        )
    """).fetchone()[0]
    unique_latency = (time.perf_counter() - t0) * 1000

    # 3. Quality & Duplicates
    t0 = time.perf_counter()
    dup_txns = con.execute("SELECT COUNT(*) - COUNT(DISTINCT Transaction_ID) FROM transactions").fetchone()[0]
    dup_latency = (time.perf_counter() - t0) * 1000

    # Nulls across critical fields (measured dynamically)
    null_count = con.execute("""
        SELECT COUNT(CASE WHEN Amount IS NULL OR Timestamp IS NULL OR Sender_Account IS NULL OR Receiver_Account IS NULL OR row_id IS NULL THEN 1 END) 
        FROM transactions
    """).fetchone()[0]

    # 4. Account lookup latency (10 sample accounts)
    sample_accs = [r[0] for r in con.execute("SELECT DISTINCT Sender_Account FROM transactions LIMIT 10").fetchall()]
    latencies = []
    for acc in sample_accs:
        t_acc0 = time.perf_counter()
        con.execute("SELECT * FROM transactions WHERE Sender_Account = ? OR Receiver_Account = ?", [acc, acc]).fetchall()
        latencies.append((time.perf_counter() - t_acc0) * 1000)

    avg_lookup_ms = sum(latencies) / len(latencies)
    min_lookup_ms = min(latencies)
    max_lookup_ms = max(latencies)

    # 5. File sizes
    csv_size_mb = CSV_PATH.stat().st_size / (1024 * 1024) if CSV_PATH.exists() else 0
    db_size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    pq_size_mb = PARQUET_PATH.stat().st_size / (1024 * 1024) if PARQUET_PATH.exists() else 0

    con.close()

    print(f"Total Transactions:        {row_count:,} rows")
    print(f"Total Unique Accounts:     {unique_accs:,} accounts")
    print(f"Duplicate Transaction IDs: {dup_txns:,} extra instances")
    print(f"Null Values Measured:      {null_count} (Across critical fields + row_id)")
    print("-" * 65)
    print(f"Raw CSV File Size:         {csv_size_mb:.2f} MB")
    print(f"DuckDB Storage Size:       {db_size_mb:.2f} MB")
    print(f"Parquet Storage Size:      {pq_size_mb:.2f} MB (Compression: ZSTD)")
    print("-" * 65)
    print(f"Full Table Count Latency:  {count_latency:.2f} ms")
    print(f"Unique Account Aggregation:{unique_latency:.2f} ms")
    print(f"Account Lookup (Avg):      {avg_lookup_ms:.2f} ms")
    print(f"Account Lookup (Min/Max):  {min_lookup_ms:.2f} ms / {max_lookup_ms:.2f} ms")
    print("=" * 65)

if __name__ == "__main__":
    run_benchmark()
