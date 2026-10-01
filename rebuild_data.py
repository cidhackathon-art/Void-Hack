import os
import sys
import time
from pathlib import Path
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent
CSV_PATH = PROJECT_ROOT / "VoidHacks8_MuleAccount_2M_Transactions.csv"
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "transactions.duckdb"
PARQUET_PATH = DATA_DIR / "transactions.parquet"

def rebuild():
    t_start = time.perf_counter()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Source dataset not found at: {CSV_PATH}")

    # Remove existing db files if any
    if DB_PATH.exists():
        DB_PATH.unlink()
    # Also clean any duckdb wal files
    wal_path = DB_PATH.with_suffix(".duckdb.wal")
    if wal_path.exists():
        wal_path.unlink()
    if PARQUET_PATH.exists():
        PARQUET_PATH.unlink()

    print("=================================================================")
    print("  OPERATION ABHEDYA-CHAKRA — COMPLETE DATA REBUILD PIPELINE")
    print("=================================================================")
    print(f"Source CSV:       {CSV_PATH}")
    print(f"Target DuckDB:    {DB_PATH}")
    print(f"Target Parquet:   {PARQUET_PATH}")
    print("-----------------------------------------------------------------")

    # Step 1: Ingest CSV into DuckDB with permanent stable row_id
    print("[1/3] Ingesting CSV and computing stable row_id...")
    t0 = time.perf_counter()
    con = duckdb.connect(str(DB_PATH))

    # Compute stable row_id ordered deterministically by all 11 columns
    con.execute(f"""
        CREATE TABLE transactions AS 
        SELECT 
            ROW_NUMBER() OVER (
                ORDER BY 
                    Timestamp, 
                    Transaction_ID, 
                    Sender_Account, 
                    Receiver_Account, 
                    Receiver_IFSC, 
                    Sender_IFSC, 
                    Amount, 
                    Payment_Mode, 
                    Narration, 
                    IP_Address, 
                    Device_Type
            ) AS row_id,
            Transaction_ID,
            Sender_Account,
            Receiver_Account,
            Sender_IFSC,
            Receiver_IFSC,
            Amount,
            Timestamp,
            Payment_Mode,
            Narration,
            IP_Address,
            Device_Type
        FROM read_csv_auto('{CSV_PATH.as_posix()}')
    """)
    t_ingest = time.perf_counter() - t0
    row_count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"      Ingested {row_count:,} rows in {t_ingest:.2f} seconds.")

    # Step 2: Create Indexes
    print("[2/3] Creating indexes (row_id, sender, receiver, timestamp, txnid)...")
    t0 = time.perf_counter()
    con.execute("CREATE UNIQUE INDEX idx_row_id ON transactions(row_id)")
    con.execute("CREATE INDEX idx_sender ON transactions(Sender_Account)")
    con.execute("CREATE INDEX idx_receiver ON transactions(Receiver_Account)")
    con.execute("CREATE INDEX idx_timestamp ON transactions(Timestamp)")
    con.execute("CREATE INDEX idx_txnid ON transactions(Transaction_ID)")
    t_index = time.perf_counter() - t0
    print(f"      Indexes created in {t_index:.2f} seconds.")

    # Step 3: Export to Parquet
    print("[3/3] Exporting transactions table to Parquet (ZSTD)...")
    t0 = time.perf_counter()
    con.execute(f"""
        COPY transactions TO '{PARQUET_PATH.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    t_parquet = time.perf_counter() - t0
    print(f"      Parquet export completed in {t_parquet:.2f} seconds.")

    con.close()
    t_total = time.perf_counter() - t_start

    # Metrics
    csv_bytes = CSV_PATH.stat().st_size
    db_bytes = DB_PATH.stat().st_size
    pq_bytes = PARQUET_PATH.stat().st_size

    print("-----------------------------------------------------------------")
    print(f"Rebuild completed in {t_total:.2f} seconds total.")
    print(f"  CSV Size:     {csv_bytes:,} bytes ({csv_bytes / 1024 / 1024:.2f} MB)")
    print(f"  DuckDB Size:  {db_bytes:,} bytes ({db_bytes / 1024 / 1024:.2f} MB)")
    print(f"  Parquet Size: {pq_bytes:,} bytes ({pq_bytes / 1024 / 1024:.2f} MB)")
    print("=================================================================")
    return {
        "row_count": row_count,
        "t_ingest": t_ingest,
        "t_index": t_index,
        "t_parquet": t_parquet,
        "t_total": t_total,
        "csv_bytes": csv_bytes,
        "db_bytes": db_bytes,
        "pq_bytes": pq_bytes
    }

if __name__ == "__main__":
    rebuild()
