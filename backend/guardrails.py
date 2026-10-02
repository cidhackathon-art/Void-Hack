"""
Operation Abhedya-Chakra -- Guardrails Validator
Phase 3 - Step 1: Validates generated narrative text against context JSON.

Checks:
a. Every number and row_id mentioned in text exists in context.
b. Forbidden phrases are rejected (fraudster, guilty, criminal, confirmed fraud, proves intent, etc.).
c. risk_score in text matches context exactly.
d. A limitations sentence is present.
"""

import re
from typing import Dict, Any, List, Set, Union

FORBIDDEN_PHRASES = [
    "fraudster",
    "guilty",
    "criminal",
    "confirmed fraud",
    "proves intent",
    "proven fraud",
    "convicted",
    "money launderer",
    "perpetrator",
    "culprit",
    "mule",
    "proven causality",
    "proves causality",
    "proves guilt",
    "proven path",
    "proven flow",
    "proven relationship"
]

def _extract_all_numbers_from_obj(obj: Any) -> Set[Union[int, float, str]]:
    """Recursively collects all numerical values from any nested dict/list/string."""
    nums = set()
    if isinstance(obj, (int, float)):
        nums.add(obj)
        # Add integer version if float is whole
        if isinstance(obj, float) and obj.is_integer():
            nums.add(int(obj))
    elif isinstance(obj, str):
        # Extract numbers from string values (timestamps, IPs, descriptions)
        clean_s = obj.replace(",", "")
        matches = re.findall(r"\b\d+(?:\.\d+)?\b", clean_s)
        for m in matches:
            if "." in m:
                try:
                    val = float(m)
                    nums.add(val)
                    if val.is_integer():
                        nums.add(int(val))
                except ValueError:
                    pass
            else:
                try:
                    nums.add(int(m))
                except ValueError:
                    pass
            nums.add(m)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            nums.update(_extract_all_numbers_from_obj(k))
            nums.update(_extract_all_numbers_from_obj(v))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            nums.update(_extract_all_numbers_from_obj(item))
    return nums

def validate(text: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates text against context.
    Returns:
    {
        "passed": bool,
        "violations": List[str]
    }
    """
    violations = []
    text_lower = text.lower()

    # b. Forbidden phrases check
    for phrase in FORBIDDEN_PHRASES:
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, text_lower):
            violations.append(f"Forbidden phrase found: '{phrase}'")

    # c. risk_score check
    context_score = context.get("risk_score")
    if context_score is not None:
        # Check if score is mentioned
        score_patterns = [
            r"risk\s+score\s+(?:is\s+|of\s+|:\s*)?(\d+)",
            r"score\s+(?:is\s+|of\s+|:\s*)?(\d+)(?:/100)?",
            r"(\d+)/100"
        ]
        found_scores = []
        for pat in score_patterns:
            matches = re.findall(pat, text_lower)
            for m in matches:
                try:
                    found_scores.append(int(m))
                except ValueError:
                    pass

        if found_scores:
            if any(s != context_score for s in found_scores if s <= 100):
                mismatches = [s for s in found_scores if s != context_score and s <= 100]
                violations.append(f"risk_score mismatch: text states {mismatches} but context score is {context_score}")
        else:
            violations.append(f"risk_score not stated in text (expected {context_score})")

    # d. Limitations sentence present check
    limitation_keywords = ["limitation", "no ground-truth", "not proven", "possible flow"]
    if not any(k in text_lower for k in limitation_keywords):
        violations.append("Missing required limitations sentence in text.")

    # a. Every number and row_id mentioned in text exists in context
    context_nums = _extract_all_numbers_from_obj(context)
    # Include standard benign syntactic numbers: 100 (for out of 100 scale)
    context_nums.add(100)
    context_nums.add("100")

    # Normalize text numbers: remove commas from numbers like 421,653.32
    # But leave alone non-numbers
    normalized_text = re.sub(r"(\d),(\d)", r"\1\2", text)
    text_num_matches = re.findall(r"\b\d+(?:\.\d+)?\b", normalized_text)

    for num_str in text_num_matches:
        is_float = "." in num_str
        try:
            val = float(num_str) if is_float else int(num_str)
        except ValueError:
            continue

        # Check if val exists in context
        matched = False
        if val in context_nums or num_str in context_nums:
            matched = True
        elif is_float and val.is_integer() and int(val) in context_nums:
            matched = True
        elif not is_float and float(val) in context_nums:
            matched = True
        else:
            # Check float tolerance (e.g. 0.98 vs 0.9800)
            for cn in context_nums:
                if isinstance(cn, (int, float)) and abs(cn - val) < 1e-4:
                    matched = True
                    break

        if not matched:
            violations.append(f"Invented or unverified number in text: '{num_str}' does not exist in context.")

    return {
        "passed": len(violations) == 0,
        "violations": violations
    }
