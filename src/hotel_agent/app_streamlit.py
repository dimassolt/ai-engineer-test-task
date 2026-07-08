"""Interactive Streamlit dashboard for the Grand Oslo Hotel email agent.

A thin client over `service.py` (no backend logic lives here) that makes the agent's
work *visible*:
- the graph pipeline lights up **live** as each node runs (via `stream_run`);
- a decision log + the read-only tools it called show *how* it reasoned;
- a clear indicator says whether it wrote to the PMS or not;
- you can **continue the conversation** with follow-up guest messages;
- a SQLite **decision history** lists every approved / disapproved answer.

Guest-facing messages render as friendly email cards, not code blocks.

Run:  streamlit run src/hotel_agent/app_streamlit.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from hotel_agent.config import Settings, load_env
from hotel_agent.history import load_decisions
from hotel_agent.service import RunResult, stream_resume, stream_run

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGO = REPO_ROOT / "altek-logo.svg"

load_env()
st.set_page_config(
    page_title="Grand Oslo Hotel — Email Agent",
    page_icon=str(LOGO) if LOGO.exists() else ":material/concierge:",  # Altek mark as favicon
    layout="wide",
)
if LOGO.exists():
    st.logo(str(LOGO), size="large")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def load_pms(path: str) -> dict:
    """Load the mock PMS JSON for the read-only explorer (cached per path)."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 — explorer is best-effort
        return {}


def _pretty(text: str | None) -> str:
    """snake_case / ids → 'Sentence case' for display."""
    return (text or "").replace("_", " ").strip().capitalize() or "—"


def render_message(
    body: str,
    *,
    to: str | None = None,
    subject: str | None = None,
    title: str = "Draft reply to guest",
    icon: str = ":material/mail:",
    caption: str | None = None,
) -> None:
    """Render a guest-facing message as a friendly email card (never a code block)."""
    with st.container(border=True):
        st.markdown(f"{icon} **{title}**")
        meta = [m for m in (f"To: {to}" if to else None, f"Subject: {subject}" if subject else None) if m]
        if meta:
            st.caption(" · ".join(meta))
        # Preserve the email's line breaks (markdown otherwise folds single newlines).
        st.markdown((body or "_(empty message)_").replace("\n", "  \n"))
        if caption:
            st.caption(caption)


def _guard(fn, *args, **kwargs):
    """Run a service call, surfacing provider/API errors as a clean message."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — user-facing, keep the app alive
        st.error(f"Run failed ({type(exc).__name__}): {exc}", icon=":material/error:")
        st.stop()


# ─── Pipeline state ──────────────────────────────────────────────────────────

STAGES = [
    ("Parse", "parse", ":material/mail:"),
    ("Classify", "classify", ":material/policy:"),
    ("Plan", "plan", ":material/checklist:"),
    ("Approval", "approval", ":material/how_to_reg:"),
    ("Execute", "execute", ":material/play_circle:"),
    ("Finalize", "finalize", ":material/flag:"),
]
STAGE_ORDER = [key for _, key, _ in STAGES]
NODE_TO_STAGE = {
    "parse": "parse", "classify": "classify", "plan": "plan",
    "approval_gate": "approval", "execute": "execute", "finalize": "finalize",
}

# stage status → (badge text, badge color, badge icon)
_BADGE = {
    "done": ("Done", "green", ":material/check_circle:"),
    "current": ("Running", "blue", ":material/pending:"),
    "waiting": ("Waiting", "orange", ":material/hourglass_top:"),
    "blocked": ("Skipped", "grey", ":material/block:"),
    "pending": ("Pending", "grey", ":material/radio_button_unchecked:"),
}


def stage_status(result: RunResult | None) -> dict[str, str]:
    """Derive each graph stage's status from a settled/paused run state."""
    status = {key: "pending" for key in STAGE_ORDER}
    if result is None:
        return status
    s, awaiting = result.state, result.awaiting_approval

    if s.get("parsed"):
        status["parse"] = "done"
    if "risky" in s:
        status["classify"] = "done"
    if s.get("plan"):
        status["plan"] = "done"

    approval = s.get("approval")
    if awaiting:
        status["approval"] = "waiting"
    elif approval in ("approved", "auto_approved"):
        status["approval"] = "done"
    elif approval == "rejected":
        status["approval"] = "done"
        status["execute"] = "blocked"  # rejected → execution skipped

    if s.get("execution") or s.get("sent"):
        status["execute"] = "done"
    if s.get("status") and not awaiting:
        status["finalize"] = "done"
    return status


