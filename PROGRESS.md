# PROGRESS.md — build tracker

Single source of truth for build status. Read at the start of each session; update after
each meaningful change. See `CLAUDE.md` §6 for the protocol. Keep entries to one line each.

- **Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done
- **Current phase:** Phase 14 — Booking correctness: guardrail scope, details, availability `[x]` (remaining deliverable: walkthrough recording)
- **Last updated:** 2026-07-08

---

## Phases

### Phase 0 — Project setup  `[x]`
- [x] Repo scaffolding + layout from `CLAUDE.md` §5 (src/ layout, pyproject.toml)
- [x] `requirements.txt` + `pyproject.toml` (langgraph 1.x, langchain 1.x, providers, streamlit, pytest)
- [x] `.env.example` with provider keys
- [x] Copy `mock_hotel_data.json` into `data/`
- [x] `config.py` — load env + defaults + key-name normalization
- **Done:** `python -m hotel_agent --version` runs.

### Phase 1 — PMS layer + tools  `[x]`
- [x] Load PMS into memory; writes hit a copy, never the source file
- [x] Read tools: `find_availability`, `quote_rate`, `get_guest`, `get_reservation`, `get_policy`, `list_inventory`
- [x] Write ops: `create_guest`, `create_reservation`, `modify_reservation`, `cancel_reservation`, `send_reply` (mock)
- [x] Unit tests — availability + rate math verified against seed reservations
- **Done:** tools pass unit tests; math matches the JSON exactly.

### Phase 2 — LLM provider layer  `[x]`
- [x] `llm/providers.py` key manager: detect keys, pick first available (anthropic→openai→gemini)
- [x] Uniform `get_chat_model()` via `init_chat_model`; clear error if no key
- **Done:** verified live on Anthropic; provider is swappable without touching graph code.

### Phase 3 — LangGraph skeleton  `[x]`
- [x] `state.py` — `AgentState` TypedDict + Plan/ParsedEmail schemas
- [x] `build.py` — StateGraph, nodes wired, checkpointer (sqlite/memory)
- [x] `nodes.py` — all six nodes implemented
- **Done:** graph compiles and runs end-to-end.

### Phase 4 — Plan phase (ReAct, read-only)  `[x]`
- [x] `plan` node: bounded ReAct loop bound to read tools only
- [x] Emits typed `Plan` (actions + args) + draft reply
- [x] Prompts (`parser`, `planner`) — minimal + explicit
- **Done:** Scenario 1 produces a correct plan + reply, zero writes (verified live).

### Phase 5 — Guardrails / risk classification  `[x]`
- [x] `policy/guardrails.py`: refund/dispute keywords + non-refundable (RP003) data signal + ambiguous
- [x] `classify` node sets `risk_flags`; planner strips actions from flagged requests
- **Done:** Scenario 3 email is flagged and stripped.

### Phase 6 — Approval gate + modes  `[x]`
- [x] Dynamic `interrupt()` in `approval_gate` (not static `interrupt_before`) — see decision
- [x] `human` waits for approve/reject; `auto` runs through unless risky
- [x] Risky flag forces human review even in `auto`
- **Done:** both modes correct; risky never auto-executes (verified live + tests).

### Phase 7 — Execute phase  `[x]`
- [x] `execute` runs approved workflows + mock send
- [x] `--dry-run` simulates on a throwaway PMS copy, never sends
- **Done:** Scenario 2 creates a reservation + sends a mock reply after approval (verified live).

### Phase 8 — CLI + flags  `[x]`
- [x] argparse with all flags from `CLAUDE.md` §7
- [x] stdin / `-e` / `-f` input; `--show-plan`, `--json`, `--resume/--approve/--reject`
- **Done:** all §7 example commands work.

### Phase 9 — Streamlit UI (optional)  `[x]`
- [x] Email input, plan/draft display, approve/reject buttons, sidebar mirrors flags
- **Done:** thin client over `service.py`.

