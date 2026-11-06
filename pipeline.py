"""Sessionize -> redact -> label -> summarize UI event streams."""
import json, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime

from mock_llm import ALLOWED, label_session

INACTIVITY_S = 10 * 60
MAX_ATTEMPTS = 3
UNKNOWN = "unknown"
TEXT_KEYS = ("text",)

# ---------- helpers ----------
def load_events(path="events.jsonl"):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]

def ts_of(e):
    return datetime.fromisoformat(str(e["ts"]).replace("Z", "+00:00")).timestamp()

# ---------- 1. sessionize ----------
def sessionize(events, gap_s=INACTIVITY_S, context_gap_s=None):
    """Per user, sorted by time. New session when the gap is >= gap_s (10 min of
    inactivity has elapsed). Optional second rule (off by default): app change
    after a pause of >= context_gap_s. See README for why it is off."""
    by_user = defaultdict(list)
    for e in events:
        by_user[e["user_id"]].append(e)
    sessions = []
    for user, evs in by_user.items():
        evs.sort(key=ts_of)
        cur = [evs[0]]
        for prev, e in zip(evs, evs[1:]):
            gap = ts_of(e) - ts_of(prev)
            switched = (context_gap_s is not None and e["app"] != prev["app"]
                        and gap >= context_gap_s)
            if gap >= gap_s or switched:
                sessions.append({"user": user, "events": cur})
                cur = []
            cur.append(e)
        sessions.append({"user": user, "events": cur})
    for s in sessions:
        s["start"], s["end"] = ts_of(s["events"][0]), ts_of(s["events"][-1])
        s["duration_s"] = s["end"] - s["start"]
    return sessions

# ---------- 2. redact ----------
SENSITIVE_FIELDS = re.compile(
    r"(full\s*name|first\s*name|last\s*name|^name$|e-?mail|phone|mobile|address|"
    r"ssn|social security|dob|date of birth|\bcard\b|cvv|iban|passport|password)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
CARD = re.compile(r"(?<![\w-])\d(?:[ -]?\d){12,18}(?![\w-])")
PHONE = re.compile(r"(?<![\w-])\+?\(?\d[\d\s().-]{6,}\d(?![\w-])")
ID_CONTEXT = re.compile(r"(order|invoice|inv|ord|po|ref|#)\s*[:#]?\s*$", re.I)

def luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0

def redact_text(text):
    text = EMAIL.sub("[EMAIL]", text)
    def card(m):
        d = re.sub(r"\D", "", m.group())
        return "[CARD]" if 13 <= len(d) <= 19 and luhn_ok(d) else m.group()
    text = CARD.sub(card, text)
    def phone(m):
        if ID_CONTEXT.search(text[: m.start()]):      # "Order 5432 ..." survives
            return m.group()
        d = re.sub(r"\D", "", m.group())
        return "[PHONE]" if 7 <= len(d) <= 15 else m.group()
    return PHONE.sub(phone, text)

def redact_event(e):
    out = dict(e)
    if SENSITIVE_FIELDS.search(str(e.get("label", ""))):
        for k in TEXT_KEYS:
            if e.get(k):
                out[k] = "[REDACTED]"
    for k, v in out.items():
        if isinstance(v, str) and v != "[REDACTED]":
            out[k] = redact_text(v)
    return out

# ---------- 3. label ----------
def build_prompt(session):
    # NOTE: the mock keyword-matches the whole prompt ("vendor", "expense"), so the
    # label list is deliberately NOT inlined here. With a real model, put ALLOWED
    # in the system prompt. Validation enforces the set regardless.
    lines = []
    for e in map(redact_event, session["events"]):
        rel = int(ts_of(e) - session["start"])
        lines.append(f"+{rel}s [{e['app']}] {e['label']}: {e['text']}")
    return ("Classify this work session. Reply with JSON only: process (an allowed label), "
            "steps (list of short strings), confidence (number from 0 to 1).\nEvents:\n"
            + "\n".join(lines))

def _repair_truncated(s):
    """Close an unterminated string and any open brackets; drop a dangling key."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            esc = (ch == "\\" and not esc)
            if ch == '"' and not esc:
                in_str = False
        elif ch == '"': in_str = True
        elif ch in "{[": stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack: stack.pop()
    s = s + ('"' if in_str else "")
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r'"[^"]*"\s*:\s*$', "", s).rstrip().rstrip(",")
    return s + "".join(reversed(stack))

def extract_json(raw):
    if not isinstance(raw, str):
        return raw if isinstance(raw, dict) else None
    start = raw.find("{")
    if start < 0:
        return None
    end = raw.rfind("}")
    for cand in ([raw[start:end + 1]] if end > start else []) + [_repair_truncated(raw[start:])]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None

def _norm_label(x):
    return re.sub(r"[\s\-]+", "_", x.strip().lower())

def validate(obj):
    """Return a clean record or None. Never trusts the model's shape."""
    if not isinstance(obj, dict):
        return None
    allowed = {_norm_label(a): a for a in ALLOWED}
    proc = obj.get("process")
    if not isinstance(proc, str) or _norm_label(proc) not in allowed:
        return None
    steps = obj.get("steps")
    if not isinstance(steps, list) or not all(isinstance(x, str) for x in steps):
        return None
    conf = obj.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        return None
    return {"process": allowed[_norm_label(proc)], "steps": steps, "confidence": float(conf)}

def label(session, fn=None, max_attempts=MAX_ATTEMPTS):
    fn = fn or label_session
    prompt = build_prompt(session)
    for attempt in range(max_attempts):
        try:
            rec = validate(extract_json(fn(prompt, attempt)))
        except Exception:
            rec = None
        if rec:
            return {**rec, "attempts": attempt + 1, "fallback": False}
    return {"process": UNKNOWN, "steps": [], "confidence": 0.0,
            "attempts": max_attempts, "fallback": True}

# ---------- 4. summary ----------
def summarize(labeled):
    per = Counter(s["label"]["process"] for s in labeled)
    durs = [s["duration_s"] for s in labeled]
    retried = sum(1 for s in labeled if s["label"]["attempts"] > 1 and not s["label"]["fallback"])
    unknown = sum(1 for s in labeled if s["label"]["fallback"])
    lines = ["Sessions per process:"] + [f"  {p}: {n}" for p, n in per.most_common()]
    lines.append(f"Median session duration: {statistics.median(durs):.0f}s" if durs else "No sessions")
    lines.append(f"Sessions needing a retry (then succeeded): {retried}")
    lines.append(f"Sessions that fell back to unknown: {unknown}")
    return "\n".join(lines)

def run(events):
    labeled = []
    for s in sessionize(events):
        s["label"] = label(s)
        labeled.append(s)
    return labeled

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "events.jsonl"
    labeled = run(load_events(path))
    for s in labeled:
        print(s["user"], len(s["events"]), "events ->", s["label"]["process"],
              f"(attempts={s['label']['attempts']}, fallback={s['label']['fallback']})")
    print()
    print(summarize(labeled))
