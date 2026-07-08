# Test questions — Grand Oslo Hotel agent eval

20 inbound guest-email test cases with **gold-standard answers/actions**, used to test the
agent's PMS **behavior** (reads) and **mutation** (writes). Gold standards were computed
programmatically against `src/hotel_agent` (`pms.py`, `workflows.py`, `guardrails.py`) — not
guessed — so quotes, availability deltas, risk flags and new record ids are exact.

## Files

- `test_questions.json` — the dataset: metadata, tool reference, coverage map, and 20 `cases`.

## Coverage (maps to the three graded scenarios)

| Scenario | Meaning | Cases |
|----------|---------|-------|
| 1 — read-only | Plan uses read tools, **no** PMS write | TQ01–TQ07 |
| 2 — action + write | Full plan → approve → execute (a **mutation** happens) | TQ08–TQ14 |
| 3 — risky/blocked | Guardrail-flagged, **never** auto-executed, human in both modes | TQ15–TQ20 |

Within those: availability lookups, rate quotes, policy questions, booking-details lookup,
party-size filtering, sold-out edge; new booking (existing + new guest), flexible/breakfast
rates, date change, rate-plan change, refundable cancel, unavailable-room handling; refund on
non-refundable, cancel non-refundable, compensation demand, fee waiver, ambiguous message,
chargeback threat.

## Case schema

```jsonc
{
  "id": "TQ08",
  "title": "...",
  "scenario": 2,                       // 1 | 2 | 3
  "category": "write",                 // read_only | write | risky_blocked
  "run_mode": "auto",                  // mode used to exercise the case
  "email": "...inbound guest email...",
  "expected": {
    "parsed":   { "intent": "...", ... },        // structured fields from parse_email
    "risk":     { "risky": false, "flags": [] }, // guardrail decision + flag codes
    "read_tool_calls": [ { "tool": "...", "args": {...} } ],   // read-only calls (order-independent, subset)
    "plan_actions":    [ { "workflow": "...", "args": {...} } ], // gold write plan ([] = read-only)
    "pms_mutation":    { "writes": true, "reservations_count_delta": 1,
                         "reservation": {...}, "availability_changes": {...} },
    "approval":     "auto_approved | human_required | blocked_pending_human",
    "final_status": "completed | awaiting_approval | completed_with_errors",
    "reply_must_include": ["..."],     // case-insensitive substrings the reply should contain
    "reply_must_not":     ["..."]      // behaviors the reply must NOT exhibit
  },
  "asserts": "one line: what this case proves",
  "known_gap": "optional: where current code diverges from the gold standard + a fix"
}
```

Matching rules (see `matching_notes` in the JSON): `read_tool_calls` is a minimal,
order-independent subset (extra reads are fine); `plan_actions` matches on workflow + key args;
`pms_mutation.writes:false` means the PMS must be unchanged; reply checks are case-insensitive
substrings.

## Key gold-standard values (verified against the code)

- **Quotes** — TQ02 Superior Double + breakfast, 2 nights = **6000 NOK**; TQ08 = **3600**;
  TQ09 Junior Suite + breakfast = **8600**; TQ10 flexible 1 night = **2070**; TQ12 rate-plan
  switch recomputes to **6900**.
- **New records** — each write case runs on a fresh copy of the PMS, so a new booking is
  always `RES007` and a new guest is always `G006`.
- **Blocked in auto** — every Scenario-3 case has `approval: blocked_pending_human` and
  `pms_mutation.writes:false`, proving risky requests never auto-execute.

## Two findings surfaced by the set

- **TQ05** — a pure booking-details lookup has no dedicated intent, so it falls to `other`,
  which the guardrail treats as `ambiguous` and escalates. The gold standard is the desired
  read-only answer; `known_gap` recommends adding a `reservation_lookup` intent.
- **TQ16** — `financial_request` fires on "cancel my **non-refundable** booking" because
  `refund` is a substring of `non-refundable`. Harmless (still correctly blocked) but noted.

## Running against the agent

Each case can be replayed through the CLI, comparing output to `expected`:

```bash
# read-only (Scenario 1)
python -m hotel_agent -e "Hi, do you have any rooms available from 20 to 22 April?" --show-plan

# write (Scenario 2), autonomous, no real mutation
python -m hotel_agent -e "<TQ08 email>" --mode auto --dry-run --json

# risky (Scenario 3) — must pause even in auto mode
python -m hotel_agent -e "<TQ15 email>" --mode auto --json
```

Use `--json` for machine-readable output to diff against the gold standard, and `--dry-run`
to check mutations without committing them. The deterministic parts (`risk.flags`,
`plan_actions`, `pms_mutation`) can be asserted exactly; the free-text reply is checked with
`reply_must_include` / `reply_must_not`.
