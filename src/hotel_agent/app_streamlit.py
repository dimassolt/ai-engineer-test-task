"""Optional Streamlit UI — a thin client over `service.py`, mirroring the CLI flags.

Run:  streamlit run src/hotel_agent/app_streamlit.py
(Frontend polish is explicitly out of scope; this just makes the approval loop clickable.)
"""

from __future__ import annotations

import streamlit as st

from hotel_agent.config import Settings, load_env
from hotel_agent.service import resume, run_email

load_env()
st.set_page_config(page_title="Grand Oslo Hotel — Email Agent", page_icon="🏨")
st.title("🏨 Grand Oslo Hotel — Email Agent")

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["human", "auto"], help="human = approve before write/send.")
    provider = st.selectbox("Provider", ["auto", "anthropic", "openai", "gemini"])
    dry_run = st.checkbox("Dry-run (simulate writes/sends)")
    data_path = st.text_input("PMS data", "data/mock_hotel_data.json")

settings = Settings(mode=mode, provider=provider, dry_run=dry_run, data_path=data_path)

EXAMPLES = {
    "1 · Availability": "Hi, do you have any rooms available April 20-22 for 2 adults?",
    "2 · Booking": "We'd like to book a double with breakfast for 2 adults, April 20-22. "
                   "— Ola Nordmann (ola@example.com)",
    "3 · Refund (risky)": "I want a refund on my non-refundable booking RES002. "
                          "— Maria (maria.gonzalez@email.com)",
}
choice = st.selectbox("Load an example", ["—", *EXAMPLES])
email = st.text_area("Inbound guest email", EXAMPLES.get(choice, ""), height=140)


def _render(result):
    st.subheader(f"Intent: {result.state.get('parsed', {}).get('intent', '?')}")
    for f in result.state.get("risk_flags", []):
        st.warning(f"⚠ {f['code']}: {f['reason']}")
    plan = result.state.get("plan", {})
    st.markdown(f"**Plan:** {plan.get('summary', '')}")
    for a in plan.get("actions", []) or ["*(read-only — no write actions)*"]:
        st.write(a if isinstance(a, str) else f"→ `{a['workflow']}` {a.get('args', {})}")
    st.markdown("**Draft reply:**")
    st.code(result.state.get("draft_reply", ""), language="markdown")
    for r in result.state.get("execution", []):
        (st.success if r["ok"] else st.error)(
            f"{r['workflow']}: {r.get('error') or ' → '.join(s['step'] for s in r['steps'])}"
        )
    if result.state.get("sent"):
        st.info(f"Reply {'would be sent' if dry_run else 'sent'} → {result.state['sent']['to']}")


def _guard(fn, *args):
    """Run a service call, surfacing provider/API errors as a clean message."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 — user-facing, keep the app alive
        st.error(f"Run failed ({type(exc).__name__}): {exc}")
        st.stop()


if st.button("Analyze email", type="primary") and email.strip():
    st.session_state.result = _guard(run_email, email, settings)

result = st.session_state.get("result")
if result:
    _render(result)
    if result.awaiting_approval:
        st.divider()
        st.markdown(f"**⏸ Awaiting approval** — `{result.approval_request.get('reason')}`")
        col1, col2 = st.columns(2)
        if col1.button("✅ Approve & execute"):
            st.session_state.result = _guard(resume, settings, result.thread_id, True)
            st.rerun()
        if col2.button("❌ Reject"):
            st.session_state.result = _guard(resume, settings, result.thread_id, False)
            st.rerun()
    else:
        st.success(f"Status: {result.status}")
