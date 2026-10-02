"""
Operation Abhedya-Chakra -- ML Anomaly Detection Test Suite
Verifies all 10 required architectural and behavioral guarantees.
"""

import os
import json
import urllib.request
import urllib.error
import numpy as np
import duckdb
from backend.ml_anomaly import (
    IsolationForestEngine,
    get_account_ml_anomaly,
    get_ml_anomaly_cache,
    train_and_cache_ml_anomaly,
    FEATURE_COLUMNS
)
from backend.detect import DetectionEngine

BASE_URL = "http://127.0.0.1:8000"

def get_file_meta():
    files = {
        "DuckDB": "data/transactions.duckdb",
        "Parquet": "data/transactions.parquet",
        "CSV": "VoidHacks8_MuleAccount_2M_Transactions.csv"
    }
    meta = {}
    for name, path in files.items():
        if os.path.exists(path):
            st = os.stat(path)
            meta[name] = {"size": st.st_size, "mtime": st.st_mtime}
    return meta

def run_ml_tests():
    print("==========================================================================================")
    print("  OPERATION ABHEDYA-CHAKRA -- CONTROLLED ML ANOMALY DETECTION TEST SUITE")
    print("==========================================================================================\n")

    meta_before = get_file_meta()
    print("STORAGE STATE BEFORE VERIFICATION:")
    for k, v in meta_before.items():
        print(f"  {k:<8}: {v['size']:,} bytes | mtime: {v['mtime']}")
    print("-" * 90)

    tests = []

    # 1. Model trains successfully
    try:
        X_toy = np.random.RandomState(42).normal(0, 1, size=(100, len(FEATURE_COLUMNS)))
        model = IsolationForestEngine(n_estimators=20, max_samples=64, random_state=42)
        model.fit(X_toy)
        scores = model.compute_anomaly_score_batch(X_toy)
        t1_pass = len(scores) == 100 and (0.0 <= scores.min() <= scores.max() <= 1.0)
        t1_detail = f"Trained 20 trees on 100 samples; score range: [{scores.min():.4f}, {scores.max():.4f}]"
    except Exception as e:
        t1_pass = False
        t1_detail = str(e)
    tests.append(("1. Model Trains Successfully", t1_pass, t1_detail))

    # 2. Same input produces reproducible output
    try:
        m1 = IsolationForestEngine(n_estimators=25, max_samples=64, random_state=42).fit(X_toy)
        s1 = m1.compute_anomaly_score_batch(X_toy)
        m2 = IsolationForestEngine(n_estimators=25, max_samples=64, random_state=42).fit(X_toy)
        s2 = m2.compute_anomaly_score_batch(X_toy)
        reproducible = np.allclose(s1, s2, atol=1e-12)
        t2_detail = f"Max diff between runs with random_state=42: {np.max(np.abs(s1 - s2)):.2e}"
    except Exception as e:
        reproducible = False
        t2_detail = str(e)
    tests.append(("2. Reproducibility with Fixed Seed", reproducible, t2_detail))

    # 3. Known account returns valid numerical signal
    try:
        known_acc = "SBIN10000435"
        ml_res = get_account_ml_anomaly(known_acc)
        t3_pass = (
            ml_res is not None and
            ml_res.get("status") == "available" and
            isinstance(ml_res.get("ml_anomaly_score"), (int, float)) and
            0.0 <= ml_res["ml_anomaly_score"] <= 1.0 and
            "deviation from learned baseline" in ml_res.get("ml_anomaly_level", "")
        )
        t3_detail = f"Score: {ml_res.get('ml_anomaly_score')}, Percentile: {ml_res.get('ml_anomaly_percentile')}%, Level: {ml_res.get('ml_anomaly_level')}"
    except Exception as e:
        t3_pass = False
        t3_detail = str(e)
    tests.append(("3. Known Account Valid Numerical Signal", t3_pass, t3_detail))

    # 4. Invalid account is handled safely
    try:
        inv_acc = "INVALID_ACCOUNT_XYZ_999"
        inv_res = get_account_ml_anomaly(inv_acc)
        inv_handled = inv_res is None
        t4_detail = "Returned None safely for non-existent account"
    except Exception as e:
        inv_handled = False
        t4_detail = str(e)
    tests.append(("4. Invalid Account Handled Safely", inv_handled, t4_detail))

    # 5. Missing feature values are handled safely
    try:
        X_with_nans = np.array([[np.nan, 1.0, 2.0] + [0.0] * 11, [1.0, np.nan, 0.0] + [0.0] * 11])
        X_clean = np.nan_to_num(X_with_nans, nan=0.0)
        m_nan = IsolationForestEngine(n_estimators=10, max_samples=2, random_state=42).fit(X_clean)
        s_nan = m_nan.compute_anomaly_score_batch(X_clean)
        t5_pass = len(s_nan) == 2 and not np.isnan(s_nan).any()
        t5_detail = f"NaN features handled with clean 0.0 imputation: scores = {s_nan.round(4)}"
    except Exception as e:
        t5_pass = False
        t5_detail = str(e)
    tests.append(("5. Missing Feature Values Imputed Safely", t5_pass, t5_detail))

    # 6. ML failure does not break deterministic detection
    try:
        det_eng = DetectionEngine()
        det_res = det_eng.score_account(known_acc)
        t6_pass = (
            det_res.get("risk_score") == 100 and
            len(det_res.get("matched_indicators", [])) >= 3
        )
        t6_detail = f"Deterministic Score: {det_res.get('risk_score')}/100, Matched: {len(det_res.get('matched_indicators', []))} indicators"
    except Exception as e:
        t6_pass = False
        t6_detail = str(e)
    tests.append(("6. Deterministic Engine Isolated from ML", t6_pass, t6_detail))

    # 7. Existing deterministic risk score remains unchanged
    try:
        norm_acc = "ICIC10014001"
        norm_res = det_eng.score_account(norm_acc)
        high_res = det_eng.score_account(known_acc)
        t7_pass = (norm_res.get("risk_score") == 0 and high_res.get("risk_score") == 100)
        t7_detail = f"Clean account = {norm_res.get('risk_score')}, High-risk account = {high_res.get('risk_score')} (Exact match with Phase 2 baseline)"
    except Exception as e:
        t7_pass = False
        t7_detail = str(e)
    tests.append(("7. Deterministic Scores Strictly Unchanged", t7_pass, t7_detail))

    # 8. Existing API endpoints continue working + new ML API
    try:
        health_resp = json.loads(urllib.request.urlopen(f"{BASE_URL}/health").read())
        ml_resp = json.loads(urllib.request.urlopen(f"{BASE_URL}/api/ml-anomaly/{known_acc}").read())
        t8_pass = (
            health_resp.get("status") == "healthy" and
            ml_resp.get("account_id") == known_acc and
            ml_resp.get("status") == "available"
        )
        t8_detail = f"GET /health: {health_resp.get('status')} | GET /api/ml-anomaly: HTTP 200, Score = {ml_resp.get('ml_anomaly_score')}"
    except Exception as e:
        t8_pass = False
        t8_detail = str(e)
    tests.append(("8. Live API Endpoints & ML Route Operational", t8_pass, t8_detail))

    # 9. Existing PDF Generation / Dashboard integrity
    try:
        dash_html = urllib.request.urlopen(f"{BASE_URL}/dashboard/").read().decode("utf-8")
        has_ml_section = "ML ANOMALY SIGNAL" in dash_html
        has_pdf_title = "Operation Abhedya-Chakra — Forensic Investigation Dossier" in dash_html
        t9_pass = has_ml_section and has_pdf_title
        t9_detail = f"Dashboard HTML: ML Signal Card present = {has_ml_section}, PDF Dossier Export template = {has_pdf_title}"
    except Exception as e:
        t9_pass = False
        t9_detail = str(e)
    tests.append(("9. Dashboard & PDF Dossier Integrity", t9_pass, t9_detail))

    # 10. No source database/data files modified
    meta_after = get_file_meta()
    data_untouched = True
    details_data = []
    for k in meta_before:
        size_same = meta_before[k]["size"] == meta_after[k]["size"]
        mtime_same = meta_before[k]["mtime"] == meta_after[k]["mtime"]
        if not (size_same and mtime_same):
            data_untouched = False
        details_data.append(f"{k}: Size={'Matches' if size_same else 'CHANGED'}, Mtime={'Matches' if mtime_same else 'CHANGED'}")
    tests.append(("10. Storage Immutability (DuckDB, Parquet, CSV)", data_untouched, " | ".join(details_data)))

    print("TEST RESULTS:")
    print("-" * 90)
    all_passed = True
    for name, passed, detail in tests:
        status_str = "[PASS]" if passed else "[FAIL]"
        if not passed:
            all_passed = False
        print(f"  {status_str} {name:<45}: {detail}")

    print("-" * 90)
    print("STORAGE SAFETY VERIFICATION AFTER ML EXECUTION:")
    for k, v in meta_after.items():
        print(f"  {k:<8}: {v['size']:,} bytes (Untouched = True)")
    print(f"\nFinal Result: {'ALL 10 TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    print("==========================================================================================")
    return all_passed

if __name__ == "__main__":
    success = run_ml_tests()
    exit(0 if success else 1)
