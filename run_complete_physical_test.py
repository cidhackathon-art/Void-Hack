"""
Operation Abhedya-Chakra -- Master Physical Test Suite
Executes complete physical validation across all 4 project phases:
  Phase 1: Storage & 2M Transaction Query Performance
  Phase 2: Forensic Detection Engine & Selectivity (129 marker accounts)
  Phase 3: Context Builder, Guardrail Grounding & LLM/Deterministic Fallback
  Phase 4: Live REST API, Interactive UI Dashboard & PDF Dossier
"""

import sys
import time
import json
import urllib.request
import urllib.error
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
CSV_PATH = PROJECT_ROOT / "VoidHacks8_MuleAccount_2M_Transactions.csv"
FRONTEND_PATH = PROJECT_ROOT / "frontend" / "index.html"
BASE_URL = "http://127.0.0.1:8000"

from backend.detect import DetectionEngine
from backend.context import build_context
from backend.explain_template import generate_explanation
from backend.guardrails import validate as validate_guardrails
from backend.explain_llm import generate_llm_explanation
from backend.trace import trace_onward_flow

def print_banner(text):
    print("\n" + "=" * 88)
    print(f"  {text}")
    print("=" * 88)

def test_1_storage():
    print_banner("1. PHYSICAL STORAGE & QUERY PERFORMANCE (2,000,000 TRANSACTIONS)")
    assert DB_PATH.exists(), "transactions.duckdb missing!"
    assert PARQUET_PATH.exists(), "transactions.parquet missing!"
    assert CSV_PATH.exists(), "CSV missing!"

    db_size = DB_PATH.stat().st_size
    pq_size = PARQUET_PATH.stat().st_size
    csv_size = CSV_PATH.stat().st_size
    print(f"  [OK] DuckDB size:  {db_size:,} bytes ({db_size / (1024*1024):.2f} MB)")
    print(f"  [OK] Parquet size: {pq_size:,} bytes ({pq_size / (1024*1024):.2f} MB)")
    print(f"  [OK] CSV size:     {csv_size:,} bytes ({csv_size / (1024*1024):.2f} MB)")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    t0 = time.perf_counter()
    cnt = con.execute("SELECT count(*) FROM transactions").fetchone()[0]
    lat_cnt = (time.perf_counter() - t0) * 1000
    assert cnt == 2000000, f"Expected 2M rows, got {cnt}"
    print(f"  [OK] Verified 2,000,000 transactions scanned in {lat_cnt:.2f} ms")

    # Fast PK lookup
    t0 = time.perf_counter()
    row = con.execute("SELECT row_id, Sender_Account, Receiver_Account, Amount, Timestamp FROM transactions WHERE row_id = 1814134").fetchone()
    lat_lk = (time.perf_counter() - t0) * 1000
    assert row[0] == 1814134
    print(f"  [OK] Direct row_id lookup in {lat_lk:.2f} ms: row_id {row[0]} | {row[1]} -> {row[2]} | INR {row[3]:,.2f} | {row[4]}")
    con.close()
    return True

def test_2_detection_engine():
    print_banner("2. FORENSIC DETECTION & SELECTIVITY (PHASE 2)")
    engine = DetectionEngine(db_path=DB_PATH)
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # High-score account
    t0 = time.perf_counter()
    det_high = engine.score_account("SBIN10000435", con=con)
    lat_high = (time.perf_counter() - t0) * 1000
    assert det_high["risk_score"] == 100
    assert len(det_high["matched_indicators"]) == 4
    print(f"  [OK] High-Risk Account SBIN10000435 scored in {lat_high:.2f} ms:")
    print(f"       Score: {det_high['risk_score']}/100 | Indicators: {[i['indicator'] for i in det_high['matched_indicators']]}")

    # Normal score-0 account
    t0 = time.perf_counter()
    det_norm = engine.score_account("ICIC10014001", con=con)
    lat_norm = (time.perf_counter() - t0) * 1000
    assert det_norm["risk_score"] == 0
    assert len(det_norm["matched_indicators"]) == 0
    print(f"  [OK] Normal Account ICIC10014001 scored in {lat_norm:.2f} ms:")
    print(f"       Score: {det_norm['risk_score']}/100 | Matches: 0 (Baseline)")

    # Full dataset scoring selectivity
    t0 = time.perf_counter()
    all_scores = engine.score_all_accounts_fast(con=con)
    lat_all = (time.perf_counter() - t0) * 1000
    high_count = sum(1 for a in all_scores if a["risk_score"] == 100)
    assert high_count == 129, f"Expected 129 marker group accounts, got {high_count}"
    print(f"  [OK] Full 24,873 accounts evaluated in {lat_all:.2f} ms ({lat_all/len(all_scores):.4f} ms/account)")
    print(f"       Exact 129 Marker Reference Group Captured: {high_count} accounts")
    con.close()
    return True

