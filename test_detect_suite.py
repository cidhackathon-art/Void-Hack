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

def run_in_memory_tests():
    print("==========================================================================================")
    print("  RUNNING IN-MEMORY SYNTHETIC EDGE-CASE TESTS (REAL SCHEMA + row_id)")
    print("==========================================================================================")
    
    con = duckdb.connect(":memory:")
    con.execute("""
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

    # Populate edge case scenarios
    # 1. Normal retail account: NOR101 (sends small amount, receives similar, standard phone/browser)
    # 2. Pass-through & Fast pass-through & Rare Device & Rare IP: MULE101
    #    Receives 100,000, forwards 98,000 (ratio 0.98), in 300s (5m), sends to 5 accounts, uses Web_Emulator, IP 185.1.2.3
    # 3. Sink account: SINK101 (receives 50,000, 0 outgoing)
    # 4. Cycle account: CYC101 -> CYC102 -> CYC101
    # 5. Repeated Transaction_ID: TX_REP_1 used in 2 distinct rows
    # 6. No outgoing account: NO_OUT_101
    test_rows = [
        # Normal account NOR101: 50 incoming, 50 outgoing (standard phone, retail narration)
        (1, "TX1001", "SRC1", "NOR101", "IFSC01", "IFSC02", 500.0, "2026-09-15 10:00:00", "UPI", "UPI/Zomato", "103.1.1.1", "Android"),
        (2, "TX1002", "NOR101", "DST1", "IFSC02", "IFSC03", 450.0, "2026-09-15 14:00:00", "UPI", "UPI/Swiggy", "103.1.1.1", "Android"),

        # High-score Pass-through MULE101:
        # Incoming 100,000
        (3, "TX2001", "INFLOW1", "MULE101", "IFSC01", "IFSC04", 100000.0, "2026-09-15 11:00:00", "UPI", "UPI/REF/TASK", "103.2.2.2", "Android"),
        # Outgoing 1: 20,000 (gap 200s, Web_Emulator, 185.1.2.3)
        (4, "TX2002", "MULE101", "OUT1", "IFSC04", "IFSC05", 20000.0, "2026-09-15 11:03:20", "IMPS", "IMPS/P2A/1", "185.1.2.3", "Web_Emulator"),
        (5, "TX2003", "MULE101", "OUT2", "IFSC04", "IFSC06", 20000.0, "2026-09-15 11:04:00", "IMPS", "IMPS/P2A/2", "185.1.2.4", "Web_Emulator"),
        (6, "TX2004", "MULE101", "OUT3", "IFSC04", "IFSC07", 20000.0, "2026-09-15 11:05:00", "IMPS", "IMPS/P2A/3", "185.1.2.5", "Web_Emulator"),
        (7, "TX2005", "MULE101", "OUT4", "IFSC04", "IFSC08", 38000.0, "2026-09-15 11:06:00", "IMPS", "IMPS/P2A/4", "185.1.2.6", "Web_Emulator"),
        # Total Out = 98,000 (ratio = 0.98), Out-deg = 4, In-deg = 1, fast gap = 200s <= 900s

        # Terminal Sink Account: SINK101
        (8, "TX3001", "OUT1", "SINK101", "IFSC05", "IFSC09", 20000.0, "2026-09-15 12:00:00", "UPI", "UPI/WALLET_LOAD", "194.1.1.1", "Linux_Script"),

        # Cycle Accounts: CYC101 -> CYC102 -> CYC101
        (9, "TX4001", "CYC101", "CYC102", "IFSC10", "IFSC11", 5000.0, "2026-09-15 13:00:00", "UPI", "UPI/Transfer", "103.5.5.5", "iOS"),
        (10, "TX4002", "CYC102", "CYC101", "IFSC11", "IFSC10", 4900.0, "2026-09-15 13:10:00", "UPI", "UPI/Transfer", "103.5.5.6", "iOS"),

        # Repeated Transaction_ID test (TX_REP_1 used twice with distinct row_id)
        (11, "TX_REP_1", "REP_S1", "REP_R1", "IFSC12", "IFSC13", 1000.0, "2026-09-15 15:00:00", "UPI", "UPI/Retail", "103.6.6.6", "Android"),
        (12, "TX_REP_1", "REP_S2", "REP_R2", "IFSC14", "IFSC15", 2000.0, "2026-09-15 16:00:00", "UPI", "UPI/Retail", "103.6.6.7", "Android"),
    ]

    for r in test_rows:
        con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(r))

    engine = DetectionEngine(db_path=":memory:")

    tests = []

    # Test 1: Normal account
    res_nor = engine.score_account("NOR101", con=con)
    # Ratio = 450/500 = 0.90 (outside 0.95-1.05), Out-deg = 1 (< 3), gap = 4 hours (> 900s), device Android, IP 103.*
    assert res_nor["risk_score"] == 0, f"Expected 0 for NOR101, got {res_nor['risk_score']}"
    assert len(res_nor["matched_indicators"]) == 0
    tests.append(("Normal Account (NOR101)", True, f"Score: {res_nor['risk_score']}"))

    # Test 2: High-Score Pass-Through Account (MULE101)
    res_mule = engine.score_account("MULE101", con=con)
    assert res_mule["risk_score"] >= 80, f"Expected high score for MULE101, got {res_mule['risk_score']}"
    assert any(m["indicator"] == "pass_through" for m in res_mule["matched_indicators"])
    assert any(m["indicator"] == "fast_pass_through" for m in res_mule["matched_indicators"])
    assert any(m["indicator"] == "fan_out" for m in res_mule["matched_indicators"])
    assert any(m["indicator"] == "rare_device" for m in res_mule["matched_indicators"])
    assert any(m["indicator"] == "rare_ip" for m in res_mule["matched_indicators"])
    tests.append(("High-Score Pass-Through (MULE101)", True, f"Score: {res_mule['risk_score']} (All 5 indicators matched)"))

    # Test 3: Sink Account (SINK101)
    res_sink = engine.score_account("SINK101", con=con)
    assert res_sink["risk_score"] == 40, f"Expected 40 for SINK101, got {res_sink['risk_score']}"
    assert any(m["indicator"] == "sink" for m in res_sink["matched_indicators"])
    tests.append(("Terminal Sink Account (SINK101)", True, f"Score: {res_sink['risk_score']} (Sink indicator matched)"))

    # Test 4: Invalid/Non-existent Account
    res_inv = engine.score_account("INVALID_NON_EXISTENT", con=con)
    assert res_inv["account_exists"] is False
    assert res_inv["risk_score"] == 0
    assert len(res_inv["evidence"]) == 0
    tests.append(("Invalid/Non-existent Account", True, "account_exists=False, Score: 0"))

    # Test 5: Cycle Account
    res_cyc = engine.score_account("CYC101", con=con)
    assert res_cyc["account_exists"] is True
    tests.append(("Cycle Account (CYC101)", True, f"Score: {res_cyc['risk_score']}"))

    # Test 6: Repeated Transaction_ID Account
    res_rep = engine.score_account("REP_S1", con=con)
    assert res_rep["account_exists"] is True
    assert len(res_rep["evidence"]) >= 1
    tests.append(("Repeated Transaction_ID Account (REP_S1)", True, f"Evidence count: {len(res_rep['evidence'])}"))

    con.close()
    print("In-Memory Tests Results:")
    for t_name, status, detail in tests:
        print(f"  [PASS] {t_name:<40}: {detail}")
    print("------------------------------------------------------------------------------------------\n")
    return tests

def evaluate_real_dataset():
    print("==========================================================================================")
    print("  EVALUATING FULL 24,873 ACCOUNTS ON VERIFIED DUCKDB (READ-ONLY)")
    print("==========================================================================================")

    meta_before = get_file_meta()
    print(f"DuckDB Before:  {meta_before['DuckDB']['size_bytes']:,} bytes | mtime: {meta_before['DuckDB']['mtime']}")
    print(f"Parquet Before: {meta_before['Parquet']['size_bytes']:,} bytes | mtime: {meta_before['Parquet']['mtime']}\n")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    engine = DetectionEngine(db_path=DB_PATH)

    # 1. One-pass scoring of all 24,873 accounts
    t0 = time.perf_counter()
    all_scores = engine.score_all_accounts_fast(con=con)
    batch_lat_ms = (time.perf_counter() - t0) * 1000

    print(f"Full Dataset Pass: Scored {len(all_scores):,d} accounts in {batch_lat_ms:.2f} ms ({batch_lat_ms/len(all_scores):.4f} ms/account)")

    # 2. Get the 129 marker-group accounts
    marker_129 = set(con.execute("""
        SELECT distinct Sender_Account 
        FROM transactions 
        WHERE Narration LIKE 'IMPS/P2A%' OR Device_Type = 'Web_Emulator'
    """).fetchall())
    marker_129 = {r[0] for r in marker_129}
    print(f"Target Reference Group Size: {len(marker_129)} accounts\n")

    # 3. Selectivity & Overlap Table
    # For every indicator calculate overlap: how many accounts outside the 129-account group also match.
    ind_keys = [
        ("pass_through", "ind_pt", "Pass-Through Amount Ratio [0.95, 1.05]"),
        ("fan_out", "ind_fo", "Fan-Out Structure (Out>=3, In<=8, Out>In)"),
        ("fast_pass_through", "ind_fpt", "Fast Pass-Through (Gap <= 900s)"),
        ("rare_device", "ind_rd", "Rare Device (Linux_Script, Web_Emulator)"),
        ("rare_ip", "ind_rip", "Rare IP Subnet (185.*, 194.*)"),
        ("sink", "ind_sink", "Terminal Sink (In>=1, Out=0)")
    ]

    print("INDICATOR SELECTIVITY & OVERLAP TABLE:")
    print(f"{'Indicator Name':<20} {'129-Group Matches':<20} {'129-Group %':<14} {'Non-129 Matches (Overlap)':<28} {'Total Matches':<14}")
    print("-" * 100)

    for ind_name, field, desc in ind_keys:
        m_129 = sum(1 for a in all_scores if a["account_id"] in marker_129 and a[field] == 1)
        m_outside = sum(1 for a in all_scores if a["account_id"] not in marker_129 and a[field] == 1)
        tot = m_129 + m_outside
        pct_129 = (m_129 / len(marker_129)) * 100
        print(f"{ind_name:<20} {m_129:<20d} {f'{pct_129:.1f}%':<14} {m_outside:<28,d} {tot:<14,d}")

    # Risk score distribution
    score_bands = [
        ("0", lambda s: s == 0),
        ("1 - 24", lambda s: 1 <= s < 25),
        ("25 - 49", lambda s: 25 <= s < 50),
        ("50 - 74", lambda s: 50 <= s < 75),
        ("75 - 99", lambda s: 75 <= s < 100),
        ("100 (Max)", lambda s: s == 100)
    ]
    print("\nRisk Score Distribution across All 24,873 Accounts:")
    print(f"{'Score Range':<16} {'Total Accounts':<16} {'% of Dataset':<14} {'129-Group Accounts':<20}")
    print("-" * 68)
    for band_lbl, cond in score_bands:
        cnt_tot = sum(1 for a in all_scores if cond(a["risk_score"]))
        cnt_129 = sum(1 for a in all_scores if a["account_id"] in marker_129 and cond(a["risk_score"]))
        pct = (cnt_tot / len(all_scores)) * 100
        print(f"{band_lbl:<16} {cnt_tot:<16,d} {f'{pct:.2f}%':<14} {cnt_129:<20d}")

    # 4. Single-account latency across >= 20 accounts (random seed 42)
    RANDOM_SEED = 42
    random.seed(RANDOM_SEED)
    all_acc_ids = [a["account_id"] for a in all_scores]
    sampled_20 = random.sample(all_acc_ids, 25)

    print(f"\nSingle-Account Latency Benchmark (25 Sampled Accounts, Random Seed = {RANDOM_SEED}):")
    latencies = []
    for acc in sampled_20:
        t_start = time.perf_counter()
        _ = engine.score_account(acc, con=con)
        lat = (time.perf_counter() - t_start) * 1000
        latencies.append(lat)

    print(f"  Sample size:        {len(sampled_20)} accounts")
    print(f"  Min latency:        {min(latencies):.2f} ms")
    print(f"  Median latency:     {sorted(latencies)[len(latencies)//2]:.2f} ms")
    print(f"  Average latency:    {sum(latencies)/len(latencies):.2f} ms")
    print(f"  Max latency:        {max(latencies):.2f} ms")

    # 5. High-score sample with evidence
    high_acc = next(a["account_id"] for a in all_scores if a["risk_score"] == 100)
    high_sample = engine.score_account(high_acc, con=con)

    print(f"\nSample High-Score Account: {high_sample['account_id']} (Risk Score: {high_sample['risk_score']})")
    print("Matched Indicators:")
    for m in high_sample["matched_indicators"]:
        print(f"  - [{m['indicator']}] (+{m['contribution']} pts): {m['basis']}")
    print("Evidence Transactions (Sample):")
    for ev in high_sample["evidence"][:4]:
        print(f"  row_id={ev['row_id']}, {ev['sender']} -> {ev['receiver']}, INR {ev['amount']:,.2f}, {ev['timestamp']}, reason: [{ev['reason']}]")

    # 6. Normal-account sample
    normal_acc = next(a["account_id"] for a in all_scores if a["risk_score"] == 0 and a["out_deg"] > 10)
    normal_sample = engine.score_account(normal_acc, con=con)
    print(f"\nSample Normal Account: {normal_sample['account_id']} (Risk Score: {normal_sample['risk_score']})")
    print(f"  In-degree: {normal_sample['metrics']['in_degree']}, Out-degree: {normal_sample['metrics']['out_degree']}, Out/In Ratio: {normal_sample['metrics']['out_in_ratio']}")
    print(f"  Matched indicators: {len(normal_sample['matched_indicators'])} (None)")

    con.close()

    meta_after = get_file_meta()
    print("\n------------------------------------------------------------------------------------------")
    print("STORAGE SAFETY VERIFICATION AFTER EXECUTION:")
    print(f"DuckDB After:   {meta_after['DuckDB']['size_bytes']:,} bytes | mtime: {meta_after['DuckDB']['mtime']}")
    print(f"Parquet After:  {meta_after['Parquet']['size_bytes']:,} bytes | mtime: {meta_after['Parquet']['mtime']}")
    db_ok = meta_before['DuckDB'] == meta_after['DuckDB']
    pq_ok = meta_before['Parquet'] == meta_after['Parquet']
    print(f"DuckDB Untouched: {db_ok} | Parquet Untouched: {pq_ok}")
    print("==========================================================================================")

if __name__ == "__main__":
    run_in_memory_tests()
    evaluate_real_dataset()
