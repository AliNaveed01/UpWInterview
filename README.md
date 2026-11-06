# Sessionize and label work activity

## Run
```
pip install pytest
python pipeline.py            # reads events.jsonl, prints per-session labels + summary
pytest -q
```
Files: `pipeline.py` (all logic), `mock_llm.py` (provided, untouched), `events.jsonl` (sample data), `test_pipeline.py`.

## Session rule
Events are grouped per `user_id`, sorted by time. A new session starts when the gap to the previous event is **>= 10 minutes**: "after 10 minutes of inactivity" means once 10 minutes of silence has elapsed. The sample data has a gap of exactly 10:00 (09:14:59 to 09:24:59), and I treat that as a boundary; it is covered by a test.

**Second rule: considered, deliberately off.** I implemented an optional "app change after a pause" rule (`context_gap_s`) but leave it disabled. In the sample data, the Outlook email in the middle of the NetSuite invoice task is part of the same process, so splitting on app change fragments real work. Switching apps mid-task is normal; inactivity is the more reliable signal. It stays available as a parameter and is tested.

## Redaction (before anything reaches the model)
- Emails, phone numbers, and 13-19 digit numbers that **pass Luhn** become `[EMAIL]`, `[PHONE]`, `[CARD]`.
- Values of sensitive fields (label matches Full name, Phone, Card, email, address, etc.) become `[REDACTED]` regardless of content.
- Order and invoice numbers survive: `INV-...`, `PO-...`, `ER-...` never match the patterns, and `Order 5432 1098 7654 3210` is left alone both because it fails Luhn and because digits right after "order/invoice/#" are treated as an ID.
- A test asserts that none of the sample PII appears in any prompt and that the IDs do.

## Handling the non-deterministic mock
Output is never trusted. Pipeline per reply: extract the JSON object (handles prose wrapper) -> repair truncation (close open strings and brackets, drop a dangling key) -> strict validation:
- `process` must be in ALLOWED (case/space/hyphen-insensitive, so "Vendor Onboarding" is repaired to `vendor_onboarding`)
- `steps` must be a list of strings
- `confidence` must be a real number in [0, 1]. Out-of-range values (1.7) are **rejected and retried**, not clamped, because clamping would invent confidence.

On any failure or exception: retry with a new seed (up to 3 attempts), then fall back to `{"process": "unknown", "steps": [], "confidence": 0}`. The pipeline never raises on model output. A truncated reply that lost `confidence` is repaired syntactically, then fails validation and is retried.

Note: the mock substring-matches the whole prompt, so the allowed label list is not inlined in the prompt (it would always trigger "vendor"). With a real model the labels would go in the system prompt.

## Summary output
Sessions per process, median session duration, number of sessions that needed a retry and then succeeded, and number that fell back to unknown.

## What I cut
CLI flags, async/batched model calls, a redaction audit log, and libphonenumber-grade phone parsing (phone detection is heuristic). Free-text names (e.g. a name typed into a non-sensitive field) are not detected.

## With more time
NER for free-text names, property-based tests for the JSON repair, logging raw model failures for evaluation, a confidence threshold that routes low-confidence labels to "unknown", and tuning the session rule on real data.