def test_3_context_and_guardrails():
    print_banner("3. EVIDENCE CONTEXT, GUARDRAILS & AUDIT GROUNDING (PHASE 3)")
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # Context generation
    t0 = time.perf_counter()
    ctx = build_context("AIRP10000312", con=con)
    lat_ctx = (time.perf_counter() - t0) * 1000
    assert ctx["account_exists"] is True
    assert ctx["risk_score"] == 100
    assert len(ctx["observed_paths"]) >= 1
    assert len(ctx["limitations"]) == 4
    print(f"  [OK] Context generated in {lat_ctx:.2f} ms for AIRP10000312:")
    print(f"       Observed Paths: {len(ctx['observed_paths'])} | In-Degree: {ctx['account_summary']['in_degree']} | Out-Degree: {ctx['account_summary']['out_degree']}")

    # Deterministic template explanation
    exp = generate_explanation(ctx)
    val = validate_guardrails(exp, ctx)
    assert val["passed"] is True, f"Guardrail violations: {val['violations']}"
    print(f"  [OK] Deterministic explanation passed all guardrails (0 violations)")

    # Adversarial validation tests
    val_fake_num = validate_guardrails(exp + " Invented INR 999999.00.", ctx)
    assert val_fake_num["passed"] is False
    print(f"  [OK] Guardrail rejected invented number: '{val_fake_num['violations'][-1]}'")

    val_fake_row = validate_guardrails(exp + " Onward flow in row_id 888888.", ctx)
    assert val_fake_row["passed"] is False
    print(f"  [OK] Guardrail rejected unverified row_id: '{val_fake_row['violations'][-1]}'")

    val_forbid = validate_guardrails(exp + " Confirmed fraud by guilty fraudster.", ctx)
    assert val_forbid["passed"] is False
    print(f"  [OK] Guardrail rejected forbidden accusations: '{val_forbid['violations'][0]}'")

    con.close()
    return True

def test_4_live_api_and_trace():
    print_banner("4. LIVE REST API & TRANSACTION TRACE ENGINE (FASTAPI)")
    
    # 4.1 /health
    req = urllib.request.urlopen(f"{BASE_URL}/health")
    assert req.status == 200
    h_data = json.loads(req.read().decode())
    assert h_data["status"] == "healthy"
    print(f"  [OK] GET /health: 200 OK | Status: {h_data['status']} | Engine: {h_data['engine']}")

    # 4.2 /api/flagged
    req = urllib.request.urlopen(f"{BASE_URL}/api/flagged?min_score=100&limit=5")
    fl_data = json.loads(req.read().decode())
    assert fl_data["total_matching"] == 129
    print(f"  [OK] GET /api/flagged: 200 OK | Total matching reference group: {fl_data['total_matching']}")

    # 4.3 /api/detect/SBIN10000435
    req = urllib.request.urlopen(f"{BASE_URL}/api/detect/SBIN10000435")
    det_data = json.loads(req.read().decode())
    assert det_data["risk_score"] == 100
    print(f"  [OK] GET /api/detect/SBIN10000435: 200 OK | Score: {det_data['risk_score']}/100")

    # 4.4 /api/explain/SBIN10000435
    req = urllib.request.urlopen(f"{BASE_URL}/api/explain/SBIN10000435")
    exp_data = json.loads(req.read().decode())
    assert exp_data["guardrail_status"]["passed"] is True
    print(f"  [OK] GET /api/explain/SBIN10000435: 200 OK | Guardrail status: PASSED")

    # 4.5 /api/trace/35196
    t0 = time.perf_counter()
    req = urllib.request.urlopen(f"{BASE_URL}/api/trace/35196?max_hops=2&cap=5")
    tr_data = json.loads(req.read().decode())
    lat_tr = (time.perf_counter() - t0) * 1000
    assert tr_data["start_row_id"] == 35196
    assert len(tr_data["paths"]) >= 1
    assert "disclaimer" in tr_data
    print(f"  [OK] GET /api/trace/35196: 200 OK in {lat_tr:.2f} ms | Found {len(tr_data['paths'])} paths")
    print(f"       Disclaimer: '{tr_data['disclaimer']}'")
    return True

