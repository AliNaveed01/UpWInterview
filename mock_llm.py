# mock_llm.py (stands in for a real model; do not edit)
import json, random

ALLOWED = ["invoice_processing", "vendor_onboarding", "expense_review", "unknown"]

def label_session(prompt: str, seed: int) -> str:
    rnd = random.Random(f"{seed}:{prompt}").random()
    p = prompt.lower()
    process = "vendor_onboarding" if "vendor" in p else "expense_review" if "expense" in p else "invoice_processing"
    out = {"process": process, "steps": ["open record", "review details", "submit"], "confidence": 0.82}
    if rnd < 0.15:
        return "Sure! Here is the JSON you asked for:\n" + json.dumps(out)
    if rnd < 0.25:
        return json.dumps(out)[:-6]
    if rnd < 0.32:
        return json.dumps({**out, "process": process.replace("_", " ").title()})
    if rnd < 0.37:
        return json.dumps({**out, "confidence": 1.7})
    return json.dumps(out)
