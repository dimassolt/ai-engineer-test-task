"""argparse CLI — the baseline interface (see CLAUDE.md §7).

    # read-only lookup, just show the plan
    python -m hotel_agent -e "Any rooms free April 20-22?" --show-plan

    # booking, human approval (pauses at the gate), then resume to approve
    python -m hotel_agent -e "Book a double with breakfast, Apr 20-22, 2 adults" --mode human
    python -m hotel_agent --resume --thread-id <id> --approve

    # fully autonomous, no real writes/sends
    python -m hotel_agent -e "..." --mode auto --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import __version__
from .config import Settings, load_env
from .llm.providers import NoProviderError
from .service import RunResult, resume, run_email


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hotel_agent", description="AI hotel guest-email agent.")
    src = p.add_argument_group("email input")
    src.add_argument("--email", "-e", help="Inbound email body, inline.")
    src.add_argument("--email-file", "-f", help="Read the email body from a file.")

    p.add_argument("--mode", choices=["human", "auto"], default=None,
                   help="Execution mode. human = approval required before any write/send; "
                        "auto = end-to-end (risky requests still pause). Default: human.")
    p.add_argument("--provider", choices=["auto", "openai", "anthropic", "gemini"], default=None,
                   help="LLM provider; auto = first available key. Default: auto.")
    p.add_argument("--model", default=None, help="Override the model id.")
    p.add_argument("--data", default=None, help="PMS data file.")

    p.add_argument("--thread-id", default=None, help="LangGraph thread id (for resuming).")
    p.add_argument("--resume", action="store_true", help="Resume a run paused at the approval gate.")
    p.add_argument("--approve", action="store_true", help="Approve when resuming a human-mode run.")
    p.add_argument("--reject", action="store_true", help="Reject when resuming a human-mode run.")

    p.add_argument("--dry-run", action="store_true", help="Simulate writes/sending; never mutate or send.")
    p.add_argument("--checkpointer", choices=["sqlite", "memory"], default=None, help="State backend.")
    p.add_argument("--db", default=None, help="SQLite checkpoint file.")

    p.add_argument("--show-plan", action="store_true", help="Print the plan + draft reply, then stop.")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output.")
    p.add_argument("--log-level", "-v", default="WARNING",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Verbosity.")
    p.add_argument("--version", action="version", version=f"hotel_agent {__version__}")
    return p


def _settings_from_args(args: argparse.Namespace) -> Settings:
    s = Settings.from_env()
    if args.mode:         s.mode = args.mode
    if args.provider:     s.provider = args.provider
    if args.model:        s.model = args.model
    if args.data:         s.data_path = args.data
    if args.checkpointer: s.checkpointer = args.checkpointer
    if args.db:           s.db_path = args.db
    if args.dry_run:      s.dry_run = True
    return s


def _read_email(args: argparse.Namespace) -> str:
    if args.email:
        return args.email
    if args.email_file:
        with open(args.email_file) as fh:
            return fh.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("No email provided. Use --email, --email-file, or pipe via stdin.")


# ---- rendering ----------------------------------------------------------------------

def _render(result: RunResult) -> None:
    st = result.state
    parsed = st.get("parsed", {})
    print(f"\n■ intent: {parsed.get('intent', '?')}   thread: {result.thread_id}")

    flags = st.get("risk_flags", [])
    if flags:
        print("■ risk flags:")
        for f in flags:
            print(f"    ⚠ {f['code']}: {f['reason']}")

    plan = st.get("plan", {})
    print(f"\n■ plan: {plan.get('summary', '')}")
    actions = plan.get("actions", [])
    if actions:
        for a in actions:
            print(f"    → {a['workflow']}({json.dumps(a.get('args', {}))})")
            if a.get("rationale"):
                print(f"      · {a['rationale']}")
    else:
        print("    → (no write actions — read-only reply)")

    print("\n■ draft reply:\n" + _indent(st.get("draft_reply", "")))

    if result.awaiting_approval:
        req = result.approval_request or {}
        reason = req.get("reason", "approval required")
        print(f"\n⏸ awaiting approval ({reason}). Resume with:")
        print(f"    python -m hotel_agent --resume --thread-id {result.thread_id} --approve")
        print(f"    python -m hotel_agent --resume --thread-id {result.thread_id} --reject")
        return

    execution = st.get("execution", [])
    if execution:
        print("\n■ execution:")
        for r in execution:
            mark = "✓" if r["ok"] else "✗"
            print(f"    {mark} {r['workflow']}: {r.get('error') or ' → '.join(s['step'] for s in r['steps'])}")

    sent = st.get("sent")
    if sent:
        tag = " (dry-run, not sent)" if sent.get("dry_run") else ""
        print(f"\n■ reply {'would be sent' if sent.get('dry_run') else 'sent'} to {sent['to']}{tag}")

    print(f"\n● status: {st.get('status', result.status)}\n")


def _indent(text: str, pad: str = "    ") -> str:
    return "\n".join(pad + line for line in (text or "").splitlines())


def _render_json(result: RunResult) -> None:
    st = result.state
    print(json.dumps({
        "thread_id": result.thread_id,
        "status": result.status,
        "parsed": st.get("parsed"),
        "risk_flags": st.get("risk_flags"),
        "plan": st.get("plan"),
        "approval": st.get("approval"),
        "execution": st.get("execution"),
        "sent": st.get("sent"),
    }, indent=2, default=str))


# ---- main ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    settings = _settings_from_args(args)

    if args.resume:
        if not args.thread_id:
            raise SystemExit("--resume requires --thread-id.")
        if args.approve == args.reject:
            raise SystemExit("Pass exactly one of --approve / --reject.")
    else:
        email = _read_email(args)
        # --show-plan stops before execution: force a pause at the gate by planning in
        # human mode, then display the plan without resuming.
        if args.show_plan:
            settings.mode = "human"

    try:
        if args.resume:
            result = resume(settings, args.thread_id, approved=args.approve)
        else:
            result = run_email(email, settings, thread_id=args.thread_id)
    except NoProviderError as exc:
        raise SystemExit(f"No LLM provider available: {exc}")
    except Exception as exc:  # provider/API errors (quota, network, bad model) — no stack dump
        if args.log_level == "DEBUG":
            raise
        raise SystemExit(f"Agent run failed ({type(exc).__name__}): {exc}\n"
                         f"(re-run with -v DEBUG for the full traceback)")

    _render_json(result) if args.json else _render(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