### Phase 10 — Tests + docs  `[x]`
- [x] pytest: PMS/workflows/guardrails units + Scenarios 1–3 (both modes), offline fake LLM — 20 passing
- [x] README (run / architecture / decisions / next); Architecture.md reconciled to the built system
- [ ] Walkthrough recording (manual deliverable — not code)
- **Done when:** recording captured.

### Phase 11 — Interactive Streamlit dashboard  `[x]`
Make the optional UI a real dashboard: show system state + how the agent thinks. Stays a thin
client over `service.py` (no new backend). Draft/guest messages render as friendly cards, not code.
- [x] Shell + Altek branding: wide layout, `st.logo(altek-logo.svg)`, sidebar logo + hotel name, modern settings widgets
- [x] Pipeline state strip (6 stages as live status badges) + KPI row (intent/risk/actions/mode/status)
- [x] Reasoning view: decision `log` timeline + ReAct `tool_trace` (tool/args/result) as expandable "thinking" steps
- [x] Plan & reply view: parsed fields, risk badges, actions, draft reply as friendly email card (`render_message` helper)
- [x] PMS explorer (cached) + approval panel with editable draft reply (wires service `edited_reply`)
- [x] Smoke-tested all 3 render paths offline via `AppTest` (idle / completed / risky-awaiting); live boot OK
- **Done:** all three scenarios render with live state + reasoning trace; messages shown friendly; logo present.

### Phase 12 — Dashboard: live state, dialog, PMS-write indicator, decisions DB  `[x]`
Second dashboard pass. Backend additions stay minimal and reuse existing state.
- [x] Favicon = Altek logo (`page_icon`); `log` now accumulates across nodes (`operator.add` reducer)
- [x] Live "system state": `service.stream_run`/`stream_resume` (`graph.stream`); pipeline lights up per node
- [x] PMS-write indicator badge: read-only / pending / wrote / dry-run / blocked (risky)
- [x] Continue-dialog chat: transcript + follow-up `st.chat_input`, prior turns passed as context
- [x] SQLite decisions audit (`history.py`); service records terminal runs; dashboard shows approved vs disapproved
- [x] Verified: pytest 20/20; offline AppTest (idle/completed/awaiting/conversation); live stream (auto read-only + human pause→resume write) — decision recorded only at terminal, `wrote_pms` correct
- **Done:** all features work live; recording gated to non-memory checkpointer (tests unaffected).

### Phase 13 — Dashboard: chat-first, PMS persistence, richer state  `[x]`
- [x] PMS writes persist to the data JSON (`PMS.save`); service persists after approved non-dry-run
      writes, gated to non-memory checkpointer; dashboard clears the cached PMS view so it refreshes
- [x] Dialog moved to the top (chat-first): transcript + example quick-starts + follow-up input + New conversation
- [x] Rejected reply blocks continuing the dialog (chat input disabled until New conversation); awaiting also disables
- [x] "System state" now shows decision log + risk assessment + plan inline (plus pipeline + write badge + KPIs)
- [x] Verified: pytest 20/20 (seed JSON hash unchanged), AppTest 4 paths (rejected/awaiting → input disabled),
      live persist to a temp seed copy (RES007 + guest added)
- **Done:** all four requests implemented and verified.

### Phase 14 — Booking correctness: guardrail scope, details, availability  `[x]`
- [x] Guardrail no longer flags **new bookings**: financial/refund + non-refundable checks apply only to
      change intents (modify/cancel/refund); word-boundary regex so "non-refundable" ≠ "refund"
- [x] Planner prompt: a booking must gather first/last name + email (required), phone + nationality when
      given; reuse existing guest by email; if required details missing → ask, schedule no action
- [x] Planner prompt: if the requested option is unavailable for the dates → schedule NO action, reply that
      it's taken and suggest `find_availability` alternatives; never book an unavailable option
- [x] Tests decoupled from the mutable runtime PMS via `tests/fixtures/mock_hotel_data.json` (pristine seed)
- [x] Hotel data explorer: added an **Availability** tab (date × room-type matrix) to trace how bookings decrement stock
- [x] Verified: pytest 22/22; live — non-refundable booking proceeds (risky=False, books RT002/RP003);
      taken double (Apr 22-24) refused with alternatives, no booking created
