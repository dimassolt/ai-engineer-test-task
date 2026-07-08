# Architecture — Hotel Email Agent

An LLM email agent for hotel guest emails. **ReAct** planning runs inside a
**Plan → Approve → Execute** graph, orchestrated by **LangGraph**, with a **CLI** baseline
and an optional **Streamlit** UI. Both interfaces call one shared entrypoint (`service.py`)
in-process — there is deliberately no web backend (the task deprioritises deployment/infra
and warns against over-engineering).

## Technology stack

![Python](https://img.shields.io/badge/Python_3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?style=for-the-badge&logo=pydantic&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)

**LLM providers (auto-selected by available key):**

![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white)
![Anthropic Claude](https://img.shields.io/badge/Anthropic_Claude-D97757?style=for-the-badge&logo=claude&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Google_Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white)

---

## System architecture

```mermaid
flowchart TD
    subgraph Clients["Clients"]
        CLI["CLI — baseline<br/>python -m hotel_agent"]
        UI["Streamlit UI (optional)"]
    end

    SVC["service.py<br/>run_email · resume (shared entrypoint)"]

    subgraph Engine["LangGraph agent · StateGraph"]
        N1["1 · parse_email"]
        N2["2 · classify + risk check"]
        N3["3 · plan · ReAct read-only"]
        GATE{"4 · approval_gate<br/>dynamic interrupt()"}
        N5["5 · execute · writes + send"]
        N6["6 · finalize"]
    end

    subgraph Tools["Tools & skills"]
        RT["Read tools<br/>availability · rates · guest · policy"]
        WT["Workflows (skills) → write tools<br/>make_reservation · change · cancel · send"]
    end

    KM["Key manager<br/>OpenAI · Anthropic · Gemini"]
    PMS[("Mock PMS<br/>mock_hotel_data.json")]
    CP[("SQLite / memory<br/>checkpointer · thread state")]

    CLI --> SVC
    UI --> SVC
    SVC --> N1
    N1 --> N2 --> N3 --> GATE
    GATE -- "approved / auto" --> N5
    GATE -- "rejected" --> N6
    N5 --> N6

    N3 -. "read" .-> RT
    N5 --> WT
    RT --> PMS
    WT --> PMS
    N1 -. "LLM" .-> KM
    N3 -. "LLM" .-> KM
    GATE <-. "pause / resume" .-> CP
```

**Reading the diagram:** the agent plans with **read-only** tools, stops at the **approval
gate**, and only then runs **write** workflows. The gate pauses via a *dynamic* `interrupt()`
keyed on mode + risk (human always pauses; auto pauses only when flagged risky). Every run's
state is checkpointed by `thread_id`, so a paused run resumes with full context — even from a
different process (SQLite).

---

## Request & approval workflow

```mermaid
sequenceDiagram
    actor G as Guest
    participant SVC as service.py
    participant GR as LangGraph
    participant PMS as Mock PMS
    participant P as LLM provider
    actor H as Staff (human / risky)

    G->>SVC: email + mode
    SVC->>GR: run (thread_id)
    GR->>P: parse + plan (ReAct, read-only)
    GR->>PMS: read availability / rates / guest

    alt Human mode, or flagged risky (Scenario 3)
        GR-->>SVC: awaiting_approval + plan + draft
        SVC-->>H: show plan + draft reply
        alt Approved
            H->>SVC: resume(approve)
            SVC->>GR: Command(resume)
            GR->>PMS: write via workflow (create / modify / cancel)
            GR-->>SVC: reply sent (mock)
        else Rejected
            H->>SVC: resume(reject)
            GR-->>SVC: no write, status=rejected
        end
    else Auto mode, not risky (Scenario 1 / 2)
        GR->>PMS: execute writes (if any)
        GR-->>SVC: completed + reply
    end

    SVC-->>G: draft or final reply
```

---

## Scenario mapping

| Scenario | Example | Path through the graph |
|----------|---------|------------------------|
| 1 · Read-only lookup | "Any rooms free Apr 20–22?" | plan uses read tools → reply, **no write** |
| 2 · Action + write | "Book a double w/ breakfast Apr 20–22" | plan → approve (or auto) → **workflow writes** → send |
| 3 · Ambiguous / risky | "Refund my non-refundable booking" | `classify` flags risky → **human review**, never auto-executed |

---

## Key design decisions

See the README for the full list. The load-bearing ones:

- **ReAct inside Plan→Approve→Execute** — a hard gate between "propose" and "do" makes the
  human-in-the-loop and the risk block trustworthy, and keeps the plan inspectable.
- **Dynamic `interrupt()` at the gate** (not static `interrupt_before`) — so the pause is
  decided from state: human always pauses; auto pauses only when flagged risky.
- **Deterministic guardrails** — Scenario 3 is blocked by code (keyword + PMS data signals),
  not by trusting the LLM.
- **Tools vs. workflows** — atomic PMS ops are tools; ordered recipes are workflows. The LLM
  chooses the workflow; the workflow guarantees the steps.
- **Provider-agnostic** via a key manager; **checkpointed state** by `thread_id` for durable
  pause/resume.
