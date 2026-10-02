from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .db import get_database_stats, get_db_cursor
from .data_access import (
    account_exists,
    get_account_summary,
    get_account_transactions
)

app = FastAPI(
    title="Operation Abhedya-Chakra — Forensic Data API",
    description="Phase 1: Local High-Performance Banking Transaction Data Access Layer",
    version="1.0.0"
)

# Enable CORS for local frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check() -> Dict[str, Any]:
    """
    Health check endpoint returning system status and DuckDB storage telemetry.
    """
    db_stats = get_database_stats()
    return {
        "status": "healthy" if db_stats.get("status") == "ready" else "degraded",
        "phase": "Phase 1: Data Foundation & Setup",
        "engine": "DuckDB Columnar Engine",
        "database": db_stats
    }

@app.get("/account/{account_id}")
def get_account(account_id: str) -> Dict[str, Any]:
    """
    Retrieves account existence status, IFSC mapping, and aggregate flow summary.
    """
    summary = get_account_summary(account_id)
    if not summary:
        raise HTTPException(
            status_code=404,
            detail=f"Account '{account_id}' not found in transaction records."
        )
    return summary

@app.get("/account/{account_id}/transactions")
def get_transactions(
    account_id: str,
    direction: str = Query("all", pattern="^(in|out|all)$", description="Filter direction: 'in', 'out', or 'all'"),
    start_time: Optional[str] = Query(None, description="ISO format start timestamp filter (e.g. 2026-09-22 00:00:00)"),
    end_time: Optional[str] = Query(None, description="ISO format end timestamp filter"),
    limit: int = Query(100, ge=1, le=1000, description="Max transactions to return (default 100, max 1000)")
) -> Dict[str, Any]:
    """
    Retrieves incoming, outgoing, or bidirectional transactions for an account with optional temporal filtering.
    """
    if not account_exists(account_id):
        raise HTTPException(
            status_code=404,
            detail=f"Account '{account_id}' not found in transaction records."
        )

    # Clean params if invoked directly as Python function without FastAPI DI
    safe_start = start_time if isinstance(start_time, str) else None
    safe_end = end_time if isinstance(end_time, str) else None
    safe_limit = limit if isinstance(limit, int) else 100
    safe_dir = direction if isinstance(direction, str) else "all"

    txns = get_account_transactions(
        account_id=account_id,
        direction=safe_dir,
        start_time=safe_start,
        end_time=safe_end,
        limit=safe_limit
    )

    return {
        "account_id": account_id,
        "direction": safe_dir,
        "returned_count": len(txns),
        "transactions": txns
    }

# =============================================================================
# Phase 2 & 3: Detection, Explanation & Forensics API Endpoints
# =============================================================================

from .detect import DetectionEngine
from .context import build_context
from .explain_template import generate_explanation
from .guardrails import validate
from .trace import trace_onward_flow

_detection_engine: Optional[DetectionEngine] = None

def get_engine() -> DetectionEngine:
    global _detection_engine
    if _detection_engine is None:
        _detection_engine = DetectionEngine()
    return _detection_engine

@app.get("/api/detect/{account_id}")
def detect_account(account_id: str) -> Dict[str, Any]:
    """
    Computes deterministic 0-100 risk score and indicator-grouped evidence for an account.
    """
    if not account_exists(account_id):
        raise HTTPException(
            status_code=404,
            detail=f"Account '{account_id}' not found in transaction records."
        )
    engine = get_engine()
    return engine.score_account(account_id)

@app.get("/api/explain/{account_id}")
def explain_account(account_id: str) -> Dict[str, Any]:
    """
    Constructs self-contained evidence context, generates deterministic explanation,
    and runs grounded guardrail validation.
    """
    if not account_exists(account_id):
        raise HTTPException(
            status_code=404,
            detail=f"Account '{account_id}' not found in transaction records."
        )
    context = build_context(account_id)
    explanation = generate_explanation(context)
    guardrail_result = validate(explanation, context)

    return {
        "account_id": account_id,
        "risk_score": context.get("risk_score", 0),
        "explanation": explanation,
        "guardrail_status": guardrail_result,
        "context": context
    }

