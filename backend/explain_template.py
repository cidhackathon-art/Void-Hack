"""
Operation Abhedya-Chakra -- Deterministic Explanation Generator
Phase 3 - Step 1: Generates human-readable explanations directly from context JSON without LLM.
"""

from typing import Dict, Any

def generate_explanation(context: Dict[str, Any]) -> str:
    """
    Produces a factual, deterministic explanation from the self-contained context.
    For score == 0, explicitly states: 'no indicators matched'.
    Never uses forbidden words (e.g. criminal, fraudster, confirmed fraud).
    """
    account_id = context.get("account_id", "UNKNOWN")
    score = context.get("risk_score", 0)
    summary = context.get("account_summary", {})
    matched = context.get("matched_indicators", [])
    paths = context.get("observed_paths", [])
    limitations = context.get("limitations", [])

    lines = []

    # Header and Score
    lines.append(f"Account {account_id} has a deterministic risk score of {score}/100.")

    # Explicit condition for score == 0
    if score == 0:
        lines.append("Analysis determined that no indicators matched for this account.")
        in_deg = summary.get("in_degree", 0)
        out_deg = summary.get("out_degree", 0)
        ratio = summary.get("out_in_ratio")
        ratio_str = f"{ratio:.4f}" if ratio is not None else "N/A"
        lines.append(f"The account recorded {in_deg} incoming and {out_deg} outgoing transactions with a cumulative out/in amount ratio of {ratio_str}.")
        lines.append("Activity aligns with regular baseline distribution.")
    else:
        lines.append("The score is derived from the following matched indicators:")
        for m in matched:
            lines.append(f"- {m['indicator']} (+{m['contribution']} points): {m['basis']}.")

        # Metrics overview
        in_deg = summary.get("in_degree", 0)
        out_deg = summary.get("out_degree", 0)
        in_amt = summary.get("total_incoming_amount", 0.0)
        out_amt = summary.get("total_outgoing_amount", 0.0)
        ratio = summary.get("out_in_ratio")
        ratio_str = f"{ratio:.4f}" if ratio is not None else "N/A"
        lines.append(f"Summary metrics: in-degree {in_deg}, out-degree {out_deg}, total incoming INR {in_amt:,.2f}, total outgoing INR {out_amt:,.2f}, out/in ratio {ratio_str}.")

        # Observed paths
        if paths:
            lines.append(f"Identified {len(paths)} observed paths through this account. For example, observed path from row_id {paths[0]['in_row_id']} (INR {paths[0]['in_amount']:,.2f}) to row_id {paths[0]['out_row_id']} (INR {paths[0]['out_amount']:,.2f}) with time gap of {paths[0]['time_gap_seconds']:.0f} seconds.")

    # Limitations (Always included)
    if limitations:
        lines.append(f"Limitations: {limitations[0]} {limitations[1]}")
    else:
        lines.append("Limitations: No ground-truth labels exist in dataset; all findings represent observed behavioral patterns only. Possible flow is not proven movement of physical funds.")

    return "\n".join(lines)
