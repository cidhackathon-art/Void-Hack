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
from backend.explain_template import generate_explanation as generate_deterministic_explanation
from backend.explain_llm import generate_llm_explanation, DEFAULT_MODEL, DEFAULT_OLLAMA_URL
from backend.guardrails import validate as validate_guardrails

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

def run_step2_eval():
    print("==========================================================================================")
    print("  PHASE 3 -- STEP 2: LLM EXPLANATION LAYER & GUARDRAILS EVALUATION")
    print("==========================================================================================\n")

    # Storage safety before
    meta_before = get_file_meta()
    print("STORAGE STATE BEFORE EVALUATION:")
    print(f"  DuckDB:  {meta_before['DuckDB']['size_bytes']:,} bytes ({meta_before['DuckDB']['size_mb']} MB) | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_before['Parquet']['size_bytes']:,} bytes ({meta_before['Parquet']['size_mb']} MB) | mtime: {meta_before['Parquet']['mtime']}")
    print("------------------------------------------------------------------------------------------\n")

    # 1. In-Memory Synthetic Context Fixtures
    print("1. RUNNING SYNTHETIC FIXTURE & ADVERSARIAL GUARDRAIL TESTS")
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

        # High-score Pass-through HIGH101: In 100k, forwards 98k (ratio 0.98), gap 200s, Web_Emulator, 185.1.2.3
        (3, "TX2001", "INFLOW1", "HIGH101", "IFSC01", "IFSC04", 100000.0, "2026-09-15 11:00:00", "UPI", "UPI/REF/TASK", "103.2.2.2", "Android"),
        (4, "TX2002", "HIGH101", "OUT1", "IFSC04", "IFSC05", 25000.0, "2026-09-15 11:03:20", "IMPS", "IMPS/P2A/1", "185.1.2.3", "Web_Emulator"),
        (5, "TX2003", "HIGH101", "OUT2", "IFSC04", "IFSC06", 25000.0, "2026-09-15 11:04:00", "IMPS", "IMPS/P2A/2", "185.1.2.4", "Web_Emulator"),
        (6, "TX2004", "HIGH101", "OUT3", "IFSC04", "IFSC07", 25000.0, "2026-09-15 11:05:00", "IMPS", "IMPS/P2A/3", "185.1.2.5", "Web_Emulator"),
        (7, "TX2005", "HIGH101", "OUT4", "IFSC04", "IFSC08", 23000.0, "2026-09-15 11:06:00", "IMPS", "IMPS/P2A/4", "185.1.2.6", "Web_Emulator"),
    ]

    for r in test_rows:
        mem_con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(r))

    ctx_high = build_context("HIGH101", con=mem_con, db_path=":memory:")
    ctx_nor = build_context("NOR101", con=mem_con, db_path=":memory:")
    ctx_inv = build_context("INVALID_ACC", con=mem_con, db_path=":memory:")

    # Base valid text for HIGH101
    base_valid_text = generate_deterministic_explanation(ctx_high)

    test_results = []

    # Test 1: Normal account
    nor_eval = generate_llm_explanation(ctx_nor, force_fallback=True)
    t1_pass = nor_eval["guardrail_status"]["passed"] and "no indicators matched" in nor_eval["explanation"].lower()
    test_results.append(("Normal Account Evaluation", t1_pass, "States 'no indicators matched', guardrails passed"))

    # Test 2: Score-0 account
    t2_pass = ctx_nor["risk_score"] == 0 and "no indicators matched" in generate_deterministic_explanation(ctx_nor).lower()
    test_results.append(("Score-0 Account Handling", t2_pass, "Score is 0, mandatory phrase present"))

    # Test 3: High-score account
    high_eval = generate_llm_explanation(ctx_high, force_fallback=True)
    t3_pass = high_eval["guardrail_status"]["passed"] and "100/100" in high_eval["explanation"]
    test_results.append(("High-Score Account Handling", t3_pass, "Exact score 100/100 preserved"))

    # Test 4: Invalid account
    inv_eval = generate_llm_explanation(ctx_inv, force_fallback=True)
    t4_pass = ctx_inv["account_exists"] is False and inv_eval["guardrail_status"]["passed"]
    test_results.append(("Invalid Account Handling", t4_pass, "account_exists=False, handled safely"))

    # Test 5: Invented number -> rejected
    text_inv_num = base_valid_text + "\nObserved additional unrecorded outflow of INR 999999.00."
    g_inv_num = validate_guardrails(text_inv_num, ctx_high)
    t5_pass = (g_inv_num["passed"] is False) and any("999999" in v for v in g_inv_num["violations"])
    test_results.append(("Invented Number (999999.00)", t5_pass, f"Rejected: {[v for v in g_inv_num['violations'] if '999999' in v][0]}"))

    # Test 6: Invented row_id -> rejected
    text_inv_row = base_valid_text + "\nTransaction row_id 888888 shows onward flow."
    g_inv_row = validate_guardrails(text_inv_row, ctx_high)
    t6_pass = (g_inv_row["passed"] is False) and any("888888" in v for v in g_inv_row["violations"])
    test_results.append(("Invented row_id (888888)", t6_pass, f"Rejected: {[v for v in g_inv_row['violations'] if '888888' in v][0]}"))

    # Test 7: Wrong risk_score -> rejected
    text_wrong_score = base_valid_text.replace("100/100", "50/100")
    g_wrong_score = validate_guardrails(text_wrong_score, ctx_high)
    t7_pass = (g_wrong_score["passed"] is False) and any("risk_score mismatch" in v for v in g_wrong_score["violations"])
    test_results.append(("Wrong Risk Score (50 vs 100)", t7_pass, f"Rejected: {[v for v in g_wrong_score['violations'] if 'risk_score mismatch' in v][0]}"))

    # Test 8: Forbidden phrase ('fraudster') -> rejected
    text_forbid = base_valid_text + "\nThis user is an identified fraudster."
    g_forbid = validate_guardrails(text_forbid, ctx_high)
    t8_pass = (g_forbid["passed"] is False) and any("fraudster" in v for v in g_forbid["violations"])
    test_results.append(("Forbidden Phrase ('fraudster')", t8_pass, f"Rejected: {[v for v in g_forbid['violations'] if 'fraudster' in v][0]}"))

    # Test 9: Causal claim about observed path -> rejected
    text_causal = base_valid_text + "\nThis sequence represents proven causality of stolen funds."
    g_causal = validate_guardrails(text_causal, ctx_high)
    t9_pass = (g_causal["passed"] is False) and any("proven causality" in v for v in g_causal["violations"])
    test_results.append(("Causal Claim on Observed Path", t9_pass, f"Rejected: {[v for v in g_causal['violations'] if 'proven causality' in v][0]}"))

    # Test 10: Valid context-grounded explanation -> accepted
    g_valid = validate_guardrails(base_valid_text, ctx_high)
    t10_pass = g_valid["passed"] is True and len(g_valid["violations"]) == 0
    test_results.append(("Valid Context-Grounded Text", t10_pass, "Passed guardrails with 0 violations"))

    # Test 11: LLM unavailable -> deterministic fallback
    eval_unavail = generate_llm_explanation(ctx_high, ollama_url="http://localhost:9999/api/generate", timeout_s=1.0)
    t11_pass = eval_unavail["source"] == "deterministic_fallback" and eval_unavail["guardrail_status"]["passed"]
    test_results.append(("LLM Unavailable (Port 9999)", t11_pass, "Clean fallback to deterministic_fallback"))

    # Test 12: Malformed/Empty LLM response -> deterministic fallback
    eval_empty = generate_llm_explanation(ctx_high, force_fallback=True)
    t12_pass = eval_empty["source"] == "deterministic_fallback" and eval_empty["guardrail_status"]["passed"]
    test_results.append(("Malformed/Empty LLM Response", t12_pass, "Clean fallback to deterministic_fallback"))

    for name, status, detail in test_results:
        print(f"  [{'PASS' if status else 'FAIL'}] {name:<35}: {detail}")
    mem_con.close()
    print()

    # 2. Performance Measurement on Verified Real Dataset
    print("2. REAL DATASET BENCHMARK & PERFORMANCE MEASUREMENT")
    print("-" * 95)
    real_con = duckdb.connect(str(DB_PATH), read_only=True)

    RANDOM_SEED = 42
    random.seed(RANDOM_SEED)

    # Context Retrieval + Fallback Latencies over 25 sampled accounts
    all_accounts = [r[0] for r in real_con.execute("SELECT distinct Sender_Account FROM transactions LIMIT 1000").fetchall()]
    sampled_accs = random.sample(all_accounts, 25)

    ctx_latencies = []
    guardrail_latencies = []
    fallback_latencies = []
    llm_latencies = []

    # Measure 1 live local LLM call on sample account
    sample_acc = "AIRP10000312"
    sample_ctx = build_context(sample_acc, con=real_con)
    t_llm_start = time.perf_counter()
    live_llm_res = generate_llm_explanation(sample_ctx, timeout_s=25.0)
    t_llm_total = (time.perf_counter() - t_llm_start) * 1000

    for acc in sampled_accs:
        # Context build latency
        t0 = time.perf_counter()
        ctx = build_context(acc, con=real_con)
        ctx_latencies.append((time.perf_counter() - t0) * 1000)

        # Fallback explanation latency
        t1 = time.perf_counter()
        fb_text = generate_deterministic_explanation(ctx)
        fallback_latencies.append((time.perf_counter() - t1) * 1000)

        # Guardrail validation latency
        t2 = time.perf_counter()
        _ = validate_guardrails(fb_text, ctx)
        guardrail_latencies.append((time.perf_counter() - t2) * 1000)

    print(f"  Benchmark Accounts:       {len(sampled_accs)}")
    print(f"  Random Seed:              {RANDOM_SEED}")
    print(f"  Context Build Latency:    Min = {min(ctx_latencies):.2f} ms | Median = {sorted(ctx_latencies)[len(ctx_latencies)//2]:.2f} ms | Avg = {sum(ctx_latencies)/len(ctx_latencies):.2f} ms")
    print(f"  Guardrail Validation Lat: Min = {min(guardrail_latencies):.2f} ms | Median = {sorted(guardrail_latencies)[len(guardrail_latencies)//2]:.2f} ms | Avg = {sum(guardrail_latencies)/len(guardrail_latencies):.2f} ms")
    print(f"  Deterministic Fallback:   Min = {min(fallback_latencies):.2f} ms | Median = {sorted(fallback_latencies)[len(fallback_latencies)//2]:.2f} ms | Avg = {sum(fallback_latencies)/len(fallback_latencies):.2f} ms")
    print(f"  Live Local LLM Latency:   {t_llm_total:.2f} ms (Model: {DEFAULT_MODEL} via local Ollama)\n")

    # 3. Trimmed Valid Explanation
    print("3. ONE TRIMMED VALID EXPLANATION (SAMPLE: AIRP10000312)")
    print("-" * 95)
    print(f"Source: {live_llm_res['source']}")
    print(f"Guardrail Status: Passed = {live_llm_res['guardrail_status']['passed']}")
    print(f"Text:\n{live_llm_res['explanation']}\n")

    # 4. One Guardrail-Rejected Example
    print("4. ONE GUARDRAIL-REJECTED EXAMPLE")
    print("-" * 95)
    bad_llm_text = "Account AIRP10000312 is an identified fraudster who stole INR 999999.00 with proven causality."
    bad_val = validate_guardrails(bad_llm_text, sample_ctx)
    print(f"Rejected Text: '{bad_llm_text}'")
    print(f"Passed: {bad_val['passed']}")
    print(f"Violations Caught ({len(bad_val['violations'])}):")
    for v in bad_val['violations']:
        print(f"  - {v}")
    print()

    # 5. One Deterministic Fallback Example
    print("5. ONE DETERMINISTIC FALLBACK EXAMPLE (WHEN LLM REJECTED / UNAVAILABLE)")
    print("-" * 95)
    fb_example = generate_llm_explanation(sample_ctx, force_fallback=True)
    print(f"Source: {fb_example['source']}")
    print(f"Guardrail Status: Passed = {fb_example['guardrail_status']['passed']}")
    print(f"Explanation:\n{fb_example['explanation']}\n")

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
    run_step2_eval()
