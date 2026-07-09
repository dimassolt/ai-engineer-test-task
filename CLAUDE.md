# CLAUDE.md

Guidance for AI coding assistants (Claude Code, Cursor, etc.) working in this repo.
**Read this file _and_ `PROGRESS.md` at the start of every session** before making changes.

---

## 1. Project

AI **email agent for hotel guest emails** (Grand Oslo Hotel). It takes an inbound guest
email, decides what needs to happen, drafts a reply, and executes actions against a
**mocked PMS** (`data/mock_hotel_data.json`). This is the Altek AI "AI Engineer Final Task."

No real email or hotel integration — the PMS is a local JSON file, email in is text/CLI,
email out is a mock send.

**What the reviewers grade (in priority order):**

1. **Agent architecture** — how LLM interaction, tools vs skills/workflows, prompts, and the
   autonomous-vs-human-in-the-loop split are structured. *Tools-vs-skills and reliable full
   workflows are the single most important axis.*
2. **Prompt design** — clear, minimal, effective instructions.
3. **Engineering quality** — clean, modular, easy to extend.

**They explicitly do NOT want:** frontend polish, real integrations, deployment/infra,
over-engineering, or framework-building for its own sake. **Timebox: 1–2 days.**

---

## 2. Architecture (decided — do not re-litigate without noting it in the Decision log)

**Pattern: ReAct reasoning inside a `Plan → Approve → Execute` graph.**

**Orchestration: LangGraph** (`StateGraph` + checkpointer).

**Graph nodes (the workflow):**

1. `parse_email` — extract sender, requested dates, party size, raw intent.
2. `classify` — intent + **risk pre-check** (guardrail). Risky/ambiguous → route to `human_review`.
3. `plan` — **ReAct loop with read-only tools only.** Produces a structured action plan
   + a draft guest reply. **No writes happen here.**
4. `approval_gate` — implemented with `interrupt_before=["execute"]`.
   - `human` mode: pause and wait for explicit approval.
   - `auto` mode: auto-approve **unless** `classify` flagged the request risky.
5. `execute` — **write tools** (create / modify / cancel reservation) + mock `send_reply`.
6. `finalize` — log outcome, return result.

**Two tiers of tools, separated by the approval gate:**

| Tier | Tools | Gate |
|------|-------|------|
| **Read** (always allowed, used in `plan`) | `get_guest`, `find_availability`, `quote_rate`, `get_policy`, `get_reservation` | none |
| **Write** (gated behind approval/policy) | `create_guest`, `create_reservation`, `modify_reservation`, `cancel_reservation`, `send_reply` (mock) | approval **and** passes policy |

**Tools vs skills/workflows (graded heavily):**
- **Tools** = atomic PMS operations.
- **Skills / workflows** = named multi-step recipes the agent invokes, e.g.
  `make_reservation` = guest-lookup → availability → quote → create. The agent decides
  *which* skill/tool to use; the workflow guarantees the steps run in order, reliably.

**Guardrails (Scenario 3):** refunds on non-refundable bookings (rate plan `RP003`),
ambiguous requests, and financially risky actions are **blocked from autonomous execution
and routed to human review in BOTH modes.**

**Three scenarios the system must handle:**
1. Read-only lookup ("any rooms Apr 20–22?") → plan uses read tools, no write.
2. Action + write ("book a double w/ breakfast Apr 20–22") → full plan → approve → execute.
3. Ambiguous/risky ("refund on my non-refundable booking") → flagged, never auto-executed.

---

## 3. Runtime context across phases (LangGraph state)

`AgentState` (a `TypedDict`) carries context between nodes: the email, parsed fields,
gathered PMS context, the plan, the draft reply, risk flags, approval status, execution
results, and the message history.

The **checkpointer persists state per `thread_id`**, so a run paused at the approval gate
can be resumed later with full context intact. This is the *runtime* mechanism for
"keeping context across phases." Default checkpointer: **SQLite** (survives process exit);
`memory` is available for tests. Do not confuse this with dev progress tracking (§6).

---

## 4. Tech stack

- **Python 3.11+**
- **LangGraph** + **langchain-core** for orchestration and tool binding.
- **Multi-provider LLM** via a small key manager (`llm/providers.py`): auto-detects which
  API keys are present and selects the first ready provider; supports **OpenAI, Anthropic,
  Gemini, and others**. Prefer `langchain.chat_models.init_chat_model` or LiteLLM for a
  uniform interface so swapping providers never touches graph code.
- **Streamlit** for the optional UI. The **argparse CLI is the baseline** (reviewers care
  about behavior, not presentation).
- **pytest** for tests.

Keys via `.env` (see `.env.example`): `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY` / `GEMINI_API_KEY`. Optional defaults: `HOTEL_AGENT_MODE`,
`HOTEL_AGENT_PROVIDER`.

---

## 5. Repo layout

