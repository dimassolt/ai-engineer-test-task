"""Mock outbound email. No real integration — sending just
records the message.
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


def send_reply(to: str, subject: str, body: str) -> SentMessage:
    """Mock 'send' of the guest reply. Returns a record of what went out."""
    return SentMessage(
        to=to or "guest@unknown",
        subject=subject or "Re: your enquiry — Grand Oslo Hotel",
        body=body,
        sent_at=datetime.now().isoformat(timespec="seconds"),
    )