def _draw_pipeline(status: dict[str, str]) -> None:
    for col, (label, key, icon) in zip(st.columns(len(STAGES), border=True), STAGES):
        text, color, badge_icon = _BADGE[status[key]]
        with col:
            st.markdown(f"{icon} **{label}**")
            st.badge(text, icon=badge_icon, color=color)


def render_pipeline(result: RunResult | None) -> None:
    _draw_pipeline(stage_status(result))


def write_indicator(result: RunResult) -> tuple[str, str, str]:
    """(label, color, icon) — does/did this run write to the PMS?"""
    s = result.state
    actions = s.get("plan", {}).get("actions", [])
    execution = s.get("execution", [])
    if execution:
        ok = [r for r in execution if r.get("ok")]
        if s.get("dry_run"):
            return ("PMS simulated (dry-run) — not modified", "orange", ":material/science:")
        if ok:
            return (f"Wrote to PMS — {len(ok)} action(s) committed", "green", ":material/database:")
        return ("PMS write attempted but failed", "red", ":material/error:")
    if result.awaiting_approval and actions:
        return ("PMS write pending approval", "blue", ":material/hourglass_top:")
    if s.get("risky"):
        return ("No PMS write — flagged for human review", "violet", ":material/gpp_maybe:")
    if actions:
        return ("PMS write planned (not yet executed)", "blue", ":material/pending:")
    return ("Read-only — no PMS write", "grey", ":material/lock:")


def consume_stream(gen, status_ph) -> RunResult | None:
    """Drive a service stream, animating the pipeline as each node finishes.

    The columns are built once; only the per-stage badge is swapped in place each tick, so
    the horizontal layout never gets re-created (which would ghost as a vertical stack)."""
    status = {key: "pending" for key in STAGE_ORDER}
    status["parse"] = "current"
    cells: dict = {}
    for col, (label, key, icon) in zip(st.columns(len(STAGES), border=True), STAGES):
        with col:
            st.markdown(f"{icon} **{label}**")
            cells[key] = st.empty()

    def paint() -> None:
        for key, ph in cells.items():
            text, color, badge_icon = _BADGE[status[key]]
            ph.badge(text, icon=badge_icon, color=color)

    paint()
    result: RunResult | None = None
    for kind, payload in gen:
        if kind == "result":
            result = payload
            continue
        for node in payload:  # payload == {node_name: delta} or {"__interrupt__": (...)}
            if node == "__interrupt__":
                status["approval"] = "waiting"
                status_ph.info("Paused — awaiting human approval.", icon=":material/pause_circle:")
                continue
            stage = NODE_TO_STAGE.get(node)
            if not stage:
                continue
            status[stage] = "done"
            nxt = STAGE_ORDER.index(stage) + 1
            if nxt < len(STAGE_ORDER) and status[STAGE_ORDER[nxt]] == "pending":
                status[STAGE_ORDER[nxt]] = "current"
            status_ph.markdown(f":material/bolt: **{stage.capitalize()}** complete…")
        paint()
    status_ph.empty()
    return result


def render_pms_explorer() -> None:
    """Read-only browser over the mock PMS (guests, reservations, inventory, rates)."""
    if not pms_data:
        st.info("PMS data file not found or unreadable.", icon=":material/info:")
        return
    sections = {
        "Reservations": pms_data.get("reservations"),
        "Guests": pms_data.get("guests"),
        "Room types": pms_data.get("room_types"),
        "Rate plans": pms_data.get("rate_plans"),
    }
    for tab, rows in zip(st.tabs(list(sections)), sections.values()):
        with tab:
            if rows:
                st.dataframe(rows, hide_index=True, width="stretch")
            else:
                st.caption("No records.")