```
.
├── CLAUDE.md                 # this file
├── PROGRESS.md               # live phase/task tracker — update it (see §6)
├── README.md                 # how to run, architecture, decisions, next steps
├── requirements.txt          # or pyproject.toml
├── .env.example
├── data/
│   └── mock_hotel_data.json  # the PMS (do not mutate the original; writes hit a copy)
├── src/hotel_agent/
│   ├── __init__.py
│   ├── config.py             # settings + parsed flags → typed config
│   ├── cli.py                # argparse entrypoint (python -m hotel_agent)
│   ├── app_streamlit.py      # optional UI, mirrors the CLI flags in a sidebar
│   ├── llm/
│   │   └── providers.py      # key manager + provider auto-select
│   ├── graph/
│   │   ├── state.py          # AgentState TypedDict
│   │   ├── build.py          # StateGraph assembly, edges, checkpointer, interrupt
│   │   └── nodes.py          # parse / classify / plan / execute / finalize
│   ├── tools/
│   │   ├── pms.py            # atomic read + write PMS operations (the "tools")
│   │   └── workflows.py      # composed multi-step "skills"
│   ├── policy/
│   │   └── guardrails.py     # risk classification (Scenario 3)
│   └── prompts/              # one file per prompt; minimal + explicit
└── tests/
    ├── test_scenario_1_lookup.py
    ├── test_scenario_2_booking.py
    └── test_scenario_3_risky.py
```

---

## 6. Context-keeping protocol (IMPORTANT)

**`PROGRESS.md` is the single source of truth for build status.** Keep it and the code in sync.

- **Start of session:** read `PROGRESS.md`, find **Current phase**, continue there.
- **After any meaningful change:**
  - Check off completed checklist items.
  - Update the **Current phase** pointer if it moved.
  - Add one line to the **Session log** (what changed) and record any real choice in the
    **Decision log** (what + why). Use absolute dates, not "today"/"yesterday."
- Keep entries **terse — one line each.** Never let code and `PROGRESS.md` drift.
- Mark a phase **Done** only when its checklist is fully checked **and** its tests pass,
  then advance **Current phase**.

---

## 7. Running the app — FLAGS

**CLI (baseline):** `python -m hotel_agent [flags]`

| Flag | Values | Default | Purpose |
|------|--------|---------|---------|
| `--email`, `-e` | text | — | Inbound email body, inline |
| `--email-file`, `-f` | path | — | Read the email body from a file |
| *(stdin)* | — | used if neither `-e`/`-f` given | Pipe the email in via stdin |
| `--mode` | `human` \| `auto` | `human` | Execution mode. `human` = approval required before any write; `auto` = end-to-end |
| `--provider` | `auto`\|`openai`\|`anthropic`\|`gemini` | `auto` | LLM provider; `auto` → key manager picks the first available key |
| `--model` | name | provider default | Override the model id |
| `--data` | path | `data/mock_hotel_data.json` | PMS data file |
| `--thread-id` | id | random | LangGraph thread for persistence / resuming a paused run |
| `--resume` | flag | off | Resume a run paused at the approval gate (use with `--thread-id`) |
| `--approve` / `--reject` | flag | — | Approval decision when resuming a `human`-mode run |
| `--checkpointer` | `sqlite` \| `memory` | `sqlite` | State persistence backend |
| `--db` | path | `.checkpoints.sqlite` | SQLite checkpoint file |
| `--show-plan` | flag | off | Print the structured plan + draft reply, then stop (no execution) |
| `--json` | flag | off | Machine-readable output (for tests / piping) |
| `--log-level`, `-v` | `DEBUG`..`ERROR` | `INFO` | Verbosity |
| `--version` | flag | — | Print version and exit |

**Examples**

```bash
# Scenario 1 — read-only lookup, just show the plan
python -m hotel_agent -e "Any rooms free April 20–22?" --show-plan

# Scenario 2 — booking, human approval (pauses at the gate)
python -m hotel_agent -f email.txt --mode human
# ...review the printed plan, then:
python -m hotel_agent --resume --thread-id <id> --approve

# Scenario 2 — fully autonomous, writes + sends end-to-end
python -m hotel_agent -f email.txt --mode auto

# Scenario 3 — risky request is flagged even in auto mode
python -m hotel_agent -e "Refund my non-refundable booking" --mode auto
```

**Streamlit (optional UI):** `streamlit run src/hotel_agent/app_streamlit.py`
Sidebar toggles mirror the flags above (mode, provider, data path).

---

## 8. Conventions

- Prompts live in `prompts/` as separate files; keep them minimal and explicit.
- Every tool is a pure function over the loaded PMS. **Writes mutate an in-memory copy.**
  They are persisted back to the data file (`PMS.save`) only by the *service layer*, and only
  after an approved write on a persistent (non-memory) checkpointer — so the dashboard reflects
  real bookings while **unit tests (memory backend) never touch the seed**. (Reverses the
  original "never mutate the original" rule, per an explicit request; `git checkout data/`
  restores the seed.)
- Add or extend a scenario test whenever you change behavior.
- Don't introduce a new abstraction unless it removes real duplication. Match the timebox.

---

## 9. Definition of done (per the task)

GitHub repo + working code · README (how to run, architecture overview, key design
decisions, what you'd improve next) · a short walkthrough recording. Tests cover all three
scenarios. Both approval modes work. Scenario 3 is provably blocked from autonomous execution.
