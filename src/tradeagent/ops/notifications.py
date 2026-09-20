"""Every alert is written to `notifications` (subject, kind, provider id, status) whether or not a provider is
configured, so an outage never loses an alert and the digest can be regenerated (ADR-0010)."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from tradeagent.domain.enums import ExecutionMode
from tradeagent.persistence.db import Database

log = logging.getLogger("tradeagent.notify")


class EmailSender(Protocol):
    name: str

    def send(self, to: str, subject: str, text: str, html: str | None) -> str: ...


class Notifier:
    def __init__(self, db: Database, sender: EmailSender, recipient: str, mode: ExecutionMode, phase_seq: int):
        self.db, self.sender, self.recipient = db, sender, recipient
        self.prefix = f"[TA:{mode.value}:{phase_seq}]"

    def send(
        self, kind: str, subject: str, text: str, html: str | None = None, payload: dict[str, Any] | None = None
    ) -> bool:
        full = f"{self.prefix} {subject}"
        try:
            mid = self.sender.send(self.recipient, full, text, html)
            status = "sent"
        except Exception as exc:
            log.error("notification %s failed: %s", kind, exc)
            mid, status = f"error:{type(exc).__name__}", "failed"
        self.db.record_notification(kind, self.recipient, full, mid, status, payload or {"text": text})
        return status == "sent"
