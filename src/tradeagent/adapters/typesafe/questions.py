"""Typed question sets for Jev (ADR-0023, versioned by prompts/jev). Design per the TypeSafe skill: state as named
fields, judgment in `instructions`, answer space in `criteria`, a no-match option in every Choice."""

from __future__ import annotations

from typing import Any

from typesafe_sdk import Choice, Noul, Score

CATALYST_SET = "catalyst.v1"


def catalyst_questions() -> dict[str, Noul | Choice | Score]:
    """§6.2 'guidance changes and significant news, material filings': one headline (or 8-K item) against a symbol."""
    return {
        "is_material": Noul(
            instructions=(
                "Is `headline` (with `summary` if present) a material catalyst for the company identified by `symbol` "
                "and `company_name`: new information a short-term trader would reasonably expect to move the stock's "
                "price over the next hours to days?"
            ),
            criteria={
                "true": "Company-specific news with clear price relevance: results, guidance, M&A, regulatory or legal outcomes, "
                "major contracts, product approvals or failures, financing, leadership change.",
                "false": "Generic market commentary, listicles, price-move recaps, promotional or opinion pieces, or news "
                "about a different company that merely mentions this one.",
            },
        ),
        "direction": Choice(
            instructions="If the headline is a catalyst for `symbol`, which direction would a trader expect the price reaction to be?",
            criteria={
                "bullish": "Clearly positive for the company's value or near-term sentiment",
                "bearish": "Clearly negative",
                "mixed": "Material but with offsetting positive and negative elements, or direction depends on details not given",
                "none": "Not a catalyst, or no directional implication",
            },
        ),
        "kind": Choice(
            instructions="What kind of event does the headline report for `symbol`?",
            criteria={
                "earnings": "Reported quarterly or annual results",
                "guidance": "Forward guidance raised, cut, or issued",
                "analyst": "Broker rating or price-target change",
                "m_and_a": "Acquisition, merger, divestiture, takeover interest",
                "regulatory_legal": "Regulator decision, approval, investigation, lawsuit, settlement",
                "product": "Product launch, recall, clinical or trial result, contract award",
                "financing": "Equity or debt offering, buyback, dividend change, credit event",
                "management": "Executive appointment or departure",
                "macro": "Sector or macro news affecting the company indirectly",
                "other": "Company-specific but none of the above",
                "none": "Not company-specific news",
            },
        ),
        "time_horizon": Score(
            instructions="Over what horizon would the price effect of this news for `symbol` plausibly play out?",
            criteria=[
                "Minutes: a one-off print or intraday blip with no lasting effect",
                "Hours to the end of the session",
                "Several trading days",
                "Weeks or longer: a thesis-changing event",
            ],
        ),
    }


def catalyst_state(
    symbol: str, company_name: str | None, headline: str, summary: str | None, source: str, published_at: str
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "company_name": company_name or "",
        "headline": headline,
        "summary": summary or "",
        "source": source,
        "published_at": published_at,
    }


DIGEST_SET = "digest.v1"


def digest_questions() -> dict[str, Noul | Choice | Score]:
    """ADR-0023 analytics tagging: one judgment over the day's statistics so the digest subject line says what kind of
    day it was and whether the owner must act. The statistics, not the judgment, remain the record."""
    return {
        "day_character": Choice(
            instructions=(
                "Given the day's counts (`scans`, `decisions`, `rejections`, `fills`, `closed_trades`, `halts`, "
                "`open_halts`, `budget_entries_spent_pct`, `equity_change_pct`), what kind of trading day was this "
                "for a small experimental portfolio?"
            ),
            criteria={
                "quiet": "Scans ran but there was little or no trading activity and nothing went wrong",
                "routine": "Some decisions and fills, all within normal limits, no incidents",
                "active": "Many decisions or fills relative to the limits, still without incidents",
                "eventful": "Incidents worth reading about: halts, reconciliation repairs, stop incidents, or a large equity move",
                "degraded": "The system was halted or a required data source was down for a material part of the session",
            },
        ),
        "owner_attention_needed": Noul(
            instructions=(
                "Do these statistics indicate the owner must act before the next session (clear a halt, resolve a "
                "reconciliation, fund nothing — deposits are prohibited — or review a budget exhaustion)?"
            ),
            criteria={
                "true": "There is an uncleared halt, an unexplained reconciliation difference, a stop incident, or the entry budget is exhausted early in the month",
                "false": "Everything is within normal operation; the digest is informational",
            },
        ),
    }


def digest_state(stats: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "scans",
        "decisions",
        "rejections",
        "fills",
        "closed_trades",
        "halts",
        "open_halts",
        "budget_entries_spent_pct",
        "equity_change_pct",
    )
    return {k: stats.get(k) for k in keys}
