import json
from pipeline import (sessionize, redact_text, redact_event, build_prompt, extract_json,
                      validate, label, luhn_ok, load_events, run, summarize)

def ev(t, user="u", app="A", label="Search", text=""):
    return {"user_id": user, "ts": t, "app": app, "label": label, "text": text}

good = {"process": "invoice_processing", "steps": ["a"], "confidence": 0.7}

# ---- session boundary ----
def test_just_under_10_min_same_session():
    assert len(sessionize([ev("2026-01-01T09:00:00Z"), ev("2026-01-01T09:09:59Z")])) == 1

def test_exactly_10_min_starts_new_session():
    assert len(sessionize([ev("2026-01-01T09:00:00Z"), ev("2026-01-01T09:10:00Z")])) == 2

def test_over_10_min_splits():
    assert len(sessionize([ev("2026-01-01T09:00:00Z"), ev("2026-01-01T09:10:01Z")])) == 2

def test_users_separated_and_unsorted_input():
    t = lambda m: f"2026-01-01T09:0{m}:00Z"
    s = sessionize([ev(t(3), "b"), ev(t(0), "a"), ev(t(2), "a"), ev(t(0), "b")])
    assert sorted(len(x["events"]) for x in s) == [2, 2]

def test_duration():
    s = sessionize([ev("2026-01-01T09:00:00Z"), ev("2026-01-01T09:05:00Z")])[0]
    assert s["duration_s"] == 300

def test_app_switch_does_not_split_by_default():
    assert len(sessionize([ev("2026-01-01T09:00:00Z", app="A"),
                           ev("2026-01-01T09:05:00Z", app="B")])) == 1

def test_optional_context_rule():
    s = sessionize([ev("2026-01-01T09:00:00Z", app="A"), ev("2026-01-01T09:05:00Z", app="B")],
                   context_gap_s=120)
    assert len(s) == 2

def test_sample_data_sessions():
    s = sessionize(load_events())
    assert sorted(len(x["events"]) for x in s) == [1, 3, 3, 4]

# ---- redaction ----
def test_email():
    assert "@" not in redact_text("mail jane.doe+x@acme.co.uk now")

def test_luhn_card_redacted_non_luhn_kept():
    assert "[CARD]" in redact_text("Charged to 4539 1488 0343 6467")
    assert luhn_ok("4539148803436467") and not luhn_ok("5432109876543210")

def test_order_number_that_looks_like_card_survives():
    t = "Order 5432 1098 7654 3210 matched to PO-88213"
    assert redact_text(t) == t

def test_invoice_ids_survive():
    t = "Invoice INV-2026-000417 expense report ER-1182 order 12345678"
    assert redact_text(t) == t

def test_phone():
    assert "[PHONE]" in redact_text("call +91 98450 12345")

def test_sensitive_field_value():
    assert redact_event(ev("2026-01-01T09:00:00Z", label="Full name", text="Rahul Mehta"))["text"] == "[REDACTED]"

def test_no_pii_reaches_prompt_on_sample_data():
    prompts = " ".join(build_prompt(s) for s in sessionize(load_events()))
    for leak in ["Rahul", "Mehta", "98450", "ap-team@", "4539 1488", "acme-example"]:
        assert leak not in prompts
    for keep in ["INV-2026-000417", "PO-88213", "5432 1098 7654 3210", "ER-1182"]:
        assert keep in prompts

# ---- output validation ----
def test_prose_wrapped():
    assert validate(extract_json("Sure! Here:\n" + json.dumps(good))) is not None

def test_truncated_repaired_then_rejected_for_missing_field():
    mock_style = {"process": "invoice_processing", "steps": ["a"], "confidence": 0.82}
    obj = extract_json(json.dumps(mock_style)[:-6])   # same cut the mock makes
    assert obj is not None and validate(obj) is None   # lost confidence -> retry

def test_titlecase_label_normalized():
    assert validate({**good, "process": "Invoice Processing"})["process"] == "invoice_processing"

def test_bad_label_rejected():
    assert validate({**good, "process": "astrology"}) is None

def test_confidence_out_of_range_rejected():
    for c in (1.7, -0.1, True, "0.8"):
        assert validate({**good, "confidence": c}) is None

def test_steps_must_be_strings():
    assert validate({**good, "steps": [1, 2]}) is None

def test_retry_then_success():
    replies = iter(["garbage", json.dumps(good)])
    r = label({"events": [ev("2026-01-01T09:00:00Z")], "start": 0}, fn=lambda p, s: next(replies))
    assert r["process"] == "invoice_processing" and r["attempts"] == 2 and not r["fallback"]

def test_fallback_unknown_never_crashes():
    def boom(p, s): raise RuntimeError("model down")
    r = label({"events": [ev("2026-01-01T09:00:00Z")], "start": 0}, fn=boom)
    assert r["process"] == "unknown" and r["fallback"] and r["attempts"] == 3

def test_end_to_end_with_real_mock_never_crashes():
    labeled = run(load_events())
    assert len(labeled) == 4
    assert all(s["label"]["process"] in
               ["invoice_processing", "vendor_onboarding", "expense_review", "unknown"] for s in labeled)
    assert "Median" in summarize(labeled)