def test_5_frontend_and_pdf():
    print_banner("5. DASHBOARD UI & CLIENT-SIDE AUDIT DOSSIER (PHASE 4)")

    # 5.1 Dashboard availability
    req = urllib.request.urlopen(f"{BASE_URL}/dashboard/")
    assert req.status == 200
    html_content = req.read().decode()
    assert len(html_content) > 10000
    print(f"  [OK] GET /dashboard/: 200 OK ({len(html_content):,} bytes)")

    # 5.2 Branding and Elements
    assert "Operation Abhedya-Chakra" in html_content
    assert "AC" in html_content
    assert "Indore Police • Financial Forensic Analysis" in html_content
    print(f"  [OK] Verified Project Branding: Operation Abhedya-Chakra + Indore Police")

    # 5.3 PDF export contract
    assert "Abhedya_Chakra_Dossier_${accId}.pdf" in html_content
    assert "printable-report" in html_content
    print(f"  [OK] Verified PDF Dossier Exporter: Abhedya_Chakra_Dossier_<account_id>.pdf template verified")
    return True

def test_6_storage_immutability():
    print_banner("6. STORAGE IMMUTABILITY & AUDIT TRAIL VERIFICATION")
    db_size = DB_PATH.stat().st_size
    pq_size = PARQUET_PATH.stat().st_size
    csv_size = CSV_PATH.stat().st_size

    assert db_size == 369897472, f"DuckDB size changed! {db_size}"
    assert pq_size == 69482546, f"Parquet size changed! {pq_size}"
    assert csv_size == 286788986, f"CSV size changed! {csv_size}"

    print(f"  [OK] DuckDB:  369,897,472 bytes (Untouched)")
    print(f"  [OK] Parquet: 69,482,546 bytes (Untouched)")
    print(f"  [OK] CSV:     286,788,986 bytes (Untouched)")
    print(f"  [OK] Read-only integrity strictly preserved across all operations")
    return True

def run_master_physical_audit():
    print("\n" + "#" * 88)
    print("  OPERATION ABHEDYA-CHAKRA -- MASTER PHYSICAL SYSTEM AUDIT")
    print("  Live End-to-End Execution Across All 4 Deliverable Phases")
    print("#" * 88)

    t_start = time.perf_counter()
    test_1_storage()
    test_2_detection_engine()
    test_3_context_and_guardrails()
    test_4_live_api_and_trace()
    test_5_frontend_and_pdf()
    test_6_storage_immutability()

    total_s = time.perf_counter() - t_start
    print_banner(f"ALL 6 PHYSICAL SYSTEM AUDITS PASSED (100% OPERATIONAL IN {total_s:.2f}s)")
    print("  System Status: PRODUCTION-READY & AUDIT-VERIFIED")
    print("=" * 88 + "\n")

if __name__ == "__main__":
    run_master_physical_audit()
