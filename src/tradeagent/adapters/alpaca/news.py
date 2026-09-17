"""Alpaca news endpoint (§5.2 primary news source). Verified 2026-09-13: GET /v1beta1/news, limit ≤ 50, Benzinga-sourced."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from tradeagent.adapters.alpaca.client import MARKET_DATA_URL, AlpacaClient
from tradeagent.domain.enums import SourceHealth, TrustGrade
from tradeagent.domain.models import Headline, SourceStamp

DOMAIN = "news"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class AlpacaNews:
    domain = DOMAIN
    source = "alpaca-news"
    grade = (
        TrustGrade.EXECUTION
    )  # Alpaca-served; the *content* is still just headlines, and Jev's judgment on it is research-grade

    def __init__(self, client: AlpacaClient):
        self.client = client

    def headlines(
        self, symbols: Sequence[str], since: datetime, limit_per_page: int = 50, max_pages: int = 4
    ) -> list[Headline]:
        out: list[Headline] = []
        token: str | None = None
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "symbols": ",".join(symbols),
                "start": since.isoformat(),
                "limit": limit_per_page,
                "sort": "desc",
                "exclude_contentless": "false",
            }
            if token:
                params["page_token"] = token
            data = self.client.get("/v1beta1/news", params, base=MARKET_DATA_URL)
            for n in data.get("news") or []:
                out.append(
                    Headline(
                        id=str(n["id"]),
                        symbols=tuple(n.get("symbols") or ()),
                        headline=n.get("headline") or "",
                        summary=n.get("summary") or "",
                        source=n.get("source") or "",
                        created_at=_ts(n["created_at"]),
                        url=n.get("url") or "",
                    )
                )
            token = data.get("next_page_token")
            if not token:
                break
        return out

    def health(self) -> SourceStamp:
        now = datetime.now(tz=UTC)
        try:
            self.client.get("/v1beta1/news", {"limit": 1}, base=MARKET_DATA_URL)
            h = SourceHealth.OK
        except Exception:
            h = SourceHealth.DOWN
        return SourceStamp(domain=DOMAIN, source=self.source, grade=self.grade, observed_at=now, health=h)
