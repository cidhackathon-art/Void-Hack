import sys
from pathlib import Path
import duckdb

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.trace import trace_onward_flow, POSSIBLE_FLOW_DISCLAIMER

def setup_synthetic_db():
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
        )
    """)

    # Populate synthetic scenarios:
    # Chain:
    # row 1: VICTIM -> L1 (10:00:00) [Start, H1, size 1]
    # row 2: L1 -> L2 (10:05:00) [H2, L1 onward]
    # row 9: L1 -> BRANCH (10:06:00) [H2, L1 onward, duplicate TXN002]
    # row 10: L1 -> TIE_ACC (10:00:00) [same second tie as row 1 -> skipped]
    # row 3: L2 -> L3 (10:10:00) [H3, L2 onward]
    # row 7: L2 -> VICTIM (10:12:00) [H3 cycle returning to victim -> skipped]
    # row 8: L2 -> L1 (10:14:00) [H3 cycle returning to L1 -> skipped]
    # row 4: L3 -> L4 (10:15:00) [H4, L3 onward]
    # row 5: L4 -> L5 (10:20:00) [H5 candidate -> MUST NOT execute]
    # row 6: L5 -> L6 (10:25:00) [H6 candidate -> MUST NOT execute]
    # row 11: VICTIM2 -> DEADEND (10:00:00) [Dead end]
    sample_data = [
        (1, "TXN001", "ACC_VICTIM", "ACC_L1", "IFSC001", "IFSC002", 1000.0, "2026-09-20 10:00:00", "UPI", "Initial transfer", "10.0.0.1", "Android"),
        (2, "TXN002", "ACC_L1", "ACC_L2", "IFSC002", "IFSC003", 900.0, "2026-09-20 10:05:00", "IMPS", "Split 1", "10.0.0.2", "Web_Emulator"),
        (3, "TXN003", "ACC_L2", "ACC_L3", "IFSC003", "IFSC004", 800.0, "2026-09-20 10:10:00", "IMPS", "Split 2", "10.0.0.3", "Linux_Script"),
        (4, "TXN004", "ACC_L3", "ACC_L4", "IFSC004", "IFSC005", 700.0, "2026-09-20 10:15:00", "NEFT", "Layer 3", "10.0.0.4", "Android"),
        (5, "TXN005", "ACC_L4", "ACC_L5", "IFSC005", "IFSC006", 600.0, "2026-09-20 10:20:00", "RTGS", "Layer 4 candidate", "10.0.0.5", "Windows_Browser"),
        (6, "TXN006", "ACC_L5", "ACC_L6", "IFSC006", "IFSC007", 500.0, "2026-09-20 10:25:00", "UPI", "Hop 5 candidate", "10.0.0.6", "Android"),
        (7, "TXN007", "ACC_L2", "ACC_VICTIM", "IFSC003", "IFSC001", 100.0, "2026-09-20 10:12:00", "UPI", "Return to victim", "10.0.0.3", "Android"),
        (8, "TXN008", "ACC_L2", "ACC_L1", "IFSC003", "IFSC002", 50.0, "2026-09-20 10:14:00", "UPI", "Return to L1", "10.0.0.3", "Android"),
        (9, "TXN002", "ACC_L1", "ACC_BRANCH", "IFSC002", "IFSC008", 100.0, "2026-09-20 10:06:00", "UPI", "Shared TxnID", "10.0.0.2", "Android"),
        (10, "TXN010", "ACC_L1", "ACC_TIE", "IFSC002", "IFSC009", 50.0, "2026-09-20 10:00:00", "UPI", "Same timestamp tie", "10.0.0.2", "Android"),
        (11, "TXN011", "ACC_VICTIM2", "ACC_DEADEND", "IFSC001", "IFSC010", 300.0, "2026-09-20 10:00:00", "UPI", "No outgoing", "10.0.0.1", "Android"),
    ]

    for row in sample_data:
        con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row))

    return con

def run_all_tests():
    con = setup_synthetic_db()
    print("=================================================================")
    print("  OPERATION ABHEDYA-CHAKRA — TRACE ENGINE SYNTHETIC TEST SUITE")
    print("  Exact Hop Definition: H1=Start (size 1), H2=L1, H3=L2, H4=L3")
    print("=================================================================")

    tests_run = 0
    tests_passed = 0

    # Test 1: Normal Chain (Hop 1 / Start and Hop 2 / L1)
    tests_run += 1
    res1 = trace_onward_flow(start_row_id=1, con=con, max_hops=2)
    assert res1["start_row_id"] == 1
    assert res1["victim_account"] == "ACC_VICTIM"
    assert res1["frontier_sizes"]["H1"] == 1
    assert res1["frontier_sizes"]["H2"] == 2  # L2 and BRANCH
    assert res1["frontier_sizes"]["H3"] == 0  # not expanded beyond max_hops=2
    assert res1["frontier_sizes"]["H4"] == 0
    print("PASS: Test 1 — Normal chain expansion (H1=1, H2=2)")
    tests_passed += 1

    # Test 2: Full 4-hop chain (H1 -> H2 -> H3 -> H4)
    # H1: Start (size 1)
    # H2: L1 onward (size 2: Row 2 L2, Row 9 BRANCH)
    # H3: L2 onward (size 1: Row 3 L3)
    # H4: L3 onward (size 1: Row 4 L4)
    # Maximum trace = 4 transactions total
    tests_run += 1
    res4 = trace_onward_flow(start_row_id=1, con=con, max_hops=4)
    assert res4["frontier_sizes"]["H1"] == 1
    assert res4["frontier_sizes"]["H2"] == 2
    assert res4["frontier_sizes"]["H3"] == 1
    assert res4["frontier_sizes"]["H4"] == 1
    assert res4["frontier_sizes"][1] == 1
    assert res4["frontier_sizes"][2] == 2
    assert res4["frontier_sizes"][3] == 1
    assert res4["frontier_sizes"][4] == 1
    # Verify maximum path length is 4 transactions
    h4_paths = [p for p in res4["paths"] if p["hop_level"] == "H4"]
    assert len(h4_paths) == 1
    assert h4_paths[0]["transaction_count"] == 4
    assert h4_paths[0]["row_id_path"] == [1, 2, 3, 4]
    assert h4_paths[0]["account_path"] == ["ACC_VICTIM", "ACC_L1", "ACC_L2", "ACC_L3", "ACC_L4"]
    print("PASS: Test 2 — Full 4-hop chain properly reached (H1=1, H2=2, H3=1, H4=1, max txns=4)")
    tests_passed += 1

    # Test 3: 5th hop must NOT execute
    tests_run += 1
    res5 = trace_onward_flow(start_row_id=1, con=con, max_hops=5)  # requests 5, strictly capped at 4
    assert res5["max_hops_configured"] == 4
    assert "H5" not in res5["frontier_sizes"]
    assert 5 not in res5["frontier_sizes"]
    # Verify ACC_L5 (Row 5) and ACC_L6 (Row 6) are strictly NEVER visited or in any path
    all_accounts_visited = [acc for p in res5["paths"] for acc in p["account_path"]]
    assert "ACC_L5" not in all_accounts_visited
    assert "ACC_L6" not in all_accounts_visited
    for p in res5["paths"]:
        assert len(p["row_id_path"]) <= 4, "No path may exceed 4 transactions"
    print("PASS: Test 3 — 5th hop strictly rejected / never executed (max txns <= 4)")
    tests_passed += 1

    # Test 4: Cycle and skipped-cycle evidence
    tests_run += 1
    assert res4["skipped_cycles_count"] == 2
    cycle_reasons = [c["reason"] for c in res4["skipped_cycles"]]
    assert "account_cycle" in cycle_reasons
    # Check that returning to victim is recorded
    victim_cycle = next(c for c in res4["skipped_cycles"] if c["receiver_account"] == "ACC_VICTIM")
    assert victim_cycle["returning_to_victim"] is True
    print("PASS: Test 4 — Cycle detection & skipped cycle evidence recorded (victim + internal cycle)")
    tests_passed += 1

    # Test 5: Two rows with same Transaction_ID but different row_id
    tests_run += 1
    # Row 2 (TXN002, L2) and Row 9 (TXN002, BRANCH) both reached in H2
    h2_paths = [p for p in res4["paths"] if p["hop_level"] == "H2"]
    h2_row_ids = [p["row_id_path"][-1] for p in h2_paths]
    assert 2 in h2_row_ids
    assert 9 in h2_row_ids
    print("PASS: Test 5 — Multiple rows with duplicate TxnID tracked distinctively by row_id")
    tests_passed += 1

    # Test 6: Invalid row_id
    tests_run += 1
    try:
        trace_onward_flow(start_row_id=999999, con=con)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "not found" in str(e)
        print("PASS: Test 6 — Invalid row_id gracefully raises ValueError")
        tests_passed += 1

    # Test 7 & 8: Dead end / No outgoing transactions
    tests_run += 1
    res_dead = trace_onward_flow(start_row_id=11, con=con, max_hops=4)
    assert res_dead["total_paths_found"] == 0
    assert res_dead["frontier_sizes"]["H1"] == 1
    assert res_dead["frontier_sizes"]["H2"] == 0
    print("PASS: Test 7 & 8 — No outgoing transaction stops gracefully with 0 onward paths")
    tests_passed += 1

    # Test 9: Same-timestamp transaction must NOT be followed
    tests_run += 1
    # Row 10 has Timestamp 10:00:00 (equal to start txn timestamp 10:00:00)
    assert res4["skipped_timestamp_ties"] == 1
    assert "ACC_TIE" not in all_accounts_visited
    print("PASS: Test 9 — Same-timestamp candidate skipped and counted in skipped_timestamp_ties")
    tests_passed += 1

    # Test 10: Disclaimer strictly present
    tests_run += 1
    assert res4["disclaimer"] == POSSIBLE_FLOW_DISCLAIMER
    print("PASS: Test 10 — Standard 'possible onward flow' disclaimer verified")
    tests_passed += 1

    print("-----------------------------------------------------------------")
    print(f"RESULTS: {tests_passed} / {tests_run} tests PASSED (100% Success)")
    print("=================================================================")
    con.close()
    return True

if __name__ == "__main__":
    success = run_all_tests()
    if not success:
        sys.exit(1)
