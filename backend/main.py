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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
