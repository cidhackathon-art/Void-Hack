"""
Operation Abhedya-Chakra -- LLM Explanation Layer
Phase 3 - Step 2: Generates narrative explanations grounded in context JSON with deterministic fallback and guardrails.

Strict Constraints:
- Receives ONLY context JSON produced by backend/context.py.
- Uses local Ollama instance (qwen2.5:0.5b).
- Enforces strict zero-hallucination prompt.
- Validates every response using guardrails.validate.
- Seamlessly falls back to deterministic template if unavailable, malformed, or guardrail-rejected.
"""

import json
import time
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

from backend.explain_template import generate_explanation as generate_deterministic_explanation
from backend.guardrails import validate as validate_guardrails

DEFAULT_OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:0.5b"

SYSTEM_PROMPT = """You are a forensic financial auditing assistant for Operation Abhedya-Chakra.
Your task is to write a concise factual summary explaining an account's risk score using SOLELY the provided Context JSON.

REQUIRED STRUCTURE:
1. State the exact account and risk score: "Account <account_id> has a risk score of <risk_score>/100." (If score is 0, state: "Account <account_id> has a risk score of 0/100 with no indicators matched.")
2. State the matched indicators and their point contributions from matched_indicators, if any.
3. State in-degree, out-degree, total incoming, and total outgoing from account_summary.
4. Conclude with the exact limitations from the context: "Limitations: No ground-truth labels exist in dataset; possible flow is not proven movement of physical funds."

STRICT FACTUAL RULES:
- The Context JSON is your ONLY source of truth. Do NOT invent, assume, or infer any unmentioned facts.
- Do NOT invent any numbers or row_ids. Mention ONLY values present in the Context JSON.
- Never use forbidden words: 'fraudster', 'criminal', 'mule', 'guilty', 'confirmed fraud', 'proves intent', or 'proven causality'.
- If mentioning paths, refer to them strictly as 'observed paths', never proven causality.
- Output ONLY the factual explanation."""

def _call_ollama(prompt: str, model: str = DEFAULT_MODEL, url: str = DEFAULT_OLLAMA_URL, timeout_s: float = 20.0) -> Optional[str]:
    """Helper to query local Ollama HTTP endpoint."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "top_p": 0.1,
            "stop": ["\n\n\n", "Conclusion", "In conclusion"]
        }
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            return res_json.get("response", "").strip()
    except Exception:
        return None

def _build_llm_prompt(context: Dict[str, Any]) -> str:
    acc = context.get("account_id", "UNKNOWN")
    score = context.get("risk_score", 0)
    matched = context.get("matched_indicators", [])
    acc_sum = context.get("account_summary", {})
    
    indicators = []
    for i in matched:
        ind_name = i.get("indicator") or i.get("name") or "indicator"
        pts = i.get("contribution", 0)
        indicators.append(f"{ind_name} (+{pts} pts)")
    matched_str = ", ".join(indicators) if indicators else "None"

    in_deg = acc_sum.get("in_degree", 0)
    out_deg = acc_sum.get("out_degree", 0)
    in_amt = acc_sum.get("total_incoming_amount") or acc_sum.get("total_incoming") or 0.0
    out_amt = acc_sum.get("total_outgoing_amount") or acc_sum.get("total_outgoing") or 0.0
    ratio = acc_sum.get("out_in_ratio")
    ratio_str = f"{ratio:.4f}" if isinstance(ratio, (int, float)) else "N/A"

    prompt = f"""Context JSON:
{json.dumps(context, indent=2)}

Task: Write a strictly factual forensic audit explanation for account {acc}.
Requirements:
- State: Account {acc} has a risk score of {score}/100{ ' with no indicators matched' if score == 0 else ''}.
- Matched indicators: {matched_str}.
- Summary: in-degree {in_deg}, out-degree {out_deg}, total incoming INR {in_amt:,.2f}, total outgoing INR {out_amt:,.2f}, out/in ratio {ratio_str}.
- State: Limitations: No ground-truth labels exist in dataset; all findings represent observed behavioral patterns only. Possible flow is not proven movement of physical funds.
- Stop immediately after the limitations sentence. Do not add conclusions, speculations, accusations, or unmentioned facts.

Forensic Explanation:"""
    return prompt

def generate_llm_explanation(
    context: Dict[str, Any],
    model: str = DEFAULT_MODEL,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    timeout_s: float = 20.0,
    force_fallback: bool = False
) -> Dict[str, Any]:
    """
    Generates an explanation using the local LLM, validated through guardrails.
    If LLM is unavailable, times out, malformed, or rejected by guardrails,
    falls back cleanly to explain_template.py.
    """
    t_start = time.perf_counter()
    violations = []
    raw_llm_text = None

    if not force_fallback:
        # Construct compact context prompt for LLM
        prompt = _build_llm_prompt(context)
        raw_llm_text = _call_ollama(prompt, model=model, url=ollama_url, timeout_s=timeout_s)

    # Check if LLM gave a valid non-empty response
    if raw_llm_text and len(raw_llm_text.strip()) > 0:
        # Validate LLM output through Guardrails
        g_res = validate_guardrails(raw_llm_text, context)
        if g_res["passed"]:
            total_lat = (time.perf_counter() - t_start) * 1000
            return {
                "explanation": raw_llm_text,
                "source": "llm",
                "guardrail_status": g_res,
                "violations": [],
                "latency_ms": round(total_lat, 2)
            }
        else:
            # Record why LLM output was rejected
            violations = g_res["violations"]

    # If LLM failed, timed out, was empty, or failed guardrails:
    fallback_text = generate_deterministic_explanation(context)
    fallback_g_res = validate_guardrails(fallback_text, context)
    total_lat = (time.perf_counter() - t_start) * 1000

    return {
        "explanation": fallback_text,
        "source": "deterministic_fallback",
        "guardrail_status": fallback_g_res,
        "violations": violations,
        "rejected_llm_text": raw_llm_text if raw_llm_text else None,
        "latency_ms": round(total_lat, 2)
    }
