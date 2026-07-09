"""Interactive Streamlit dashboard for the Grand Oslo Hotel email agent.

A thin client over `service.py` (no backend logic lives here) that makes the agent's
work *visible*:
- the guest **conversation** sits at the top and drives everything (chat-first);
- the graph pipeline lights up **live** as each node runs (via `stream_run`);
- "System state" shows the decision log, risk assessment, and plan for the current turn;
- a clear indicator says whether the agent wrote to the PMS or not (and the PMS view
  refreshes when it does);
- rejected replies are never sent — the dialog can't be continued until you start over;
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
    """Load the mock PMS JSON for the read-only explorer (cached; cleared after a write)."""
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
    """Read-only browser over the mock PMS (reservations, availability, guests, rates)."""
    if not pms_data:
        st.info("PMS data file not found or unreadable.", icon=":material/info:")
        return
    # Availability as a date × room-type matrix, so you can watch a cell drop when a booking
    # is made (create decrements the held nights; cancel/modify restore them).
    avail = pms_data.get("availability") or {}
    rt_ids = [rt["id"] for rt in (pms_data.get("room_types") or [])]
    avail_rows = [
        {"Date": d, **{rid: rooms.get(rid, 0) for rid in rt_ids}}
        for d, rooms in sorted(avail.items())
        if d != "_comment" and isinstance(rooms, dict)
    ]
    sections = {
        "Reservations": pms_data.get("reservations"),
        "Availability": avail_rows,
        "Guests": pms_data.get("guests"),
        "Room types": pms_data.get("room_types"),
        "Rate plans": pms_data.get("rate_plans"),
    }
    for tab, (name, rows) in zip(st.tabs(list(sections)), sections.items()):
        with tab:
            if name == "Availability":
                st.caption(
                    "Rooms free per room type per night (columns are room-type ids). "
                    "Dates not listed are fully booked. Watch a cell drop after a booking."
                )
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
# Header
# ─────────────────────────────────────────────────────────────────────────────

st.title(f":material/concierge: {hotel_name} — email agent")
st.caption(
    "Chat as a guest below. The agent reasons over the PMS with read-only tools, drafts a "
    "reply, and (after approval) writes to the PMS — watch its state and reasoning update live."
)

EXAMPLES = {
    "Availability": "Hi, do you have any rooms available April 20-22 for 2 adults?",
    "Booking": "We'd like to book a double with breakfast for 2 adults, April 20-22. "
               "— Ola Nordmann (ola@example.com)",
    "Refund (risky)": "I want a refund on my non-refundable booking RES002. "
                      "— Maria (maria.gonzalez@email.com)",
}

st.session_state.setdefault("conversation", [])
result: RunResult | None = st.session_state.get("result")
busy = bool(st.session_state.get("pending"))          # a turn is streaming this rerun
awaiting = result is not None and result.awaiting_approval
rejected = result is not None and result.status == "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# Conversation (dialog) — top of the dashboard, drives everything
# ─────────────────────────────────────────────────────────────────────────────

head_l, head_r = st.columns([4, 1], vertical_alignment="center")
head_l.subheader(":material/forum: Conversation")
if head_r.button("New conversation", icon=":material/refresh:", disabled=busy, width="stretch"):
    for k in ("conversation", "result", "reply_edit"):
        st.session_state.pop(k, None)
    st.session_state.conversation = []
    st.rerun()

convo = st.session_state.conversation
with st.container(border=True):
    if not convo and not busy:
        st.caption("Start the conversation — type a guest email below, or try an example:")
        for col, (name, text) in zip(st.columns(len(EXAMPLES)), EXAMPLES.items()):
            if col.button(name, width="stretch"):
                st.session_state.pending = {"kind": "run", "agent_input": text, "display": text, "reset": True}
                st.rerun()

    for turn in convo:
        with st.chat_message("user" if turn["role"] == "guest" else "assistant"):
            st.markdown(turn["text"].replace("\n", "  \n"))

    preview = st.session_state.get("pending")  # show the in-flight message while it streams
    if preview and preview.get("kind") == "run":
        with st.chat_message("user"):
            st.markdown(preview["display"].replace("\n", "  \n"))
        with st.chat_message("assistant"):
            st.caption("Thinking…")

    placeholder = (
        "Resolve the pending approval to continue…" if awaiting
        else "Reply rejected — start a new conversation to continue" if rejected
        else "Message as the guest…"
    )
    msg = st.chat_input(placeholder, disabled=busy or awaiting or rejected)
    if msg:
        st.session_state.pending = {
            "kind": "run",
            "agent_input": build_agent_input(convo, msg),
            "display": msg, "reset": not convo,
        }
        st.rerun()

if rejected:
    st.caption(
        ":material/block: The drafted reply was rejected and never sent — "
        "start a new conversation to continue."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Approval panel — right under the dialog, on the current turn
# ─────────────────────────────────────────────────────────────────────────────

if result is not None and not busy and result.awaiting_approval:
    req = result.approval_request or {}
    with st.container(border=True):
        st.markdown(":material/pause_circle: **Awaiting your approval**")
        if req.get("reason") == "risky_request":
            st.warning("Flagged as risky — cannot be executed autonomously.", icon=":material/gpp_maybe:")
        else:
            st.info("Human-in-the-loop mode — review the plan and reply before it runs.",
                    icon=":material/how_to_reg:")
        edited = st.text_area(
            "Edit the reply before sending (optional)",
            value=result.state.get("draft_reply", ""), key="reply_edit", height=180,
        )
        approve, reject_col = st.columns(2)
        if approve.button("Approve & send", type="primary", icon=":material/check:", width="stretch"):
            st.session_state.pending = {
                "kind": "resume", "thread_id": result.thread_id, "approved": True, "edited_reply": edited,
            }
            st.rerun()
        if reject_col.button("Reject", icon=":material/close:", width="stretch"):
            st.session_state.pending = {
                "kind": "resume", "thread_id": result.thread_id, "approved": False,
            }
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# System state — live pipeline, PMS-write indicator, decision log, risk, plan
# ─────────────────────────────────────────────────────────────────────────────

st.subheader(":material/account_tree: System state")

# Process a queued turn: stream it live, then settle via a rerun.
pending = st.session_state.pop("pending", None)
if pending:
    status_ph = st.empty()
    if pending["kind"] == "resume":
        gen = stream_resume(settings, pending["thread_id"], pending["approved"], pending.get("edited_reply"))
    else:
        gen = stream_run(pending["agent_input"], settings, pending.get("thread_id"))
    result = _guard(consume_stream, gen, status_ph)
    finish_turn(pending, result)
    # The service persists PMS writes to the data file; drop the cached view so it reloads.
    if result and not result.awaiting_approval:
        st_ = result.state
        if not st_.get("dry_run") and any(r.get("ok") for r in st_.get("execution", [])):
            load_pms.clear()
    st.session_state.result = result
    st.session_state.pop("reply_edit", None)
    st.rerun()

render_pipeline(result)

if result is None:
    st.badge("Idle — start a conversation above", icon=":material/bedtime:", color="grey")
else:
    s = result.state
    parsed = s.get("parsed", {})
    risk_flags = s.get("risk_flags", [])
    plan = s.get("plan", {})
    actions = plan.get("actions", []) or []

    label, color, icon = write_indicator(result)
    st.badge(label, icon=icon, color=color)

    with st.container(horizontal=True):
        st.metric("Intent", _pretty(parsed.get("intent")), border=True)
        st.metric("Risk", "Clear" if not risk_flags else f"{len(risk_flags)} flag(s)", border=True)
        st.metric("Planned actions", len(actions), border=True)
        st.metric("Mode", "Autonomous" if s.get("mode") == "auto" else "Human", border=True)
        st.metric("Status", _pretty(result.status), border=True)

    log_col, risk_col = st.columns(2)
    with log_col, st.container(border=True):
        st.markdown(":material/menu_book: **Decision log**")
        log = s.get("log", [])
        if log:
            for i, line in enumerate(log, 1):
                st.markdown(f":material/chevron_right: **{i}.** {line}")
        else:
            st.caption("No log entries.")
    with risk_col, st.container(border=True):
        st.markdown(":material/shield: **Risk assessment**")
        if risk_flags:
            for f in risk_flags:
                st.warning(f"**{f['code']}** — {f['reason']}", icon=":material/gpp_maybe:")
        else:
            st.success("No risk flags — safe to automate.", icon=":material/verified_user:")

    with st.container(border=True):
        st.markdown(":material/task: **Plan**")
        if plan.get("summary"):
            st.caption(plan["summary"])
        if actions:
            for a in actions:
                st.markdown(f":material/bolt: **{a['workflow']}** — {a.get('rationale', '')}")
                if a.get("args"):
                    st.json(a["args"])
        else:
            st.caption("Read-only request — no write actions planned.")


# ─────────────────────────────────────────────────────────────────────────────
# Details — reasoning trace, reply, execution, hotel data
# ─────────────────────────────────────────────────────────────────────────────

if result is None:
    with st.expander("Hotel data (read-only PMS)", icon=":material/inventory_2:"):
        render_pms_explorer()
else:
    s = result.state
    parsed = s.get("parsed", {})
    trace_tab, reply_tab, exec_tab, data_tab = st.tabs([
        "Reasoning trace", "Reply", "Execution", "Hotel data",
    ])

    with trace_tab:
        st.caption(
            "The ReAct loop — read-only tools the agent called while planning. No writes here."
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

    with reply_tab:
        st.markdown(":material/description: **What the guest asked**")
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

        render_message(
            s.get("draft_reply", ""),
            to=parsed.get("sender_email"),
            subject="Re: your enquiry — Grand Oslo Hotel",
        )
        sent = s.get("sent")
        if sent:
            verb = "would be sent (dry run)" if sent.get("dry_run") else "sent"
            render_message(
                sent.get("body", ""),
                to=sent.get("to"), subject=sent.get("subject"),
                title=f"Reply {verb}", icon=":material/outgoing_mail:",
                caption=f"At {sent.get('sent_at', '')}",
            )

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

    with data_tab:
        st.caption("Live view of the mock PMS — updates when the agent books, modifies, or cancels.")
        render_pms_explorer()


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
    disapproved = [r for r in rows if not r["approved"]]
    wrote = [r for r in rows if r["wrote_pms"]]
    with st.container(horizontal=True):
        st.metric("Total", len(rows), border=True)
        st.metric("Approved", len(approved), border=True)
        st.metric("Disapproved", len(disapproved), border=True)
        st.metric("Wrote to PMS", len(wrote), border=True)

    choice = st.segmented_control(
        "Filter decisions", ["All", "Approved", "Disapproved"], default="All",
        label_visibility="collapsed",
    ) or "All"
    view = {"Approved": approved, "Disapproved": disapproved}.get(choice, rows)
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
