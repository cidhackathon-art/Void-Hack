import sys
import time
import random
import threading
import tracemalloc
import ctypes
from ctypes import wintypes
from pathlib import Path
import datetime
import duckdb

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.trace import trace_onward_flow

DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"
PARQUET_PATH = PROJECT_ROOT / "data" / "transactions.parquet"

SAFETY_TIMEOUT_SECONDS = 3.0
RANDOM_SEED = 42

# OS Process Memory measurement (Windows Working Set / RSS includes DuckDB C++ memory)
class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('PageFaultCount', wintypes.DWORD),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]

GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
GetProcessMemoryInfo.restype = wintypes.BOOL

def get_process_memory_mb():
    try:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / (1024 * 1024), 2), round(counters.PeakWorkingSetSize / (1024 * 1024), 2)
    except Exception:
        pass
    return 0.0, 0.0

def get_db_file_metadata():
    meta = {}
    for label, p in [("DuckDB", DB_PATH), ("Parquet", PARQUET_PATH)]:
        if p.exists():
            st = p.stat()
            meta[label] = {
                "size_bytes": st.st_size,
                "size_mb": round(st.st_size / (1024 * 1024), 2),
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat()
            }
        else:
            meta[label] = None
    return meta

def select_benchmark_starting_transactions(con: duckdb.DuckDBPyConnection):
    """
    Selects 22 starting transactions:
    - 2 Earliest transactions (Day 1 start)
    - 3 Transactions whose RECEIVER has highest out-degree (worst-case onward branching)
    - 15 Stratified random transactions (1 per calendar day, Days 1 to 15) using fixed seed 42
    - 2 Additional stratified checkpoints (Days 7 and 14)
    """
    rng = random.Random(RANDOM_SEED)

    daily = con.execute("""
        SELECT 
            strftime(Timestamp, '%Y-%m-%d') as day,
            MIN(row_id) as min_rid,
            MAX(row_id) as max_rid
        FROM transactions
        GROUP BY strftime(Timestamp, '%Y-%m-%d')
        ORDER BY day
    """).fetchall()

    selected = []

    # 1. Earliest transactions
    selected.append((1, "Earliest transaction (Day 1 start)"))
    selected.append((50, "Earliest transaction (Day 1 early)"))

    # 2. Transactions whose RECEIVER account has highest out-degree
    # (Trace expands from Receiver onward, making Receiver out-degree the true branching worst-case)
    high_receiver_txns = con.execute("""
        WITH top_senders AS (
            SELECT Sender_Account, COUNT(*) as outdegree
            FROM transactions
            GROUP BY Sender_Account
            ORDER BY outdegree DESC
            LIMIT 10
        )
        SELECT t.row_id, t.Receiver_Account, ts.outdegree
        FROM transactions t
        JOIN top_senders ts ON t.Receiver_Account = ts.Sender_Account
        ORDER BY t.row_id ASC
        LIMIT 3
    """).fetchall()

    for r_row in high_receiver_txns:
        rid, rec_acc, outdeg = r_row[0], r_row[1], r_row[2]
        selected.append((rid, f"High-outdegree Receiver ({rec_acc}, outdeg={outdeg})"))

    # 3. Stratified random selection across all 15 calendar days
    for day_str, min_r, max_r in daily:
        rid = rng.randint(min_r, max_r)
        selected.append((rid, f"Stratified random ({day_str})"))

    # 4. Additional stratified checkpoints
    selected.append((rng.randint(daily[6][1], daily[6][2]), "Stratified random (Day 7 extra)"))
    selected.append((rng.randint(daily[13][1], daily[13][2]), "Stratified random (Day 14 extra)"))

    return selected