def build_agent_input(conversation: list[dict], new_msg: str) -> str:
    """Prepend the running transcript so a follow-up email keeps context."""
    if not conversation:
        return new_msg
    lines = [f"{'Guest' if t['role'] == 'guest' else 'Hotel'}: {t['text']}" for t in conversation]
    return (
        "[Ongoing email thread — earlier messages, for context]\n"
        + "\n".join(lines)
        + f"\n\n[Latest message from the guest — reply to this]\n{new_msg}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — settings + branding
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    if LOGO.exists():
        st.image(str(LOGO), width=120)
    st.header("Agent settings")

    mode = st.segmented_control(
        "Execution mode",
        ["human", "auto"],
        default="human",
        format_func=lambda m: {"human": "Human-in-the-loop", "auto": "Autonomous"}[m],
        help="Human = approve before any write/send. Auto = end-to-end, unless flagged risky.",
    ) or "human"
    provider = st.selectbox("LLM provider", ["auto", "anthropic", "openai", "gemini"])
    dry_run = st.toggle("Dry run", help="Simulate writes and sends — never mutate the PMS or 'send'.")

    with st.expander("Advanced", icon=":material/tune:"):
        data_path = st.text_input("PMS data file", "data/mock_hotel_data.json")

    st.caption(
        "Guardrails: refunds on non-refundable bookings, ambiguous or financially risky "
        "requests are routed to human review in **both** modes."
    )

settings = Settings(mode=mode, provider=provider, dry_run=dry_run, data_path=data_path)
pms_data = load_pms(data_path)
hotel_name = (pms_data.get("hotel") or {}).get("name", "Grand Oslo Hotel")


# ─────────────────────────────────────────────────────────────────────────────
# Header + new-email input (starts a fresh conversation)
# ─────────────────────────────────────────────────────────────────────────────

st.title(f":material/concierge: {hotel_name} — email agent")
st.caption(
    "Reads an inbound guest email, reasons over the PMS with read-only tools, drafts a "
    "reply, and executes actions — with a human approval gate. Watch it think below."
)

EXAMPLES = {
    "Availability": "Hi, do you have any rooms available April 20-22 for 2 adults?",
    "Booking": "We'd like to book a double with breakfast for 2 adults, April 20-22. "
               "— Ola Nordmann (ola@example.com)",
    "Refund (risky)": "I want a refund on my non-refundable booking RES002. "
                      "— Maria (maria.gonzalez@email.com)",
}

st.session_state.setdefault("email_text", "")
st.session_state.setdefault("conversation", [])


def _load_example() -> None:
    choice = st.session_state.get("example_choice")
    if choice:
        st.session_state.email_text = EXAMPLES[choice]


with st.container(border=True):
    st.markdown(":material/mail: **New guest email** (starts a fresh conversation)")
    st.segmented_control(
        "Load an example scenario",
        list(EXAMPLES),
        key="example_choice",
        on_change=_load_example,
        label_visibility="collapsed",
    )
    st.text_area(
        "Inbound guest email",
        key="email_text",
        height=140,
        placeholder="Paste a guest email, or load an example above…",
        label_visibility="collapsed",
    )
    if st.button("Analyze email", type="primary", icon=":material/send:") and st.session_state.email_text.strip():
        st.session_state.pending = {
            "kind": "run", "agent_input": st.session_state.email_text,
            "display": st.session_state.email_text, "reset": True,
        }
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# System state — live pipeline while a run streams, settled strip otherwise
# ─────────────────────────────────────────────────────────────────────────────

st.subheader(":material/account_tree: System state")


def finish_turn(pending: dict, result: RunResult | None) -> None:
    """Update the conversation transcript after a streamed turn settles."""
    if result is None:
        return
    convo = st.session_state.conversation
    if pending.get("reset"):
        convo.clear()
    if pending["kind"] == "run":
        convo.append({"role": "guest", "text": pending["display"]})
        draft = result.state.get("draft_reply")
        if draft:
            convo.append({"role": "agent", "text": draft})
    elif pending["kind"] == "resume" and pending.get("edited_reply"):
        if convo and convo[-1]["role"] == "agent":
            convo[-1]["text"] = pending["edited_reply"]


pending = st.session_state.pop("pending", None)
if pending:
    status_ph = st.empty()
    if pending["kind"] == "resume":
        gen = stream_resume(settings, pending["thread_id"], pending["approved"], pending.get("edited_reply"))
    else:
        gen = stream_run(pending["agent_input"], settings, pending.get("thread_id"))
    result = _guard(consume_stream, gen, status_ph)
    finish_turn(pending, result)
    st.session_state.result = result
    st.session_state.pop("reply_edit", None)
    st.rerun()

result: RunResult | None = st.session_state.get("result")
render_pipeline(result)
if result is not None:
    label, color, icon = write_indicator(result)
    st.badge(label, icon=icon, color=color)
else:
    st.caption("Pipeline is idle — analyze an email to see each stage light up.")
    with st.expander("Hotel data (read-only PMS)", icon=":material/inventory_2:"):
        render_pms_explorer()


# ─────────────────────────────────────────────────────────────────────────────
# Results for the latest turn
# ─────────────────────────────────────────────────────────────────────────────

if result is not None:
    s = result.state
    parsed = s.get("parsed", {})
    plan = s.get("plan", {})
    risk_flags = s.get("risk_flags", [])
    actions = plan.get("actions", []) or []

    with st.container(horizontal=True):
        st.metric("Intent", _pretty(parsed.get("intent")), border=True)
        st.metric("Risk", "Clear" if not risk_flags else f"{len(risk_flags)} flag(s)", border=True)
        st.metric("Planned actions", len(actions), border=True)
        st.metric("Mode", "Autonomous" if s.get("mode") == "auto" else "Human", border=True)
        st.metric("Status", _pretty(result.status), border=True)

    # Approval panel (prominent, above the detail tabs)
    if result.awaiting_approval:
        req = result.approval_request or {}
        reason = req.get("reason", "human_mode")
        with st.container(border=True):
            st.markdown(":material/pause_circle: **Awaiting your approval**")
            if reason == "risky_request":
                st.warning(
                    "This request was flagged as risky and cannot be executed autonomously.",
                    icon=":material/gpp_maybe:",
                )
            else:
                st.info("Human-in-the-loop mode — review the plan and reply before it runs.",
                        icon=":material/how_to_reg:")
            edited = st.text_area(
                "Edit the reply before sending (optional)",
                value=s.get("draft_reply", ""), key="reply_edit", height=180,
            )
            approve, reject = st.columns(2)
            if approve.button("Approve & execute", type="primary", icon=":material/check:"):
                st.session_state.pending = {
                    "kind": "resume", "thread_id": result.thread_id,
                    "approved": True, "edited_reply": edited,
                }
                st.rerun()
            if reject.button("Reject", icon=":material/close:"):
                st.session_state.pending = {
                    "kind": "resume", "thread_id": result.thread_id, "approved": False,
                }
                st.rerun()
    elif result.status == "rejected":
        st.error("Run rejected by the reviewer — no writes were performed.", icon=":material/block:")
    else:
        st.success(f"Run {result.status.replace('_', ' ')}.", icon=":material/task_alt:")

    reasoning_tab, plan_tab, exec_tab, data_tab = st.tabs([
        "Reasoning", "Plan & reply", "Execution", "Hotel data",
    ])

    # -- Reasoning: how the agent thinks --
    with reasoning_tab:
        st.markdown("#### :material/menu_book: Decision log")
        st.caption("One line per graph node — the trail of decisions this run made.")
        log = s.get("log", [])
        if log:
            with st.container(border=True):
                for i, line in enumerate(log, 1):
                    st.markdown(f":material/chevron_right: **{i}.** {line}")
        else:
            st.caption("No log entries.")

        st.markdown("#### :material/psychology: Reasoning trace (read-only tools)")
        st.caption(
            "While planning, the agent runs a ReAct loop using only read tools — this is the "
            "information it gathered before drafting. No writes happen here."
        )
        trace = s.get("tool_trace", [])
        if trace:
            for i, step in enumerate(trace, 1):
                args = step.get("args", {})
                arg_hint = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
                with st.status(f"Step {i} · {step['tool']} {arg_hint}", state="complete"):
                    if args:
                        st.markdown("**Input**")
                        st.json(args)
                    st.markdown("**Result**")
                    raw = step.get("result", "")
                    try:
                        st.json(json.loads(raw))
                    except (ValueError, TypeError):
                        st.text(raw)
        else:
            st.caption("No tools were needed for this request.")

    # -- Plan & reply --
    with plan_tab:
        st.markdown("#### :material/description: What the guest asked")
        with st.container(border=True):
            st.markdown(parsed.get("summary") or "_No summary._")
            field_labels = {
                "sender_name": "Guest", "sender_email": "Email",
                "check_in": "Check-in", "check_out": "Check-out",
                "adults": "Adults", "children": "Children",
                "room_preference": "Room preference", "reservation_id": "Reservation",
            }
            present = [(lbl, parsed.get(k)) for k, lbl in field_labels.items() if parsed.get(k) not in (None, "")]
            if present:
                cols = st.columns(2)
                for idx, (lbl, val) in enumerate(present):
                    cols[idx % 2].markdown(f"**{lbl}:** {val}")

        st.markdown("#### :material/shield: Risk assessment")
        if risk_flags:
            for f in risk_flags:
                st.warning(f"**{f['code']}** — {f['reason']}", icon=":material/gpp_maybe:")
        else:
            st.success("No risk flags — safe to automate.", icon=":material/verified_user:")

        st.markdown("#### :material/task: Planned actions")
        if actions:
            st.caption(plan.get("summary", ""))
            for a in actions:
                with st.container(border=True):
                    st.markdown(f":material/bolt: **{a['workflow']}**")
                    if a.get("rationale"):
                        st.caption(a["rationale"])
                    if a.get("args"):
                        st.json(a["args"])
        else:
            st.caption("Read-only request — no write actions planned.")

        st.markdown("#### :material/mail: Draft reply")
        render_message(
            s.get("draft_reply", ""),
            to=parsed.get("sender_email"),
            subject="Re: your enquiry — Grand Oslo Hotel",
        )

    # -- Execution --
    with exec_tab:
        execution = s.get("execution", [])
        if execution:
            for r in execution:
                if r["ok"]:
                    with st.container(border=True):
                        st.markdown(f":material/check_circle: **{r['workflow']}**")
                        for step in r.get("steps", []):
                            st.markdown(f":material/check: {step['step']}")
                else:
                    st.error(f"**{r['workflow']}** failed: {r.get('error')}", icon=":material/error:")
        else:
            st.caption("No write actions were executed for this request.")

        sent = s.get("sent")
        if sent:
            verb = "would be sent (dry run)" if sent.get("dry_run") else "sent"
            render_message(
                sent.get("body", ""),
                to=sent.get("to"),
                subject=sent.get("subject"),
                title=f"Reply {verb}",
                icon=":material/outgoing_mail:",
                caption=f"At {sent.get('sent_at', '')}",
            )

    # -- Hotel data --
    with data_tab:
        render_pms_explorer()


# ─────────────────────────────────────────────────────────────────────────────
# Conversation — transcript + follow-up (continue the dialog)
# ─────────────────────────────────────────────────────────────────────────────

if st.session_state.conversation:
    st.subheader(":material/forum: Conversation")
    with st.container(border=True):
        for turn in st.session_state.conversation:
            role = "user" if turn["role"] == "guest" else "assistant"
            with st.chat_message(role):
                st.markdown(turn["text"].replace("\n", "  \n"))
        followup = st.chat_input("Send a follow-up as the guest…", disabled=result is not None and result.awaiting_approval)
        if followup:
            st.session_state.pending = {
                "kind": "run",
                "agent_input": build_agent_input(st.session_state.conversation, followup),
                "display": followup, "reset": False,
            }
            st.rerun()
    if result is not None and result.awaiting_approval:
        st.caption("Resolve the pending approval above before continuing the conversation.")


# ─────────────────────────────────────────────────────────────────────────────
# Decision history — SQLite audit log of approved / disapproved answers
# ─────────────────────────────────────────────────────────────────────────────

st.subheader(":material/history: Decision history")
st.caption("Every completed run is logged to SQLite — approved, auto-approved, or rejected.")
rows = load_decisions(settings.history_db_path)
if not rows:
    st.caption("No decisions recorded yet. Completed runs will appear here.")
else:
    approved = [r for r in rows if r["approved"]]
    rejected = [r for r in rows if not r["approved"]]
    wrote = [r for r in rows if r["wrote_pms"]]
    with st.container(horizontal=True):
        st.metric("Total", len(rows), border=True)
        st.metric("Approved", len(approved), border=True)
        st.metric("Disapproved", len(rejected), border=True)
        st.metric("Wrote to PMS", len(wrote), border=True)

    choice = st.segmented_control(
        "Filter decisions", ["All", "Approved", "Disapproved"], default="All",
        label_visibility="collapsed",
    ) or "All"
    view = {"Approved": approved, "Disapproved": rejected}.get(choice, rows)
    st.dataframe(
        [
            {
                "Time": r["ts"],
                "Intent": _pretty(r["intent"]),
                "Decision": _pretty(r["approval"]),
                "Approved": bool(r["approved"]),
                "Risky": bool(r["risky"]),
                "Wrote PMS": bool(r["wrote_pms"]),
                "To": r["sent_to"],
                "Answer": r["answer"],
            }
            for r in view
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "Approved": st.column_config.CheckboxColumn(),
            "Risky": st.column_config.CheckboxColumn(),
            "Wrote PMS": st.column_config.CheckboxColumn(),
            "Answer": st.column_config.TextColumn(width="large"),
        },
    )
