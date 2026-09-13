"""Broker selection by EXECUTION_MODE (ADR-0020). There is no LIVE implementation and no LIVE credential lookup."""

from __future__ import annotations

from tradeagent.adapters.alpaca.broker_null import NullBroker
from tradeagent.adapters.alpaca.client import AlpacaClient, AlpacaCredentials
from tradeagent.config import Settings
from tradeagent.domain.enums import ExecutionMode
from tradeagent.interfaces import Broker, LiveLockedOutError


class PaperBrokerNotYetImplemented(NotImplementedError):
    """AlpacaPaperBroker arrives in Slice 3."""


def paper_credentials(env: dict[str, str]) -> AlpacaCredentials | None:
    key, secret = env.get("ALPACA_PAPER_KEY"), env.get("ALPACA_PAPER_SECRET")
    if key and secret:
        return AlpacaCredentials(key, secret)
    return None


def make_broker(settings: Settings, env: dict[str, str]) -> Broker:
    mode = settings.risk.execution.execution_mode
    if mode == ExecutionMode.LIVE:
        raise LiveLockedOutError("LIVE is not enabled in this codebase (ADR-0020)")
    if mode == ExecutionMode.DRY_RUN:
        creds = paper_credentials(env)
        return NullBroker(AlpacaClient(creds) if creds else None)
    raise PaperBrokerNotYetImplemented("PAPER execution arrives in Slice 3")
