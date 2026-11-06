# AI usage

**Tool:** Claude (Anthropic) drafted the pipeline, tests, and README.

**Accepted:** overall structure (sessionize, redact, label, summarize), the Luhn check, the JSON extraction and truncation-repair approach, and most of the test cases.

**Corrected / overrode:**
- The first draft guessed the event schema; I replaced it with the real fields (`user_id`, `label`, `text`) and the real mock signature.
- The AI's first session design added an app-change split rule. On the sample data it fragmented the invoice session (the Outlook detour), so I turned it off by default and documented why.
- The first draft kept a gap of exactly 10:00 in the same session; I changed it to `>= 10 min` because the sample data has an exact-10-minute gap as a deliberate boundary.
- I caught that the mock keyword-matches the whole prompt, so listing the allowed labels in the prompt would force every session to "vendor_onboarding"; the label list is kept out of the prompt.
- Added normalization so "Vendor Onboarding" is repaired instead of wasting a retry.
- Two AI-written tests had wrong expectations (session sizes, truncation shape); I fixed the tests after confirming the pipeline behaviour was right.
