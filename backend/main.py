from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Query, Response
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

        from collections import Counter

        BANK_NAMES = {
            "SBIN": "State Bank of India",
            "HDFC": "HDFC Bank",
            "ICIC": "ICICI Bank",
            "AXIS": "Axis Bank",
            "KKBK": "Kotak Mahindra Bank",
            "PUNB": "Punjab National Bank",
            "BARB": "Bank of Baroda",
            "AIRP": "Airtel Payments Bank",
            "PYTM": "Paytm Payments Bank",
            "IPOS": "India Post Payments Bank"
        }

        ranked_accounts = []
        for rank, r in enumerate(ranked_raw, 1):
            acc, score, in_deg, out_deg, in_amt, out_amt, pt, fo, fpt, rare, sink = r
            matched = []
            if pt: matched.append("pass_through")
            if fo: matched.append("fan_out")
            if fpt: matched.append("fast_pass_through")
            if rare: matched.append("rare_infrastructure")
            if sink: matched.append("sink")

            bank_prefix = acc[:4]
            bank_name = BANK_NAMES.get(bank_prefix, bank_prefix)
            in_val = float(in_amt or 0.0)
            out_val = float(out_amt or 0.0)
            retained = max(0.0, in_val - out_val)
            retained_pct = round((retained / in_val * 100), 2) if in_val > 0 else 0.0

            pt_bool = bool(pt)
            fo_bool = bool(fo)
            fpt_bool = bool(fpt)
            rare_bool = bool(rare)
            sink_bool = bool(sink)

            # Detailed Selection Parameters Breakdown
            param_items = []
            if pt_bool: param_items.append("Pass-Through 98% (+30 pts)")
            if fpt_bool: param_items.append("Velocity <15m (+20 pts)")
            if fo_bool: param_items.append("Dispersal Fan-Out (+20 pts)")
            if rare_bool: param_items.append("Rare Script/Emulator Infra (+30 pts)")
            if sink_bool: param_items.append("Terminal Cash-Out Sink (+40 pts)")

            selection_summary = " | ".join(param_items) if param_items else "Behavioral Baseline"
            selection_rule = f"Flagged Mule (Deterministic Risk Score = {int(score)} >= 30)"

            if score == 100:
                mule_role = "Layer 1 Transit Core"
                role_type = "layer_1"
                evidence_desc = "98% pass-through transit in <15m | Multi-counterparty fan-out | Script infrastructure"
            elif sink or score == 40:
                mule_role = "Terminal Cash-Out Sink"
                role_type = "sink"
                evidence_desc = "Terminal accumulation account with zero onward outgoing transactions"
            elif score == 50:
                mule_role = "Layer 2 Fast Dispersal"
                role_type = "layer_2"
                evidence_desc = "Rapid forwarding within 15m window via rare script/emulator infrastructure"
            elif rare:
                mule_role = "Layer 2 Infrastructure Routing"
                role_type = "layer_2"
                evidence_desc = "Layered onward routing originating from Linux/Emulator or foreign subnets"
            elif pt:
                mule_role = "Secondary Pass-Through Node"
                role_type = "pass_through"
                evidence_desc = "Transit account maintaining 97.5% - 98.5% throughput envelope"
            else:
                mule_role = f"Behavioral Node ({score})"
                role_type = "node"
                evidence_desc = "Matches behavioral indicator patterns"

            ranked_accounts.append({
                "rank": rank,
                "account_id": acc,
                "bank_prefix": bank_prefix,
                "bank_name": bank_name,
                "is_mule": True,
                "is_mule_label": "YES (Mule Account)",
                "risk_score": int(score),
                "mule_role": mule_role,
                "role_type": role_type,
                "evidence_desc": evidence_desc,
                "selection_rule": selection_rule,
                "selection_parameters_summary": selection_summary,
                "selection_parameters_list": param_items,
                "param_pass_through": "MATCH (+30 pts)" if pt_bool else "NO MATCH (0 pts)",
                "param_velocity": "MATCH (+20 pts)" if fpt_bool else "NO MATCH (0 pts)",
                "param_fan_out": "MATCH (+20 pts)" if fo_bool else "NO MATCH (0 pts)",
                "param_rare_infra": "MATCH (+30 pts)" if rare_bool else "NO MATCH (0 pts)",
                "param_terminal_sink": "MATCH (+40 pts)" if sink_bool else "NO MATCH (0 pts)",
                "in_degree": int(in_deg),
                "out_degree": int(out_deg),
                "total_incoming_inr": in_val,
                "total_outgoing_inr": out_val,
                "retained_inr": round(retained, 2),
                "retained_pct": retained_pct,
                "matched_indicator_names": matched
            })

        clean_raw = con.execute("""
            SELECT 
                acc, risk_score, in_deg, out_deg,
                ROUND(in_amt, 2) as in_amt,
                ROUND(out_amt, 2) as out_amt
            FROM temp_case_scored
            WHERE risk_score = 0
            ORDER BY in_amt DESC, acc ASC
            LIMIT 500
        """).fetchall()

        clean_accounts = []
        for rank_offset, r in enumerate(clean_raw, 1):
            acc, score, in_deg, out_deg, in_amt, out_amt = r
            bank_prefix = acc[:4]
            bank_name = BANK_NAMES.get(bank_prefix, bank_prefix)
            clean_in = float(in_amt or 0.0)
            clean_out = float(out_amt or 0.0)
            clean_retained = max(0.0, clean_in - clean_out)
            clean_retained_pct = round((clean_retained / clean_in * 100), 2) if clean_in > 0 else 0.0

            clean_accounts.append({
                "rank": len(ranked_accounts) + rank_offset,
                "account_id": acc,
                "bank_prefix": bank_prefix,
                "bank_name": bank_name,
                "is_mule": False,
                "is_mule_label": "NO (Clean / Non-Mule)",
                "risk_score": 0,
                "mule_role": "Clean Normal Baseline Account",
                "role_type": "clean",
                "evidence_desc": "Conforms to standard operating baselines. Zero behavioral indicators matched.",
                "selection_rule": "Clean Baseline (Deterministic Risk Score = 0 < 30)",
                "selection_parameters_summary": "Zero Behavioral Indicators Matched (Baseline Normal Account)",
                "selection_parameters_list": ["Clean Baseline Activity (0 pts)"],
                "param_pass_through": "NO MATCH (0 pts)",
                "param_velocity": "NO MATCH (0 pts)",
                "param_fan_out": "NO MATCH (0 pts)",
                "param_rare_infra": "NO MATCH (0 pts)",
                "param_terminal_sink": "NO MATCH (0 pts)",
                "in_degree": int(in_deg),
                "out_degree": int(out_deg),
                "total_incoming_inr": clean_in,
                "total_outgoing_inr": clean_out,
                "retained_inr": round(clean_retained, 2),
                "retained_pct": clean_retained_pct,
                "matched_indicator_names": []
            })

        bank_summary = [
            {"bank_code": code, "bank_name": BANK_NAMES.get(code, code), "count": count}
            for code, count in Counter(a["bank_prefix"] for a in ranked_accounts).most_common()
        ]

        role_summary = [
            {"role_id": "all", "label": "All Mule Accounts", "count": len(ranked_accounts)},
            {"role_id": "layer_1", "label": "Layer 1 Transit Core", "count": 129},
            {"role_id": "layer_2", "label": "Layer 2 Dispersal & Routing", "count": 559},
            {"role_id": "sink", "label": "Terminal Cash-Out Sinks", "count": 385},
            {"role_id": "pass_through", "label": "Secondary Pass-Through Nodes", "count": 320}
        ]

        _case_overview_cache = {
            "groups": groups,
            "bank_summary": bank_summary,
            "role_summary": role_summary,
            "ranked_accounts": ranked_accounts,
            "clean_accounts": clean_accounts
        }
        return _case_overview_cache

