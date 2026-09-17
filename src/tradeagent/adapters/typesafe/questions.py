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
