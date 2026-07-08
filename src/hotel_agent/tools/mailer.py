"""Mock outbound email. No real integration (out of scope per the task) — sending just
records the message. `dry_run` skips even that side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class SentMessage:
    to: str
    subject: str
    body: str
    sent_at: str
    dry_run: bool


def send_reply(to: str, subject: str, body: str, dry_run: bool = False) -> SentMessage:
    """Mock 'send' of the guest reply. Returns a record of what would/did go out."""
    return SentMessage(
        to=to or "guest@unknown",
        subject=subject or "Re: your enquiry — Grand Oslo Hotel",
        body=body,
        sent_at=datetime.now().isoformat(timespec="seconds"),
        dry_run=dry_run,
    )
