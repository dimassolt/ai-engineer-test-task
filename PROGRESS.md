# PROGRESS.md — build tracker

Single source of truth for build status. Read at the start of each session; update after
each meaningful change. See `CLAUDE.md` §6 for the protocol. Keep entries to one line each.

- **Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done
- **Current phase:** Phase 10 — Tests + docs `[x]` (remaining deliverable: walkthrough recording)
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

---

## Session log

- 2026-07-08 — Repo bootstrapped from CLAUDE.md; architecture and flags fixed.
- 2026-07-08 — Built full agent: PMS+tools, workflows, providers, graph (6 nodes), guardrails,
  CLI, Streamlit. 20 pytest tests green. Verified all 3 scenarios + both modes live on Anthropic
  (claude-sonnet-4-6), incl. cross-process SQLite resume. Reconciled README/Architecture/PROGRESS.

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

## Known issues / TODO

- Writes are in-memory per run (never persisted to disk by design) — a resumed run reloads the
  PMS from file, so created ids restart (e.g. RES007). Fine for the mock; see README "next".
