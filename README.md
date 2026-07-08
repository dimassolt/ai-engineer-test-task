# Grand Oslo Hotel — AI Email Agent

An LLM agent that reads an inbound hotel guest email, decides what needs to happen, drafts
a reply, and executes actions against a mocked PMS — with a **human-approval mode** and a
**fully-autonomous mode** (where ambiguous/risky requests are still held for a human).

Built with **LangGraph** as a `Plan → Approve → Execute` graph. The PMS is a local JSON
file; email in is CLI/stdin; email out is a mock send.

---

## What it does — the three scenarios

| # | Example email | Behaviour |
|---|---------------|-----------|
| **1 · Read-only** | *"Any rooms free April 20–22?"* | Plans with **read-only** tools, replies with real availability/prices, **no write**. |
| **2 · Action + write** | *"Book a double with breakfast, Apr 20–22, 2 adults."* | Full workflow: guest lookup → create-if-new → price → create reservation → reply. |
| **3 · Risky** | *"Refund my non-refundable booking."* | **Flagged and never auto-executed** — routed to human review in *both* modes. |

---

## Quickstart

```bash
# 1. install (Python 3.11+)
python -m venv .venv && source .venv/bin/activate
pip install -e .                      # or: pip install -r requirements.txt

# 2. add at least one provider key
cp .env.example .env                  # then edit: OPENAI_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY

# 3. run
python -m hotel_agent -e "Any rooms free April 20-22 for 2?" --show-plan
```

`--provider auto` picks the first available key (order: anthropic → openai → gemini).

### CLI examples (these are the real commands used to validate the build)

```bash
# Scenario 1 — read-only lookup, print the plan + draft, then stop
python -m hotel_agent -e "Do you have rooms April 20-22 for 2 adults?" --show-plan

# Scenario 2 — human approval: plan pauses at the gate...
python -m hotel_agent -e "Book a Standard Double w/ breakfast, Apr 20-22, 2 adults. \
  Ola Nordmann, ola@example.com" --mode human --thread-id demo1
# ...review the printed plan, then approve (resumes from the SQLite checkpoint):
python -m hotel_agent --resume --thread-id demo1 --approve

# Scenario 2 — fully autonomous, but simulate (no real write/send)
python -m hotel_agent -e "Book a double w/ breakfast, Apr 20-22, 2 adults. me@example.com" \
  --mode auto --dry-run

# Scenario 3 — risky request is held for a human even in auto mode
python -m hotel_agent -e "I want a refund on my non-refundable booking RES002. \
  maria.gonzalez@email.com" --mode auto
```

Optional UI: `streamlit run src/hotel_agent/app_streamlit.py`

---

## Architecture

The same graph runs behind the CLI and the Streamlit UI, via `service.py`.

```
              ┌──────────── read-only tools ────────────┐
              │ find_availability · quote_rate · get_*   │
              ▼                                          │
 email → parse → classify → plan (ReAct) → approval_gate ─(approved)→ execute → finalize
                    │            │              │                        │
              risk pre-check  gathers facts   pauses?                writes via
              (guardrails)    + typed Plan    (mode + risk)          workflows + send
                                                 │
                                          (rejected) └──────────────→ finalize
```

**Nodes (the workflow):**
1. `parse` — LLM structured-output extraction of sender, dates, party size, intent.
2. `classify` — **deterministic** risk pre-check (guardrails). No LLM → reliable + testable.
3. `plan` — bounded **ReAct loop bound to read-only tools**, then commits a typed `Plan`
   (chosen workflows + args) and a draft reply. **No writes happen here.**
4. `approval_gate` — the autonomous-vs-human split (see decisions).
5. `execute` — runs approved **workflows** (writes) + mock send. Honors `--dry-run`.
6. `finalize` — sets the final status.

### Tools vs. skills/workflows (the part the task grades hardest)

- **Tools** (`tools/pms.py`) — *atomic* PMS operations, one responsibility each:
  `find_availability`, `quote_rate`, `get_guest`, `get_reservation`, `get_policy`
  (read) and `create_guest`, `create_reservation`, `modify_reservation`,
  `cancel_reservation` (write). Pure Python, no LLM — the rate/availability math is unit-tested
  against the seed data.
- **Skills / workflows** (`tools/workflows.py`) — *named, ordered recipes* over those tools
  that guarantee reliable multi-step execution. `make_reservation` is exactly the brief's
  example: **guest-lookup → create-if-new → price → create reservation**, returning an
  auditable step trace.