class TraceExecutionWorker(threading.Thread):
    def __init__(self, row_id: int, con: duckdb.DuckDBPyConnection):
        super().__init__()
        self.row_id = row_id
        self.con = con
        self.abort_event = threading.Event()
        self.result = None
        self.error = None
        self.frontier_sizes = {"H1": 1, "H2": 0, "H3": 0, "H4": 0}
        self.status = "PENDING"
        self.start_time = 0.0
        self.elapsed_ms = 0.0

    def run(self):
        self.start_time = time.perf_counter()
        tracker = {"frontier_sizes": self.frontier_sizes}
        try:
            self.result = trace_onward_flow(
                start_row_id=self.row_id,
                con=self.con,
                max_hops=4,
                cap=None,  # Strictly uncapped to measure empirical frontier expansion
                verbose=False,
                abort_event=self.abort_event,
                tracker=tracker
            )
            self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000
            self.status = "COMPLETED"
            self.frontier_sizes = {
                "H1": self.result["frontier_sizes"]["H1"],
                "H2": self.result["frontier_sizes"]["H2"],
                "H3": self.result["frontier_sizes"]["H3"],
                "H4": self.result["frontier_sizes"]["H4"]
            }
        except Exception as e:
            self.elapsed_ms = (time.perf_counter() - self.start_time) * 1000
            err_str = str(e).lower()
            if "interrupt" in err_str or self.abort_event.is_set():
                self.status = "TIMEOUT"
            else:
                self.status = f"ERROR: {e}"
                self.error = e

