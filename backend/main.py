from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .db import get_database_stats
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

# Mount static frontend dashboard
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_frontend_dir), html=True), name="dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)