- **Done:** all three requests implemented and verified live.

---

## Session log

- 2026-07-09 — Removed dry-run entirely (per request): dropped `--dry-run` CLI flag + sidebar
  toggle, `Settings.dry_run`, `AgentState.dry_run`, mailer `dry_run`, and all downstream
  branches in nodes/service/streamlit; updated README + CLAUDE.md. pytest 22/22; Streamlit
  renders clean via AppTest.
- 2026-07-09 — Docs: rewrote README to be Streamlit-first (promote the dashboard); merged
  `Architecture.md` (mermaid system + sequence diagrams, badges, scenario map) into README and
  deleted it. Fixed stale bits: install now `pip install -e ".[ui]"`, 6 read tools incl.
  `list_inventory`, workflow names, PMS-persist is done (dropped from "next"), 22 tests, added
  decision-audit-log + `history.py`/`__main__.py` to layout.
- 2026-07-08 — Repo bootstrapped from CLAUDE.md; architecture and flags fixed.
- 2026-07-08 — Built full agent: PMS+tools, workflows, providers, graph (6 nodes), guardrails,
  CLI, Streamlit. 20 pytest tests green. Verified all 3 scenarios + both modes live on Anthropic
  (claude-sonnet-4-6), incl. cross-process SQLite resume. Reconciled README/Architecture/PROGRESS.
- 2026-07-08 — Phase 11: rebuilt `app_streamlit.py` into an interactive dashboard — Altek logo,
  live pipeline-state strip, KPI row, Reasoning view (decision log + ReAct tool_trace), Plan &
  reply, PMS explorer, editable approval reply. Guest messages render as friendly email cards
  (not code). Verified offline via `AppTest` (3 render paths) + clean live boot. Still a thin
  client over `service.py`.
- 2026-07-09 — Phase 14: guardrail no longer flags new bookings (financial/non-refundable checks
  scoped to change intents; word-boundary regex); planner prompt now gathers guest details and
  refuses unavailable options with alternatives; tests read a pristine fixture, not the mutable
  runtime PMS. pytest 22/22; both booking behaviors verified live.
- 2026-07-08 — Phase 13: dashboard is now chat-first (dialog on top); PMS writes persist to the
  data JSON (`PMS.save`, service-gated to non-memory + non-dry-run); System state shows decision
  log/risk/plan inline; rejected replies block continuing the dialog. pytest 20/20 (seed hash
  unchanged), AppTest 4 paths, live persist verified on a temp seed copy.
- 2026-07-08 — Phase 12: added live streaming (`stream_run`/`stream_resume`) so the dashboard
  pipeline lights up per node; Altek favicon; `log` reducer (accumulates trail); PMS-write
  indicator badge; continue-dialog chat (context passed to follow-ups); SQLite decision audit
  (`history.py`) + approved/disapproved view. Verified live (auto read-only + human pause→resume
  write) and offline (pytest 20/20, AppTest 4 paths).
- 2026-07-08 — Added `test-questions/` eval dataset: 20 guest-email cases (7 read / 7 write /
  6 risky) with structured tool-call gold standards + PMS-mutation deltas. Gold standards
  computed and re-verified against pms.py/workflows.py/guardrails.py (0 mismatches). Surfaced 2
  findings: TQ05 lookup falls to intent `other`→ambiguous (recommend `reservation_lookup` intent);
  TQ16 `financial_request` matches on `refund` inside `non-refundable` (harmless).

## Decision log

- 2026-07-08 — **ReAct, not RLM.** PMS is a tiny structured JSON; no long-context problem.
- 2026-07-08 — **Plan → Approve → Execute** on LangGraph.
- 2026-07-08 — **Dynamic `interrupt()` at the gate, not static `interrupt_before`.** A static
  breakpoint pauses unconditionally, breaking auto mode and unable to react to a risk flag
  computed inside the run. Dynamic interrupt lets the pause depend on state (mode + risk), which
  is the only clean way to satisfy "risky forces human review even in auto." Deviates from the
  original CLAUDE.md §2 note — intentional.