def run_benchmarks():
    print("==========================================================================================")
    print("  OPERATION ABHEDYA-CHAKRA — REAL 2M DATASET TRACE BENCHMARK")
    print("  Hop Definition: H1=Start (1 txn), H2=L1, H3=L2, H4=L3 | Max trace = 4 transactions")
    print("==========================================================================================")
    print(f"Safety Timeout: {SAFETY_TIMEOUT_SECONDS} seconds (benchmark-only safety threshold, NOT a detection threshold)")
    print(f"Random Seed:    {RANDOM_SEED}")
    print(f"Traversal Cap:  Uncapped (cap=None) to measure empirical frontier expansion")
    print("------------------------------------------------------------------------------------------")

    # 1. Database Safety Check: Before Benchmark
    meta_before = get_db_file_metadata()
    print("DATABASE STATE BEFORE BENCHMARK:")
    print(f"  DuckDB:  {meta_before['DuckDB']['size_bytes']:,} bytes ({meta_before['DuckDB']['size_mb']} MB) | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_before['Parquet']['size_bytes']:,} bytes ({meta_before['Parquet']['size_mb']} MB) | mtime: {meta_before['Parquet']['mtime']}")
    print("------------------------------------------------------------------------------------------")

    # 2. Select starting transactions
    sample_con = duckdb.connect(str(DB_PATH), read_only=True)
    benchmark_set = select_benchmark_starting_transactions(sample_con)
    sample_con.close()

    print(f"SELECTED STARTING TRANSACTIONS ({len(benchmark_set)} total):")
    for idx, (rid, cat) in enumerate(benchmark_set, 1):
        print(f"  [{idx:02d}] row_id={rid:<7} | {cat}")
    print("==========================================================================================")
    print(f"{'Idx':<4} {'row_id':<8} {'Timestamp':<20} {'Status':<10} {'Latency(ms)':<12} {'Frontiers [H1, H2, H3, H4]':<28} {'Paths':<8} {'Ties':<6} {'Cycles':<8} {'RSS_Mem(MB)'}")
    print("-" * 115)

    results = []

    for idx, (rid, cat) in enumerate(benchmark_set, 1):
        # Open an isolated read-only connection for this thread
        thread_con = duckdb.connect(str(DB_PATH), read_only=True)

        # Get transaction timestamp
        ts_row = thread_con.execute("SELECT Timestamp FROM transactions WHERE row_id = ?", [rid]).fetchone()
        ts_str = str(ts_row[0]) if ts_row else "UNKNOWN"

        worker = TraceExecutionWorker(rid, thread_con)

        worker.start()
        worker.join(timeout=SAFETY_TIMEOUT_SECONDS)

        if worker.is_alive():
            # Safety timeout triggered! Signal abort and interrupt DuckDB query
            worker.abort_event.set()
            try:
                thread_con.interrupt()
            except Exception:
                pass
            worker.join(timeout=2.0)
            worker.status = "TIMEOUT"

        rss_mb, peak_rss_mb = get_process_memory_mb()
        thread_con.close()

        # Extract metrics
        status = worker.status
        latency_val = round(worker.elapsed_ms, 2)
        f_sizes = worker.frontier_sizes
        f_display = f"[{f_sizes.get('H1', 1)}, {f_sizes.get('H2', 0)}, {f_sizes.get('H3', 0)}, {f_sizes.get('H4', 0)}]"

        if status == "COMPLETED" and worker.result:
            total_paths = worker.result["total_paths_found"]
            ties = worker.result["skipped_timestamp_ties"]
            cycles = worker.result["skipped_cycles_count"]
        else:
            total_paths = "N/A"
            ties = "N/A"
            cycles = "N/A"

        res_record = {
            "index": idx,
            "row_id": rid,
            "category": cat,
            "timestamp": ts_str,
            "status": status,
            "latency_ms": latency_val,
            "frontier_sizes": f_sizes,
            "total_paths": total_paths,
            "ties": ties,
            "cycles": cycles,
            "rss_mem_mb": rss_mb,
            "peak_rss_mb": peak_rss_mb
        }
        results.append(res_record)

        lat_str = f"{latency_val:.2f}" if status == "COMPLETED" else f">{SAFETY_TIMEOUT_SECONDS*1000:.0f}"
        print(f"{idx:<4} {rid:<8} {ts_str:<20} {status:<10} {lat_str:<12} {f_display:<28} {str(total_paths):<8} {str(ties):<6} {str(cycles):<8} {rss_mb:<8.2f}")

    print("==========================================================================================")

    # 3. Database Safety Check: After Benchmark
    meta_after = get_db_file_metadata()
    print("DATABASE STATE AFTER BENCHMARK:")
    print(f"  DuckDB:  {meta_after['DuckDB']['size_bytes']:,} bytes ({meta_after['DuckDB']['size_mb']} MB) | mtime: {meta_after['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_after['Parquet']['size_bytes']:,} bytes ({meta_after['Parquet']['size_mb']} MB) | mtime: {meta_after['Parquet']['mtime']}")

    duckdb_untouched = (meta_before["DuckDB"]["size_bytes"] == meta_after["DuckDB"]["size_bytes"]) and (meta_before["DuckDB"]["mtime"] == meta_after["DuckDB"]["mtime"])
    parquet_untouched = (meta_before["Parquet"]["size_bytes"] == meta_after["Parquet"]["size_bytes"]) and (meta_before["Parquet"]["mtime"] == meta_after["Parquet"]["mtime"])

    print(f"  Verification: DuckDB Untouched = {duckdb_untouched} | Parquet Untouched = {parquet_untouched}")
    print("------------------------------------------------------------------------------------------")

    # 4. Summary Statistics
    completed_runs = [r for r in results if r["status"] == "COMPLETED"]
    timeout_runs = [r for r in results if r["status"] == "TIMEOUT"]

    print("BENCHMARK EXECUTION SUMMARY:")
    print(f"  Total Runs:             {len(results)}")
    print(f"  COMPLETED Count:        {len(completed_runs)}")
    print(f"  TIMEOUT Count:          {len(timeout_runs)}")

    if completed_runs:
        c_latencies = [r["latency_ms"] for r in completed_runs]
        min_lat = min(c_latencies)
        avg_lat = sum(c_latencies) / len(c_latencies)
        max_lat = max(c_latencies)
        print(f"\n  COMPLETED Runs Latency Statistics (n = {len(completed_runs)}):")
        print(f"    Min Latency:          {min_lat:.2f} ms")
        print(f"    Average Latency:      {avg_lat:.2f} ms")
        print(f"    Max Latency:          {max_lat:.2f} ms")

    if timeout_runs:
        print(f"\n  TIMEOUT Runs Frontier & Memory Analysis (n = {len(timeout_runs)}):")
        for r in timeout_runs:
            f = r["frontier_sizes"]
            print(f"    row_id {r['row_id']:<7} ({r['timestamp']}): reached H1={f.get('H1')}, H2={f.get('H2')}, H3={f.get('H3')}, H4={f.get('H4')} | Process RSS: {r['rss_mem_mb']} MB")

    print("==========================================================================================")
    return results

if __name__ == "__main__":
    run_benchmarks()
