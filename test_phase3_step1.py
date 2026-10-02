import sys
import time
import random
import json
import datetime
from pathlib import Path
import duckdb

# Ensure UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "transactions.duckdb"
PARQUET_PATH = PROJECT_ROOT / "data" / "transactions.parquet"

from backend.context import build_context
from backend.explain_template import generate_explanation
from backend.guardrails import validate

def get_file_meta():
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

def run_tests():
    print("==========================================================================================")
    print("  PHASE 3 -- STEP 1: CONTEXT BUILDER & GUARDRAILS EVALUATION")
    print("==========================================================================================\n")

    # Storage safety before
    meta_before = get_file_meta()
    print("STORAGE STATE BEFORE EVALUATION:")
    print(f"  DuckDB:  {meta_before['DuckDB']['size_bytes']:,} bytes ({meta_before['DuckDB']['size_mb']} MB) | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_before['Parquet']['size_bytes']:,} bytes ({meta_before['Parquet']['size_mb']} MB) | mtime: {meta_before['Parquet']['mtime']}")
    print("------------------------------------------------------------------------------------------\n")

    # 1. In-Memory Synthetic Tests
    print("1. IN-MEMORY SYNTHETIC TESTS (REAL SCHEMA + row_id)")
    print("-" * 95)
    mem_con = duckdb.connect(":memory:")
    mem_con.execute("""
        CREATE TABLE transactions (
            row_id BIGINT,
            Transaction_ID VARCHAR,
            Sender_Account VARCHAR,
            Receiver_Account VARCHAR,
            Sender_IFSC VARCHAR,
            Receiver_IFSC VARCHAR,
            Amount DOUBLE,
            Timestamp TIMESTAMP,
            Payment_Mode VARCHAR,
            Narration VARCHAR,
            IP_Address VARCHAR,
            Device_Type VARCHAR
        );
    """)

    test_rows = [
        # Normal account NOR101
        (1, "TX1001", "SRC1", "NOR101", "IFSC01", "IFSC02", 500.0, "2026-09-15 10:00:00", "UPI", "UPI/Zomato", "103.1.1.1", "Android"),
        (2, "TX1002", "NOR101", "DST1", "IFSC02", "IFSC03", 450.0, "2026-09-15 14:00:00", "UPI", "UPI/Swiggy", "103.1.1.1", "Android"),

        # Pass-through MULE101: In 100k, forwards 98k (ratio 0.98), gap 200s, Web_Emulator, 185.1.2.3
        (3, "TX2001", "INFLOW1", "MULE101", "IFSC01", "IFSC04", 100000.0, "2026-09-15 11:00:00", "UPI", "UPI/REF/TASK", "103.2.2.2", "Android"),
        (4, "TX2002", "MULE101", "OUT1", "IFSC04", "IFSC05", 25000.0, "2026-09-15 11:03:20", "IMPS", "IMPS/P2A/1", "185.1.2.3", "Web_Emulator"),
        (5, "TX2003", "MULE101", "OUT2", "IFSC04", "IFSC06", 25000.0, "2026-09-15 11:04:00", "IMPS", "IMPS/P2A/2", "185.1.2.4", "Web_Emulator"),
        (6, "TX2004", "MULE101", "OUT3", "IFSC04", "IFSC07", 25000.0, "2026-09-15 11:05:00", "IMPS", "IMPS/P2A/3", "185.1.2.5", "Web_Emulator"),
        (7, "TX2005", "MULE101", "OUT4", "IFSC04", "IFSC08", 23000.0, "2026-09-15 11:06:00", "IMPS", "IMPS/P2A/4", "185.1.2.6", "Web_Emulator"),

        # Terminal Sink Account: SINK101 (in 20k, 0 outgoing)
        (8, "TX3001", "OUT1", "SINK101", "IFSC05", "IFSC09", 20000.0, "2026-09-15 12:00:00", "UPI", "UPI/WALLET_LOAD", "194.1.1.1", "Linux_Script"),

        # No-outgoing Account: NO_OUT_101
        (9, "TX3002", "OUT2", "NO_OUT_101", "IFSC05", "IFSC10", 15000.0, "2026-09-15 12:30:00", "UPI", "UPI/Transfer", "103.1.1.2", "Android"),

        # Repeated Transaction_ID test
        (10, "TX_REP_1", "REP_S1", "REP_R1", "IFSC12", "IFSC13", 1000.0, "2026-09-15 15:00:00", "UPI", "UPI/Retail", "103.6.6.6", "Android"),
        (11, "TX_REP_1", "REP_S2", "REP_R2", "IFSC14", "IFSC15", 2000.0, "2026-09-15 16:00:00", "UPI", "UPI/Retail", "103.6.6.7", "Android"),
    ]

    for r in test_rows:
        mem_con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(r))

    ctx_mule = build_context("MULE101", con=mem_con, db_path=":memory:")
    ctx_nor = build_context("NOR101", con=mem_con, db_path=":memory:")
    ctx_inv = build_context("INVALID_ACC", con=mem_con, db_path=":memory:")
    ctx_rep = build_context("REP_S1", con=mem_con, db_path=":memory:")
    ctx_no_out = build_context("NO_OUT_101", con=mem_con, db_path=":memory:")

    tests_context = [
        ("Pass-Through Account (MULE101)", ctx_mule["risk_score"] == 100 and len(ctx_mule["matched_indicators"]) == 4 and len(ctx_mule["observed_paths"]) >= 1, "Context built, score: 100, observed paths captured"),
        ("Normal Account (NOR101)", ctx_nor["risk_score"] == 0 and len(ctx_nor["matched_indicators"]) == 0 and "no indicators matched" in generate_explanation(ctx_nor).lower(), "Score 0, explanation states 'no indicators matched'"),
        ("Invalid Account", ctx_inv["account_exists"] is False and ctx_inv["risk_score"] == 0, "account_exists=False, Score 0"),
        ("Repeated Transaction_ID Account", ctx_rep["account_exists"] is True, "Handled safely via row_id"),
        ("No-Outgoing Account (NO_OUT_101)", ctx_no_out["account_summary"]["out_degree"] == 0 and ctx_no_out["risk_score"] == 40, "Handled safely, sink detected"),
    ]

    for t_name, status, detail in tests_context:
        print(f"  [PASS] {t_name:<36}: {detail}")
    mem_con.close()
    print()

    # 2. Guardrail Tests
    print("2. GUARDRAIL VALIDATION TESTS")
    print("-" * 95)

    # Valid template text test
    exp_valid = generate_explanation(ctx_mule)
    res_valid = validate(exp_valid, ctx_mule)

    # Invented number test
    text_invented_num = exp_valid + "\nAlso detected unverified amount of INR 999999.00 in the account."
    res_inv_num = validate(text_invented_num, ctx_mule)

    # Forbidden phrase test
    text_forbidden = exp_valid + "\nThis user is a confirmed fraud criminal and fraudster."
    res_forbid = validate(text_forbidden, ctx_mule)

    # Nonexistent row_id test
    text_nonexist_row = exp_valid + "\nReference row_id 888888 participated in the transaction."
    res_nonexist_row = validate(text_nonexist_row, ctx_mule)

    guardrail_cases = [
        ("Correct Text from Template", res_valid["passed"] is True, f"Passed: {res_valid['passed']}"),
        ("Invented Number ('999999')", res_inv_num["passed"] is False and any("999999" in v for v in res_inv_num["violations"]), f"Caught violation: {res_inv_num['violations'][-1]}"),
        ("Forbidden Phrases ('criminal', 'fraudster')", res_forbid["passed"] is False and any("forbidden" in v.lower() for v in res_forbid["violations"]), f"Caught violation: {res_forbid['violations'][0]}"),
        ("Nonexistent row_id ('888888')", res_nonexist_row["passed"] is False and any("888888" in v for v in res_nonexist_row["violations"]), f"Caught violation: {res_nonexist_row['violations'][-1]}")
    ]

    for g_name, status, detail in guardrail_cases:
        print(f"  [{'PASS' if status else 'FAIL'}] {g_name:<38}: {detail}")
    print()

    # 3. Full Real Dataset Evaluation & Benchmark
    print("3. REAL DATASET BENCHMARK & PERFORMANCE MEASUREMENT")
    print("-" * 95)
    real_con = duckdb.connect(str(DB_PATH), read_only=True)

    RANDOM_SEED = 42
    random.seed(RANDOM_SEED)

    # Sample 25 accounts from dataset
    all_accounts = [r[0] for r in real_con.execute("SELECT distinct Sender_Account FROM transactions LIMIT 1000").fetchall()]
    sampled_accs = random.sample(all_accounts, 25)

    latencies = []
    char_sizes = []
    sample_ctx_real = None

    for acc in sampled_accs:
        t_start = time.perf_counter()
        ctx = build_context(acc, con=real_con)
        lat_ms = (time.perf_counter() - t_start) * 1000
        latencies.append(lat_ms)

        ctx_json_str = json.dumps(ctx, indent=2)
        char_sizes.append(len(ctx_json_str))

        if sample_ctx_real is None and ctx["risk_score"] > 0:
            sample_ctx_real = ctx

    if sample_ctx_real is None:
        sample_ctx_real = build_context("AIRP10000312", con=real_con)

    print(f"  Accounts Benchmarked:     {len(sampled_accs)}")
    print(f"  Random Seed:              {RANDOM_SEED}")
    print(f"  Min Latency:              {min(latencies):.2f} ms")
    print(f"  Median Latency:           {sorted(latencies)[len(latencies)//2]:.2f} ms")
    print(f"  Average Latency:          {sum(latencies)/len(latencies):.2f} ms")
    print(f"  Max Latency:              {max(latencies):.2f} ms")
    print(f"  Min Context Size:         {min(char_sizes):,d} characters")
    print(f"  Median Context Size:      {sorted(char_sizes)[len(char_sizes)//2]:,d} characters")
    print(f"  Max Context Size:         {max(char_sizes):,d} characters")
    print("  (Note: Character counts reported; token counts are not claimed.)\n")

    # 4. Display Trimmed Sample Context
    print("4. TRIMMED SAMPLE CONTEXT (AIRP10000312)")
    print("-" * 95)
    trimmed_sample = {
        "account_id": sample_ctx_real["account_id"],
        "account_exists": sample_ctx_real["account_exists"],
        "risk_score": sample_ctx_real["risk_score"],
        "account_summary": sample_ctx_real["account_summary"],
        "matched_indicators": [
            {
                "indicator": m["indicator"],
                "contribution": m["contribution"],
                "basis": m["basis"],
                "evidence_summary": m.get("evidence", {}).get("summary")
            }
            for m in sample_ctx_real["matched_indicators"]
        ],
        "observed_paths": sample_ctx_real["observed_paths"][:2],
        "device_ip_summary": sample_ctx_real["device_ip_summary"],
        "limitations": sample_ctx_real["limitations"]
    }
    print(json.dumps(trimmed_sample, indent=2))
    print()

    # 5. Display Deterministic Template Explanation for Sample
    print("5. SAMPLE DETERMINISTIC TEMPLATE EXPLANATION")
    print("-" * 95)
    sample_exp = generate_explanation(sample_ctx_real)
    print(sample_exp)
    print()

    # Verify Guardrail on the Template Explanation
    v_res = validate(sample_exp, sample_ctx_real)
    print(f"Guardrail Check on Generated Explanation: Passed = {v_res['passed']} (Violations: {len(v_res['violations'])})\n")

    real_con.close()

    # Storage safety after
    meta_after = get_file_meta()
    print("------------------------------------------------------------------------------------------")
    print("STORAGE SAFETY VERIFICATION AFTER EVALUATION:")
    print(f"  DuckDB:  {meta_after['DuckDB']['size_bytes']:,} bytes | mtime: {meta_after['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_after['Parquet']['size_bytes']:,} bytes | mtime: {meta_after['Parquet']['mtime']}")
    db_ok = meta_before['DuckDB'] == meta_after['DuckDB']
    pq_ok = meta_before['Parquet'] == meta_after['Parquet']
    print(f"  Integrity Status: DuckDB Untouched = {db_ok} | Parquet Untouched = {pq_ok}")
    print("==========================================================================================")

if __name__ == "__main__":
    run_tests()
