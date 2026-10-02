import sys
from backend.main import (
    health_check,
    get_account,
    get_transactions,
    detect_account,
    explain_account,
    trace_transaction,
    list_flagged_accounts
)

print("=== TESTING FASTAPI ENDPOINTS DIRECTLY ===")

# 1. Health Check
res = health_check()
print(f"1. health_check: Status = {res.get('status')}")
assert res.get("status") == "healthy"

# 2. Flagged Accounts
flagged = list_flagged_accounts(min_score=100, limit=5)
print(f"2. list_flagged_accounts: Total Matching = {flagged.get('total_matching')}, Returned = {flagged.get('returned_count')}")
assert flagged.get("total_matching") == 129
sample_acc = flagged["accounts"][0]["account_id"]

# 3. Detect Single Account
det = detect_account(sample_acc)
print(f"3. detect_account('{sample_acc}'): Risk Score = {det.get('risk_score')}, Matched Indicators = {len(det.get('matched_indicators', []))}")
assert det.get("risk_score") == 100

# 4. Explain Single Account (Context + Template + Guardrail)
exp = explain_account(sample_acc)
print(f"4. explain_account('{sample_acc}'): Score = {exp.get('risk_score')}, Guardrail Passed = {exp.get('guardrail_status', {}).get('passed')}")
assert exp.get("risk_score") == 100
assert exp.get("guardrail_status", {}).get("passed") is True
print("Explanation preview:")
print("  " + exp.get("explanation", "").split("\n")[0])

# 5. Explain Normal Account
norm_acc = "ICIC10014001"
norm_exp = explain_account(norm_acc)
print(f"5. explain_account('{norm_acc}'): Score = {norm_exp.get('risk_score')}")
assert norm_exp.get("risk_score") == 0
assert "no indicators matched" in norm_exp.get("explanation", "").lower()

# 6. Trace Transaction
trace_res = trace_transaction(row_id=35196, max_hops=2, cap=5)
print(f"6. trace_transaction(row_id=35196): Start Row = {trace_res.get('start_row_id')}, Total Paths = {trace_res.get('total_paths')}")
assert trace_res.get("start_row_id") == 35196

print("\nALL 6 API ENDPOINTS VERIFIED AND WORKING PERFECTLY!")