- 2026-07-08 — **Deterministic guardrails** (keyword + PMS data signals), not LLM-judged, so
  Scenario 3 is provably blocked and unit-testable.
- 2026-07-08 — **Tools vs skills:** atomic PMS ops as tools; ordered recipes as workflows. LLM
  picks the workflow (in the Plan); the workflow guarantees the steps run in order.
- 2026-07-08 — **No FastAPI / feedback store** (were in the aspirational Architecture.md). Cut to
  respect the timebox + "no over-engineering". CLI is baseline; Streamlit is a thin optional client.
- 2026-07-08 — **Multi-provider via key manager** (`--provider auto`); graph stays provider-agnostic.
- 2026-07-08 — **Structured output uses `method="function_calling"`**, not OpenAI's strict
  `json_schema` (the langchain-openai 0.3 default), which rejects the free-form `PlanAction.args`
  dict (`additionalProperties` error). function_calling is portable across OpenAI/Anthropic/Gemini.
  Also added graceful provider-error handling in CLI + Streamlit (no raw tracebacks on quota/network).
  Verified live: OpenAI + Anthropic OK; Gemini key has 0 free-tier quota (429) — external, not a bug.
- 2026-07-08 — **Bare dates default to year 2025** (the PMS's populated calendar) so scenarios hit data.
- 2026-07-08 — **Dashboard surfaces existing state, adds no backend.** The "how it thinks" view is
  just the already-persisted `log` + `tool_trace`; the pipeline strip is derived from state. Keeps
  the Streamlit app a thin presentation client and respects "no over-engineering". The editable
  approval reply wires the previously-unused `service.resume(edited_reply=...)` param.
- 2026-07-08 — **`log` gets an `operator.add` reducer.** Without it each node overwrote `log`, so a
  finished run kept only the last line; the reducer makes it a full decision trail (needed for the
  reasoning view). Only `log` accumulates — other list fields are set-once and stay overwrite.
- 2026-07-08 — **Multi-turn dialog via context-passing, not graph memory.** Each follow-up runs a
  fresh graph turn with the prior transcript prepended to the email; avoids checkpoint state-bleed
  and keeps nodes unchanged. The checkpointer still handles intra-run pause/resume.
- 2026-07-08 — **Reversed the earlier "no feedback store" cut** — the task now asks for it. Added a
  minimal stdlib-`sqlite3` audit log (`history.py`), separate from the LangGraph checkpointer.
  Recording is best-effort and gated to non-memory checkpointers so tests/offline runs stay clean.
- 2026-07-08 — **PMS writes now persist to the data JSON** (reverses CLAUDE.md §8 "never the
  original", at the user's request so the dashboard reflects bookings). `PMS.save()` persists;
  the *service* calls it only after an approved, non-dry-run write, and only for non-memory
  checkpointers — so unit tests (memory backend, in-graph) and dry-run never mutate the seed
  (verified: seed hash unchanged across pytest). Reset the seed with `git checkout data/`.
- 2026-07-09 — **Guardrail scope: new bookings always proceed.** Financial/refund + non-refundable
  risk checks now run only for change intents (modify/cancel/refund). Picking a non-refundable rate
  when *booking* is a normal choice, not a refund — fixed the false positive where "refund" matched
  inside "non-refundable" (now word-boundary regex). Only modify/cancel of an existing booking is gated.
- 2026-07-09 — **Tests read a pristine fixture, not `data/mock_hotel_data.json`.** Once the app
  persists writes to that file, reading it in tests would be non-deterministic; `tests/fixtures/`
  holds a frozen seed copy so tests stay stable while the runtime PMS is free to change.

## Known issues / TODO

- Writes are in-memory per run (never persisted to disk by design) — a resumed run reloads the
  PMS from file, so created ids restart (e.g. RES007). Fine for the mock; see README "next".
