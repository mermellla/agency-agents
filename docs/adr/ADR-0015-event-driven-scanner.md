# ADR-0015 — Event-driven scanner interface and what changes when MARKET_DATA_PLAN flips

**Appendix C item:** 15 · **Status:** Accepted (designed, not built) · **Date:** 2026-09-13

## Context
§5.6 and §13.6: SIP_REALTIME is architected now so that buying Algo Trader Plus is a config change and a new phase,
restoring the §6.3 signals and allowing an event-driven scanner.

## Decision
Interfaces (`src/tradeagent/interfaces/__init__.py`): `MarketDataAdapter` (tiered; the SIP_DELAYED adapter refuses
`end > now − 15 min`, the SIP_REALTIME adapter has no embargo), `MarketStream` (tier + `max_symbols`), `Scanner.scan`
(polling) and `EventDrivenScanner.on_event` (returns a `ScanResult` when a symbol's signals cross the triage threshold).
The signal library is tier-aware: each signal declares the tier it needs; on a lower tier it is reported as
`signal_unavailable_reason`, never silently computed from IEX volume.

What flips with `MARKET_DATA_PLAN = algo_trader_plus`:
| Component | basic | algo_trader_plus |
|---|---|---|
| Bars/quotes adapter | `SIP_DELAYED` (+ `IEX_REALTIME` reference) | `SIP_REALTIME` for both scanning and reference |
| Stream | `wss://stream.data.alpaca.markets/v2/iex`, cap 30 | `/v2/sip`, no cap |
| Scanner mode | polling every `SCAN_INTERVAL_MIN` | event-driven (`on_event`) with a polling backstop every 15 min |
| §6.3 signals | unavailable (recorded) | intraday relative volume, real-time breakouts, pre/post-market movers enabled |
| `signal_observed_at` | bar end + 15 min + fetch | bar end + fetch |
| Shadow fill reconstruction | after `SIM_FILL_DELAY_MIN` | after `sim_fill_delay_margin_min` only |
| `LATENCY_FLOOR_MIN` | 30 | re-derived (≈ 10) — a config change, new ADR |
| Cost ledger | `market_data_cost = 0` | `$99/month` prorated |

Unchanged: scoring code (ADR-0008), regime code, risk desk, ledger, models, prompts. The §17 switch test asserts that
substituting a mocked `SIP_REALTIME` adapter and stream changes no module under `tradeagent/scanner/` (import-graph
and file-hash check), only configuration and adapter registration.

## Alternatives considered
- Two scanners (polling and event-driven) as separate code paths: guarantees divergence over time.
- Buying the plan now: rejected by D-49/D-59.

## Consequences
- A plan flip opens a phase (category `strategy`) and restores signals as a recorded change.
- The delayed_sip WebSocket stream could replace REST polling for the focus set on basic; not adopted in V1 because it
  gives no information REST does not (same 15-minute delay).
