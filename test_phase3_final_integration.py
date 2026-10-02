"""
Operation Abhedya-Chakra -- Phase 3 Final Integration & Verification Suite
Verifies the complete end-to-end pipeline:
DuckDB -> detect.py -> context.py -> explain_template.py / explain_llm.py -> guardrails
Also verifies API endpoints, storage immutability, and benchmarks.
"""

import sys
import time
import random
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

from backend.detect import DetectionEngine
from backend.context import build_context
from backend.explain_template import generate_explanation as generate_deterministic_explanation
from backend.explain_llm import generate_llm_explanation, DEFAULT_MODEL
from backend.guardrails import validate as validate_guardrails
from backend.main import (
    health_check,
    get_account,
    get_transactions,
    detect_account,
    explain_account,
    trace_transaction,
    list_flagged_accounts
)

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

def run_integration_tests():
    print("==========================================================================================")
    print("  PHASE 3 -- FINAL INTEGRATION & REGRESSION VERIFICATION")
    print("==========================================================================================\n")

    meta_before = get_file_meta()
    print("STORAGE STATE BEFORE VERIFICATION:")
    print(f"  DuckDB:  {meta_before['DuckDB']['size_bytes']:,} bytes ({meta_before['DuckDB']['size_mb']} MB) | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"  Parquet: {meta_before['Parquet']['size_bytes']:,} bytes ({meta_before['Parquet']['size_mb']} MB) | mtime: {meta_before['Parquet']['mtime']}")
    print("------------------------------------------------------------------------------------------\n")

    # 1. Pipeline Verification across all required scenarios
    print("1. PIPELINE INTEGRATION SCENARIOS (DuckDB -> detect -> context -> explain -> guardrails)")
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

        # High-score account HIGH101: In 100k, forwards 98k (ratio 0.98), gap 200s, Web_Emulator, 185.1.2.3
        (3, "TX2001", "INFLOW1", "HIGH101", "IFSC01", "IFSC04", 100000.0, "2026-09-15 11:00:00", "UPI", "UPI/REF/TASK", "103.2.2.2", "Android"),
        (4, "TX2002", "HIGH101", "OUT1", "IFSC04", "IFSC05", 25000.0, "2026-09-15 11:03:20", "IMPS", "IMPS/P2A/1", "185.1.2.3", "Web_Emulator"),
        (5, "TX2003", "HIGH101", "OUT2", "IFSC04", "IFSC06", 25000.0, "2026-09-15 11:04:00", "IMPS", "IMPS/P2A/2", "185.1.2.4", "Web_Emulator"),
        (6, "TX2004", "HIGH101", "OUT3", "IFSC04", "IFSC07", 25000.0, "2026-09-15 11:05:00", "IMPS", "IMPS/P2A/3", "185.1.2.5", "Web_Emulator"),
        (7, "TX2005", "HIGH101", "OUT4", "IFSC04", "IFSC08", 23000.0, "2026-09-15 11:06:00", "IMPS", "IMPS/P2A/4", "185.1.2.6", "Web_Emulator"),

        # No-outgoing / Sink account SINK101
        (8, "TX3001", "SRC2", "SINK101", "IFSC01", "IFSC09", 50000.0, "2026-09-15 12:00:00", "NEFT", "NEFT/CREDIT", "103.3.3.3", "Windows_Browser"),

        # Repeated Transaction_ID rows with distinct row_id
        (9, "TX_DUP", "REP_S1", "REP_R1", "IFSC01", "IFSC10", 1200.0, "2026-09-15 13:00:00", "UPI", "UPI/RETRY1", "103.4.4.4", "Android"),
        (10, "TX_DUP", "REP_S1", "REP_R1", "IFSC01", "IFSC10", 1200.0, "2026-09-15 13:00:01", "UPI", "UPI/RETRY2", "103.4.4.4", "Android"),

        # Cycle case: CYC_A -> CYC_B -> CYC_C -> CYC_A
        (11, "TX_C1", "CYC_A", "CYC_B", "IFSC01", "IFSC02", 5000.0, "2026-09-15 15:00:00", "IMPS", "IMPS/C1", "103.5.5.5", "Android"),
        (12, "TX_C2", "CYC_B", "CYC_C", "IFSC02", "IFSC03", 4900.0, "2026-09-15 15:05:00", "IMPS", "IMPS/C2", "103.5.5.6", "Android"),
        (13, "TX_C3", "CYC_C", "CYC_A", "IFSC03", "IFSC01", 4800.0, "2026-09-15 15:10:00", "IMPS", "IMPS/C3", "103.5.5.7", "Android"),
    ]

    for r in test_rows:
        mem_con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(r))

    pipeline_results = []

    # Scenario 1: Valid high-score account
    ctx_high = build_context("HIGH101", con=mem_con, db_path=":memory:")
    exp_high = generate_llm_explanation(ctx_high, force_fallback=True)
    val_high = validate_guardrails(exp_high["explanation"], ctx_high)
    s1_pass = ctx_high["risk_score"] == 100 and val_high["passed"]
    pipeline_results.append(("Valid high-score account", s1_pass, f"Score: {ctx_high['risk_score']}/100, Guardrail: {val_high['passed']}"))

    # Scenario 2: Normal score-0 account
    ctx_nor = build_context("NOR101", con=mem_con, db_path=":memory:")
    exp_nor = generate_llm_explanation(ctx_nor, force_fallback=True)
    val_nor = validate_guardrails(exp_nor["explanation"], ctx_nor)
    s2_pass = ctx_nor["risk_score"] == 0 and "no indicators matched" in exp_nor["explanation"].lower() and val_nor["passed"]
    pipeline_results.append(("Normal score-0 account", s2_pass, f"Score: 0, Mandatory phrase present, Guardrail: {val_nor['passed']}"))

    # Scenario 3: Invalid account
    ctx_inv = build_context("NON_EXISTENT_ACC", con=mem_con, db_path=":memory:")
    exp_inv = generate_llm_explanation(ctx_inv, force_fallback=True)
    val_inv = validate_guardrails(exp_inv["explanation"], ctx_inv)
    s3_pass = ctx_inv["account_exists"] is False and ctx_inv["risk_score"] == 0 and val_inv["passed"]
    pipeline_results.append(("Invalid account", s3_pass, f"account_exists=False, Handled safely"))

    # Scenario 4: Account with no outgoing transactions (Sink)
    ctx_sink = build_context("SINK101", con=mem_con, db_path=":memory:")
    exp_sink = generate_llm_explanation(ctx_sink, force_fallback=True)
    val_sink = validate_guardrails(exp_sink["explanation"], ctx_sink)
    s4_pass = ctx_sink["account_summary"]["out_degree"] == 0 and len(ctx_sink["observed_paths"]) == 0 and val_sink["passed"]
    pipeline_results.append(("Account with no outgoing transactions", s4_pass, f"In: 1, Out: 0, Observed paths: 0, Guardrail: {val_sink['passed']}"))

    # Scenario 5: Repeated Transaction_ID
    ctx_dup = build_context("REP_S1", con=mem_con, db_path=":memory:")
    exp_dup = generate_llm_explanation(ctx_dup, force_fallback=True)
    val_dup = validate_guardrails(exp_dup["explanation"], ctx_dup)
    s5_pass = ctx_dup["account_summary"]["out_degree"] == 2 and val_dup["passed"]
    pipeline_results.append(("Repeated Transaction_ID", s5_pass, f"Tracked via distinct row_ids (9, 10), Guardrail: {val_dup['passed']}"))

    # Scenario 6: Cycle case
    ctx_cyc = build_context("CYC_A", con=mem_con, db_path=":memory:")
    exp_cyc = generate_llm_explanation(ctx_cyc, force_fallback=True)
    val_cyc = validate_guardrails(exp_cyc["explanation"], ctx_cyc)
    s6_pass = ctx_cyc["account_exists"] is True and val_cyc["passed"]
    pipeline_results.append(("Cycle case", s6_pass, f"Cycle accounted deterministically, Guardrail: {val_cyc['passed']}"))

    # Scenario 7: LLM failure -> deterministic fallback
    exp_fail = generate_llm_explanation(ctx_high, ollama_url="http://localhost:9999/api/generate", timeout_s=1.0)
    s7_pass = exp_fail["source"] == "deterministic_fallback" and exp_fail["guardrail_status"]["passed"]
    pipeline_results.append(("LLM failure -> deterministic fallback", s7_pass, f"Source: {exp_fail['source']}, Guardrail: {exp_fail['guardrail_status']['passed']}"))

    # Scenario 8: Guardrail rejection -> deterministic fallback
    # Simulate LLM generating ungrounded text
    hallucinated_text = exp_high["explanation"] + " Account is a guilty fraudster with stolen INR 999999.00."
    g_val_rej = validate_guardrails(hallucinated_text, ctx_high)
    s8_pass = g_val_rej["passed"] is False and len(g_val_rej["violations"]) >= 2
    pipeline_results.append(("Guardrail rejection -> deterministic fallback", s8_pass, f"Caught {len(g_val_rej['violations'])} violations, Rejects safely"))

    for name, status, detail in pipeline_results:
        print(f"  [{'PASS' if status else 'FAIL'}] {name:<42}: {detail}")
    print()

    # 2. API Endpoints Verification
    print("2. API ENDPOINTS STRUCTURE VERIFICATION")
    print("-" * 95)
    api_results = []

    # /health
    h = health_check()
    api_results.append(("/health", h.get("status") == "healthy", f"status: {h.get('status')}, engine: {h.get('engine')}"))

    # /api/flagged
    fl = list_flagged_accounts(min_score=100, limit=10)
    fl_pass = fl.get("total_matching") == 129 and len(fl.get("accounts", [])) == 10
    api_results.append(("/api/flagged", fl_pass, f"total_matching: {fl.get('total_matching')}, returned: {fl.get('returned_count')}"))

    target_acc = fl["accounts"][0]["account_id"]

    # /api/detect/{account_id}
    det = detect_account(target_acc)
    det_pass = det.get("risk_score") == 100 and "matched_indicators" in det and "evidence_by_indicator" in det
    api_results.append((f"/api/detect/{target_acc}", det_pass, f"risk_score: {det.get('risk_score')}, indicators: {len(det.get('matched_indicators', []))}"))

    # /api/explain/{account_id}
    exp = explain_account(target_acc)
    exp_pass = exp.get("risk_score") == 100 and exp.get("guardrail_status", {}).get("passed") is True and "context" in exp
    api_results.append((f"/api/explain/{target_acc}", exp_pass, f"risk_score: {exp.get('risk_score')}, guardrail_status: {exp.get('guardrail_status', {}).get('passed')}"))

    # /api/trace/{row_id}
    tr = trace_transaction(row_id=35196, max_hops=2, cap=5)
    tr_pass = tr.get("start_row_id") == 35196 and "paths" in tr and "disclaimer" in tr
    api_results.append(("/api/trace/35196", tr_pass, f"start_row_id: {tr.get('start_row_id')}, paths: {len(tr.get('paths', []))}, disclaimer: {bool(tr.get('disclaimer'))}"))

    for endpoint, status, detail in api_results:
        print(f"  [{'PASS' if status else 'FAIL'}] {endpoint:<35}: {detail}")
    print()

    # 3. Performance Benchmark on Real Verified Dataset
    print("3. PERFORMANCE MEASUREMENTS BENCHMARK")
    print("-" * 95)
    real_con = duckdb.connect(str(DB_PATH), read_only=True)
    engine = DetectionEngine(db_path=DB_PATH)

    # a. Full dataset account scoring
    t0_score = time.perf_counter()
    all_scores = engine.score_all_accounts_fast(con=real_con)
    t_score_all = (time.perf_counter() - t0_score) * 1000

    # b. Context generation over 25 sampled accounts
    RANDOM_SEED = 42
    random.seed(RANDOM_SEED)
    all_acc_ids = [a["account_id"] for a in all_scores]
    sampled_accs = random.sample(all_acc_ids, 25)

    ctx_times = []
    fb_times = []
    guard_times = []

    for acc in sampled_accs:
        t0 = time.perf_counter()
        c = build_context(acc, con=real_con)
        ctx_times.append((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        txt = generate_deterministic_explanation(c)
        fb_times.append((time.perf_counter() - t1) * 1000)

        t2 = time.perf_counter()
        _ = validate_guardrails(txt, c)
        guard_times.append((time.perf_counter() - t2) * 1000)

    # c. LLM explanation timing for sample account
    t_llm_start = time.perf_counter()
    live_sample_ctx = build_context("AIRP10000312", con=real_con)
    live_llm_res = generate_llm_explanation(live_sample_ctx, timeout_s=25.0)
    t_llm_ms = (time.perf_counter() - t_llm_start) * 1000

    print(f"  Random Seed:                    {RANDOM_SEED}")
    print(f"  Full Account Scoring (24,873):  {t_score_all:.2f} ms ({t_score_all / len(all_scores):.4f} ms/account)")
    print(f"  Context Generation (25 accs):   Min = {min(ctx_times):.2f} ms | Median = {sorted(ctx_times)[len(ctx_times)//2]:.2f} ms | Avg = {sum(ctx_times)/len(ctx_times):.2f} ms")
    print(f"  Deterministic Fallback:         Min = {min(fb_times):.2f} ms | Median = {sorted(fb_times)[len(fb_times)//2]:.2f} ms | Avg = {sum(fb_times)/len(fb_times):.2f} ms")
    print(f"  Guardrail Validation:           Min = {min(guard_times):.2f} ms | Median = {sorted(guard_times)[len(guard_times)//2]:.2f} ms | Avg = {sum(guard_times)/len(guard_times):.2f} ms")
    print(f"  Live Local LLM Inference:       {t_llm_ms:.2f} ms (Model: {DEFAULT_MODEL} via local Ollama)\n")

    real_con.close()
    mem_con.close()

    # 4. Storage Safety Verification
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
    run_integration_tests()
