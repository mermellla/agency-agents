"""SEC EDGAR access under the fair-access policy: identifying User-Agent, ≤ 10 requests/second (§5.2, ADR-0005)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from tradeagent.domain.enums import SourceHealth, TrustGrade
from tradeagent.domain.models import SourceStamp

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
MIN_INTERVAL = 0.11  # seconds between requests → < 10 req/s


class EdgarClient:
    domain = "filings"
    source = "sec-edgar"
    grade = TrustGrade.EXECUTION

    def __init__(self, user_agent: str, http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep):
        if "@" not in user_agent:
            raise ValueError(
                "EDGAR_USER_AGENT must identify the operator with a contact email (SEC fair-access policy)"
            )
        self.http = http or httpx.Client(timeout=20.0, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"})
        self.http.headers.setdefault("User-Agent", user_agent)
        self._last = 0.0
        self._sleep = sleep

    def _throttle(self) -> None:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def get_json(self, url: str) -> Any:
        self._throttle()
        r = self.http.get(url)
        r.raise_for_status()
        return r.json()

    def ticker_map(self) -> dict[str, int]:
        data = self.get_json(TICKERS_URL)
        return {str(v["ticker"]).upper(): int(v["cik_str"]) for v in data.values()}

    def submissions(self, cik: int) -> dict[str, Any]:
        result: dict[str, Any] = self.get_json(SUBMISSIONS_URL.format(cik=cik))
        return result

    def health(self) -> SourceStamp:
        now = datetime.now(tz=UTC)
        try:
            self.http.head(TICKERS_URL).raise_for_status()
            h = SourceHealth.OK
        except Exception:
            h = SourceHealth.DOWN
        return SourceStamp(domain=self.domain, source=self.source, grade=self.grade, observed_at=now, health=h)


def last_10k_filed_on(submissions: dict[str, Any]) -> date | None:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms, dates = recent.get("form") or [], recent.get("filingDate") or []
    for form, d in zip(forms, dates, strict=False):
        if form in ("10-K", "10-K/A"):
            return date.fromisoformat(d)
    return None


def sic_of(submissions: dict[str, Any]) -> int | None:
    raw = submissions.get("sic")
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None