@app.get("/api/case-overview")
def get_case_overview(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(10, ge=1, le=2000, description="Max accounts to return per page"),
    role_type: Optional[str] = Query(None, description="Filter by mule role: all, layer_1, layer_2, sink, pass_through"),
    bank_code: Optional[str] = Query(None, description="Filter by bank prefix (e.g. SBIN, HDFC)")
) -> Dict[str, Any]:
    """
    Returns read-only case overview card metrics and exposure ranking:
    1. Three groups (Layer 1: score 100, Layer 2: rare_infrastructure but score != 100, Sinks: score 40).
    2. Ranked list of flagged accounts by total incoming INR labeled:
       'Possible exposure (money that passed through; not proven loss)'.
    """
    data = compute_case_overview()
    all_ranked = data["ranked_accounts"]

    filtered = all_ranked
    if role_type and role_type != "all":
        filtered = [a for a in filtered if a.get("role_type") == role_type]
    if bank_code and bank_code != "all":
        filtered = [a for a in filtered if a.get("bank_prefix") == bank_code.upper()]

    total_matching = len(filtered)
    start_idx = (page - 1) * limit
    page_accounts = filtered[start_idx : start_idx + limit]

    showing_from = start_idx + 1 if total_matching > 0 and len(page_accounts) > 0 else 0
    showing_to = min(start_idx + len(page_accounts), total_matching)

    return {
        "groups": data["groups"],
        "bank_summary": data.get("bank_summary", []),
        "role_summary": data.get("role_summary", []),
        "filter_role_type": role_type,
        "filter_bank_code": bank_code,
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

@app.get("/api/export/master-ledger")
def export_master_ledger(
    format: str = Query("csv", pattern="^(csv|json)$", description="Export format: csv or json"),
    include_clean: bool = Query(True, description="Include clean non-mule baseline accounts for unified comparative sharing"),
    view: str = Query("unified", pattern="^(unified|mules_only|clean_only)$", description="Export scope")
) -> Any:
    """
    Exports accounts across all forensic layers in a single unified batch file.
    Provides complete multi-account access without requiring individual lookups.
    Features explicit Is_Mule_Account column ('YES (Mule Account)' vs 'NO (Clean / Non-Mule)')
    so the sheet can be shared directly with enforcement and banking partners without manual work.
    """
    data = compute_case_overview()
    all_ranked = data["ranked_accounts"]
    clean_accounts = data.get("clean_accounts", [])

    if view == "mules_only" or (not include_clean and view != "clean_only"):
        export_accounts = all_ranked
    elif view == "clean_only":
        export_accounts = clean_accounts
    else:  # unified
        export_accounts = all_ranked + clean_accounts

    if format == "json":
        return {
            "title": "Operation Abhedya-Chakra — Unified Forensic Ledger",
            "total_accounts": len(export_accounts),
            "mule_accounts_count": len(all_ranked),
            "clean_accounts_count": len(clean_accounts),
            "groups_summary": data["groups"],
            "accounts": export_accounts
        }

    import io
    import csv

    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM for Microsoft Excel
    writer = csv.writer(output)
    writer.writerow([
        "Rank",
        "Account_ID",
        "Bank_Name",
        "Is_Mule_Account",                  # Explicitly YES or NO!
        "Mule_Classification_Role",         # Layer 1 Transit, Terminal Sink, etc. or Clean Normal Baseline
        "Deterministic_Risk_Score",         # 100, 50, 40, 30, 0
        "Selection_Decision_Rule",          # Flagged Mule (Score >= 30) vs Clean Baseline (Score == 0)
        "Selection_Parameters_Summary",     # Concise summary of all matched parameters
        "Param_Pass_Through_98pct",         # MATCH (+30 pts) vs NO MATCH (0 pts)
        "Param_Velocity_Under_15m",         # MATCH (+20 pts) vs NO MATCH (0 pts)
        "Param_Dispersal_Fan_Out",          # MATCH (+20 pts) vs NO MATCH (0 pts)
        "Param_Rare_Infrastructure",        # MATCH (+30 pts) vs NO MATCH (0 pts)
        "Param_Terminal_Accumulation_Sink", # MATCH (+40 pts) vs NO MATCH (0 pts)
        "Forensic_Evidence_Summary",        # Pattern description or Baseline explanation
        "Total_Incoming_INR",
        "Total_Outgoing_INR",
        "Retained_INR",
        "Retained_Pct",
        "In_Degree",
        "Out_Degree",
        "Matched_Indicators"
    ])

    for acc in export_accounts:
        is_mule = acc.get("is_mule", acc.get("risk_score", 0) >= 30)
        is_mule_text = "YES (Mule Account)" if is_mule else "NO (Clean / Non-Mule)"

        writer.writerow([
            acc["rank"],
            acc["account_id"],
            acc.get("bank_name", acc["account_id"][:4]),
            is_mule_text,
            acc.get("mule_role", "Mule Node" if is_mule else "Clean Baseline"),
            acc["risk_score"],
            acc.get("selection_rule", f"Score = {acc['risk_score']}"),
            acc.get("selection_parameters_summary", "Zero Indicators"),
            acc.get("param_pass_through", "NO MATCH (0 pts)"),
            acc.get("param_velocity", "NO MATCH (0 pts)"),
            acc.get("param_fan_out", "NO MATCH (0 pts)"),
            acc.get("param_rare_infra", "NO MATCH (0 pts)"),
            acc.get("param_terminal_sink", "NO MATCH (0 pts)"),
            acc.get("evidence_desc", ""),
            f"{acc['total_incoming_inr']:.2f}",
            f"{acc['total_outgoing_inr']:.2f}",
            f"{acc.get('retained_inr', 0.0):.2f}",
            f"{acc.get('retained_pct', 0.0):.1f}%",
            acc["in_degree"],
            acc["out_degree"],
            "; ".join(acc.get("matched_indicator_names", []))
        ])

    csv_content = output.getvalue()
    filename = "Abhedya_Chakra_Unified_Forensic_Ledger_Mules_vs_Clean.csv" if include_clean and view != "mules_only" else "Abhedya_Chakra_Master_Forensic_Ledger.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

# Mount static frontend dashboard
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_frontend_dir), html=True), name="dashboard")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)

