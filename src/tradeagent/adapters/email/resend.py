"""Resend transactional email (ADR-0010). One API key from the environment (RESEND_API_KEY); nothing is stored."""

from __future__ import annotations

import logging
from typing import Any

import httpx

RESEND_URL = "https://api.resend.com/emails"
log = logging.getLogger("tradeagent.email")


class ResendSender:
    name = "resend"

    def __init__(self, api_key: str, from_address: str, http: httpx.Client | None = None):
        self.api_key, self.from_address = api_key, from_address
        self.http = http or httpx.Client(timeout=15.0)

    def send(self, to: str, subject: str, text: str, html: str | None) -> str:
        body: dict[str, Any] = {"from": self.from_address, "to": [to], "subject": subject, "text": text}
        if html:
            body["html"] = html
        r = self.http.post(RESEND_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400:
            raise RuntimeError(f"resend {r.status_code}: {r.text[:200]}")
        return str(r.json().get("id") or "")


class NullSender:
    """No provider configured: the notifications table is still the audit log (ADR-0010)."""

    name = "null"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, text: str, html: str | None) -> str:
        self.sent.append((to, subject, text))
        log.info("notification (no email provider): %s", subject)
        return f"null:{len(self.sent)}"