@app.get("/api/trace/{row_id}")
def trace_transaction(
    row_id: int,
    max_hops: int = Query(4, ge=1, le=4, description="Max onward hops (1 to 4)"),
    cap: Optional[int] = Query(None, description="Frontier branch cap per hop")
) -> Dict[str, Any]:
    """
    Traces possible onward flow graph from an explicit starting transaction row_id.
    """
    try:
        trace_result = trace_onward_flow(start_row_id=row_id, max_hops=max_hops, cap=cap)
        return trace_result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Trace traversal error: {str(e)}"
        )

@app.get("/api/flagged")
def list_flagged_accounts(
    min_score: int = Query(30, ge=0, le=100, description="Minimum risk score filter"),
    max_score: Optional[int] = Query(None, ge=0, le=100, description="Maximum risk score filter"),
    exact_score: Optional[int] = Query(None, ge=0, le=100, description="Exact risk score filter"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(50, ge=1, le=1000, description="Max accounts to return per page")
) -> Dict[str, Any]:
    """
    Retrieves highest-risk accounts meeting score criteria (min_score, max_score, or exact_score)
    with pagination support.
    """
    engine = get_engine()
    all_scores = engine.score_all_accounts_fast()

    if exact_score is not None:
        filtered = [a for a in all_scores if a["risk_score"] == exact_score]
    else:
        filtered = [
            a for a in all_scores
            if a["risk_score"] >= min_score and (max_score is None or a["risk_score"] <= max_score)
        ]

    filtered.sort(key=lambda x: (-x["risk_score"], x["account_id"]))
    total_matching = len(filtered)
    start_idx = (page - 1) * limit
    returned_accounts = filtered[start_idx : start_idx + limit]

    # Enrich returned accounts with matched indicator names
    indicator_map = [
        ("ind_pt", "pass_through"),
        ("ind_fo", "fan_out"),
        ("ind_fpt", "fast_pass_through"),
        ("ind_rare", "rare_infrastructure"),
        ("ind_sink", "sink"),
    ]
    for acc in returned_accounts:
        if "matched_indicator_names" not in acc:
            acc["matched_indicator_names"] = [
                name for flag, name in indicator_map if acc.get(flag)
            ]

    # Exact tier totals across all accounts >= 30
    c_100 = sum(1 for a in all_scores if a["risk_score"] == 100)
    c_50 = sum(1 for a in all_scores if a["risk_score"] == 50)
    c_40 = sum(1 for a in all_scores if a["risk_score"] == 40)
    c_30 = sum(1 for a in all_scores if a["risk_score"] == 30)

    showing_from = start_idx + 1 if total_matching > 0 and len(returned_accounts) > 0 else 0
    showing_to = min(start_idx + len(returned_accounts), total_matching)

    return {
        "filter_min_score": min_score,
        "filter_max_score": max_score,
        "filter_exact_score": exact_score,
        "total_matching": total_matching,
        "page": page,
        "limit": limit,
        "showing_from": showing_from,
        "showing_to": showing_to,
        "returned_count": len(returned_accounts),
        "tier_summary": {
            "tier_100": c_100,
            "tier_50": c_50,
            "tier_40": c_40,
            "tier_30": c_30,
            "total_flagged": c_100 + c_50 + c_40 + c_30
        },
        "accounts": returned_accounts
    }

_case_overview_cache: Optional[Dict[str, Any]] = None

def compute_case_overview() -> Dict[str, Any]:
    global _case_overview_cache
    if _case_overview_cache is not None:
        return _case_overview_cache

    with get_db_cursor(read_only=True) as con:
        con.execute("""
            CREATE TEMP VIEW IF NOT EXISTS temp_case_scored AS
            WITH all_accounts AS (
                SELECT Sender_Account as acc FROM transactions
                UNION
                SELECT Receiver_Account as acc FROM transactions
            ),
            in_stats AS (
                SELECT 
                    Receiver_Account as acc,
                    count(*) as in_deg,
                    sum(Amount) as in_amt
                FROM transactions
                GROUP BY Receiver_Account
            ),
            out_stats AS (
                SELECT 
                    Sender_Account as acc,
                    count(*) as out_deg,
                    sum(Amount) as out_amt,
                    sum(CASE WHEN Device_Type IN ('Linux_Script', 'Web_Emulator') OR IP_Address LIKE '185.%' OR IP_Address LIKE '194.%' THEN 1 ELSE 0 END) as rare_infra_cnt
                FROM transactions
                GROUP BY Sender_Account
            ),
            max_in AS (
                SELECT Receiver_Account as acc, Amount, Timestamp,
                       row_number() over (partition by Receiver_Account order by Amount desc, Timestamp asc) as rn
                FROM transactions
            ),
            largest_in AS (
                SELECT acc, Amount as in_amt, Timestamp as in_ts
                FROM max_in WHERE rn = 1
            ),
            window_outs AS (
                SELECT 
                    lin.acc,
                    lin.in_amt,
                    sum(tout.Amount) as sum_out
                FROM largest_in lin
                JOIN transactions tout
                  ON lin.acc = tout.Sender_Account
                 AND tout.Timestamp > lin.in_ts
                 AND tout.Timestamp <= lin.in_ts + INTERVAL 900 SECOND
                GROUP BY lin.acc, lin.in_amt
            ),
            scored AS (
                SELECT 
                    a.acc,
                    coalesce(i.in_deg, 0) as in_deg,
                    coalesce(o.out_deg, 0) as out_deg,
                    coalesce(i.in_amt, 0.0) as in_amt,
                    coalesce(o.out_amt, 0.0) as out_amt,
                    CASE WHEN coalesce(o.out_amt, 0.0) / nullif(coalesce(i.in_amt, 0.0), 0) BETWEEN 0.9750 AND 0.9850 THEN 1 ELSE 0 END as ind_pt,
                    CASE WHEN coalesce(o.out_deg, 0) >= 3 AND coalesce(i.in_deg, 0) <= 8 AND coalesce(o.out_deg, 0) > coalesce(i.in_deg, 0) THEN 1 ELSE 0 END as ind_fo,
                    CASE WHEN wo.sum_out IS NOT NULL AND wo.sum_out >= 0.50 * lin.in_amt THEN 1 ELSE 0 END as ind_fpt,
                    CASE WHEN coalesce(o.rare_infra_cnt, 0) > 0 THEN 1 ELSE 0 END as ind_rare,
                    CASE WHEN coalesce(i.in_deg, 0) >= 1 AND coalesce(o.out_deg, 0) == 0 THEN 1 ELSE 0 END as ind_sink
                FROM all_accounts a
                LEFT JOIN in_stats i ON a.acc = i.acc
                LEFT JOIN out_stats o ON a.acc = o.acc
                LEFT JOIN largest_in lin ON a.acc = lin.acc
                LEFT JOIN window_outs wo ON a.acc = wo.acc
            )
            SELECT *,
            LEAST(100, CAST(ROUND(ind_pt * 30 + ind_fo * 20 + ind_fpt * 20 + ind_rare * 30 + ind_sink * 40) AS INT)) as risk_score
            FROM scored
        """)

        groups_raw = con.execute("""
            SELECT 
                'layer_1' as grp_id,
                'Layer 1' as name,
                'Accounts with risk score 100' as desc,
                COUNT(*) as account_count,
                ROUND(SUM(in_amt), 2) as total_incoming_inr,
                ROUND(SUM(out_amt), 2) as total_outgoing_inr
            FROM temp_case_scored
            WHERE risk_score = 100
            UNION ALL
            SELECT 
                'layer_2' as grp_id,
                'Layer 2' as name,
                'Accounts with rare infrastructure (score 50 & 30)' as desc,
                COUNT(*) as account_count,
                ROUND(SUM(in_amt), 2) as total_incoming_inr,
                ROUND(SUM(out_amt), 2) as total_outgoing_inr
            FROM temp_case_scored
            WHERE ind_rare = 1 AND risk_score != 100
            UNION ALL
            SELECT 
                'sinks' as grp_id,
                'Sinks' as name,
                'Terminal accounts with risk score 40' as desc,
                COUNT(*) as account_count,
                ROUND(SUM(in_amt), 2) as total_incoming_inr,
                ROUND(SUM(out_amt), 2) as total_outgoing_inr
            FROM temp_case_scored
            WHERE risk_score = 40
        """).fetchall()

        groups = []
        for g_id, g_name, g_desc, cnt, in_amt, out_amt in groups_raw:
            groups.append({
                "group_id": g_id,
                "name": g_name,
                "description": g_desc,
                "account_count": int(cnt),
                "total_incoming_inr": float(in_amt or 0.0),
                "total_outgoing_inr": float(out_amt or 0.0)
            })

        ranked_raw = con.execute("""
            SELECT 
                acc, risk_score, in_deg, out_deg,
                ROUND(in_amt, 2) as in_amt,
                ROUND(out_amt, 2) as out_amt,
                ind_pt, ind_fo, ind_fpt, ind_rare, ind_sink
            FROM temp_case_scored
            WHERE risk_score >= 30
            ORDER BY in_amt DESC, acc ASC
        """).fetchall()

        ranked_accounts = []
        for rank, r in enumerate(ranked_raw, 1):
            acc, score, in_deg, out_deg, in_amt, out_amt, pt, fo, fpt, rare, sink = r
            matched = []
            if pt: matched.append("pass_through")
            if fo: matched.append("fan_out")
            if fpt: matched.append("fast_pass_through")
            if rare: matched.append("rare_infrastructure")
            if sink: matched.append("sink")

            ranked_accounts.append({
                "rank": rank,
                "account_id": acc,
                "risk_score": int(score),
                "in_degree": int(in_deg),
                "out_degree": int(out_deg),
                "total_incoming_inr": float(in_amt or 0.0),
                "total_outgoing_inr": float(out_amt or 0.0),
                "matched_indicator_names": matched
            })

        _case_overview_cache = {
            "groups": groups,
            "ranked_accounts": ranked_accounts
        }
        return _case_overview_cache

@app.get("/api/case-overview")
def get_case_overview(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=1000, description="Max accounts to return per page")
) -> Dict[str, Any]:
    """
    Returns read-only case overview card metrics and exposure ranking:
    1. Three groups (Layer 1: score 100, Layer 2: rare_infrastructure but score != 100, Sinks: score 40).
    2. Ranked list of flagged accounts by total incoming INR labeled:
       'Possible exposure (money that passed through; not proven loss)'.
    """
    data = compute_case_overview()
    all_ranked = data["ranked_accounts"]
    total_matching = len(all_ranked)
    start_idx = (page - 1) * limit
    page_accounts = all_ranked[start_idx : start_idx + limit]

    showing_from = start_idx + 1 if total_matching > 0 and len(page_accounts) > 0 else 0
    showing_to = min(start_idx + len(page_accounts), total_matching)

    return {
        "groups": data["groups"],
        "exposure_ranking": {
            "label": "Possible exposure (money that passed through; not proven loss)",
            "total_matching": total_matching,
            "page": page,
            "limit": limit,
            "showing_from": showing_from,
            "showing_to": showing_to,
            "accounts": page_accounts
        }
    }

# Mount static frontend dashboard
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_frontend_dir), html=True), name="dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)