**The division of labour:** the LLM decides *which* workflow to run and with *what*
arguments (that's the `Plan`); the workflow code guarantees *how* it runs. We never hope the
model emits five tool calls in the right order — the recipe owns the ordering and validation.

### Two-tier tools, split by the approval gate

| Tier | Tools | When |
|------|-------|------|
| **Read** | availability, rates, guest, reservation, policy | Free during `plan` (bound to the LLM). |
| **Write** | create/modify/cancel reservation, send reply | Only via workflows in `execute`, **after** approval **and** a policy check. |

---

## Key design decisions

- **ReAct inside a Plan→Approve→Execute graph, not a single agent loop.** A hard gate
  between "propose" and "do" is what makes the human-in-the-loop and the risk block
  trustworthy, and it makes the plan inspectable before anything is written.

- **Dynamic `interrupt()` for the approval gate — *not* a static `interrupt_before`.**
  A static breakpoint pauses *unconditionally*, which breaks auto mode and can't react to a
  risk flag computed *inside* the run. A dynamic interrupt lets the pause be decided from
  state: **human mode always pauses; auto mode runs straight through *unless* the request was
  flagged risky**, which forces a human even in auto. This is the single most important
  behaviour in the brief, so it drove the design. *(This intentionally deviates from the
  original `interrupt_before` note in `CLAUDE.md`; see the Decision log in `PROGRESS.md`.)*

- **Guardrails are deterministic** (`policy/guardrails.py`), not left to the LLM. Scenario 3
  must be *provably* blocked, so risk detection uses two signals — refund/dispute keywords on
  the raw text, and the PMS fact that the booking is on a non-refundable rate — and the
  planner additionally strips any action from a flagged request (defense in depth; the
  workflows refuse non-refundable changes too).

- **State persists via a checkpointer keyed by `thread_id`.** A run paused at the gate is
  resumable later — even from a *different process* (SQLite backend) — which is how the
  two-command human-approval flow works. Tests use the in-memory backend.

- **Provider-agnostic** (`llm/providers.py`). A tiny key manager auto-selects OpenAI /
  Anthropic / Gemini via `init_chat_model`, so swapping providers never touches graph code.

- **Scope.** No FastAPI/web backend, no feedback store, no real email/PMS integration — the
  brief explicitly deprioritises those and warns against over-engineering. The CLI is the
  baseline; Streamlit is a thin optional client over the same `service.py`.

---

## Project layout

```
src/hotel_agent/
  cli.py            # argparse entrypoint (python -m hotel_agent)
  service.py        # run / resume orchestration (shared by CLI + UI)
  app_streamlit.py  # optional UI
  config.py         # settings + .env loading & key-name normalization
  llm/providers.py  # provider auto-select + init_chat_model
  graph/
    state.py        # AgentState + Plan/ParsedEmail schemas
    nodes.py        # parse / classify / plan / approval_gate / execute / finalize
    build.py        # StateGraph assembly + checkpointer
  tools/
    pms.py          # atomic read+write PMS operations (the "tools")
    read_tools.py   # read-only tools bound to the LLM for planning
    workflows.py    # composed multi-step "skills"
    mailer.py       # mock send_reply
  policy/guardrails.py  # deterministic risk classification (Scenario 3)
  prompts/          # one prompt per file (parser, planner)
tests/              # PMS + workflow + guardrail unit tests, and the 3 scenarios (offline)
```

---

## Testing

```bash
pytest            # 20 tests, fully offline (a scripted fake LLM — no API calls)
```

Coverage: PMS availability/rate math (verified against the seed reservations), workflow
ordering + policy guards, deterministic guardrails, and all three scenarios end-to-end
through the real graph — including both approval modes and the risky-in-auto pause.

---

## What I'd add next

- **Slot-filling for missing info** (e.g. no dates/party size) — a clarifying-reply branch
  instead of a best-effort plan.
- **Persist PMS writes** to a per-run snapshot file so state carries across CLI invocations
  (today writes are in-memory per run by design, never touching the source JSON).
- **Feedback capture** — log each approve/reject as a labelled example for eval / fine-tuning.
- **Richer availability** (partial-stay suggestions, alternative dates) and multi-room bookings.
- **A FastAPI surface** if a web/multi-user deployment were actually needed.
