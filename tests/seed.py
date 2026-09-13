from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

NOW = datetime(2026, 9, 14, 14, 30, tzinfo=UTC)


@dataclass
class Seed:
    conn: object
    experiment_id: uuid.UUID
    phase_id: uuid.UUID
    primary_id: uuid.UUID
    shadow_id: uuid.UUID
    scan_id: uuid.UUID
    candidate_id: uuid.UUID
    versions: dict

    @classmethod
    def create(cls, conn) -> Seed:
        v = {
            "prompt_version": "p1.0.0",
            "config_version": "cfg-" + uuid.uuid4().hex[:8],
            "scanner_version": "1.0.0",
            "qb_rules_version": "1.0.0",
            "exclusion_list_version": "2026.09.13-seed",
        }
        cur = conn.cursor()
        cur.execute("set search_path = trading, public")
        cur.execute(
            "insert into prompt_versions (version, content_hash, changelog) values (%s, 'h', 'initial') on conflict do nothing",
            (v["prompt_version"],),
        )
        cur.execute(
            "insert into config_versions (version, content) values (%s, '{}') on conflict do nothing",
            (v["config_version"],),
        )
        cur.execute(
            "insert into scanner_versions (version, content) values (%s, '{}') on conflict do nothing",
            (v["scanner_version"],),
        )
        cur.execute(
            "insert into qb_rules_versions (version, content) values (%s, '{}') on conflict do nothing",
            (v["qb_rules_version"],),
        )
        cur.execute(
            "insert into exclusion_list_versions (version, content_hash, content, entry_count) values (%s, 'h', '{}', 0) on conflict do nothing",
            (v["exclusion_list_version"],),
        )
        exp = uuid.uuid4()
        cur.execute(
            "insert into experiments (id, name, execution_mode, equity_start_usd, started_on) values (%s, %s, 'DRY_RUN', 500, '2026-09-14')",
            (exp, f"exp-{exp.hex[:6]}"),
        )
        phase = uuid.uuid4()
        cur.execute(
            """insert into experiment_phases (id, experiment_id, seq, category, reason, prompt_version, config_version, scanner_version,
               qb_rules_version, exclusion_list_version, model_triage, model_decision, model_critique, migration_version, code_version)
               values (%s, %s, 1, 'initial', 'seed', %s, %s, %s, %s, %s, 'claude-haiku-4-5', 'claude-sonnet-5', 'claude-sonnet-5', '20260913000008', 'phase0')""",
            (
                phase,
                exp,
                v["prompt_version"],
                v["config_version"],
                v["scanner_version"],
                v["qb_rules_version"],
                v["exclusion_list_version"],
            ),
        )
        primary, shadow = uuid.uuid4(), uuid.uuid4()
        cur.execute(
            "insert into portfolios (id, experiment_id, kind, name, touches_broker, equity_start_usd) values (%s, %s, 'llm_primary', 'primary', true, 500)",
            (primary, exp),
        )
        cur.execute(
            "insert into portfolios (id, experiment_id, kind, name, touches_broker, equity_start_usd) values (%s, %s, 'quant_shadow', 'qb-1.0', false, 500)",
            (shadow, exp),
        )
        for pid, key in ((primary, "init-primary"), (shadow, "init-shadow")):
            cur.execute(
                "insert into cash_ledger (portfolio_id, experiment_id, experiment_phase_id, kind, amount_usd, balance_after_usd, idempotency_key) values (%s, %s, %s, 'initial_equity', 500, 500, %s)",
                (pid, exp, phase, f"{key}-{pid}"),
            )
        cur.execute(
            "insert into instruments (symbol, name, tradable, fractionable, universe_status) values ('AAPL', 'Apple', true, true, 'eligible') on conflict do nothing"
        )
        cur.execute(
            "insert into instruments (symbol, name, tradable, fractionable, universe_status, status_reason) values ('XOM', 'Exxon', true, true, 'excluded_ethical', 'denylist') on conflict do nothing"
        )
        scan = uuid.uuid4()
        cur.execute(
            """insert into scans (id, experiment_id, experiment_phase_id, kind, started_at, scanner_completed_at, bars_end_at, scanner_version,
               exclusion_list_version, universe_size, candidate_count, source_status)
               values (%s, %s, %s, 'intraday', %s, %s, %s, %s, %s, 1500, 1, '[]')""",
            (
                scan,
                exp,
                phase,
                NOW,
                NOW + timedelta(seconds=20),
                NOW - timedelta(minutes=16),
                v["scanner_version"],
                v["exclusion_list_version"],
            ),
        )
        cand = uuid.uuid4()
        cur.execute(
            """insert into candidates (id, scan_id, symbol, rank, composite_score, signals, signal_bar_time, signal_observed_at, sip_signal_price, sip_signal_timestamp)
               values (%s, %s, 'AAPL', 1, 0.8, '{}', %s, %s, 100.00, %s)""",
            (cand, scan, NOW - timedelta(minutes=16), NOW, NOW - timedelta(minutes=16)),
        )
        return cls(conn, exp, phase, primary, shadow, scan, cand, v)

    def decision(
        self,
        *,
        portfolio_id=None,
        decision="BUY",
        status="validated",
        signal_observed_at=NOW,
        eligible_at=NOW + timedelta(minutes=4),
        **extra,
    ) -> uuid.UUID:
        did = uuid.uuid4()
        cols = dict(
            decision_id=did,
            experiment_id=self.experiment_id,
            experiment_phase_id=self.phase_id,
            portfolio_id=portfolio_id or self.primary_id,
            scan_id=self.scan_id,
            candidate_id=self.candidate_id,
            origin="llm",
            kind="entry",
            status=status,
            decision=decision,
            ticker="AAPL",
            direction="long",
            proposed_notional=100,
            target_price=110,
            invalidation_price=95,
            time_stop_at=NOW + timedelta(days=5),
            expected_holding_period=timedelta(days=2),
            confidence=0.6,
            forecast_target_price_at_entry=110,
            forecast_invalidation_price_at_entry=95,
            forecast_time_stop_at_entry=NOW + timedelta(days=5),
            probability_pre_critique=0.55,
            probability_post_critique=0.50,
            signal_observed_at=signal_observed_at,
            scanner_completed_at=NOW + timedelta(seconds=20),
            decision_completed_at=NOW + timedelta(minutes=3),
            critique_completed_at=NOW + timedelta(minutes=3, seconds=30),
            risk_validation_completed_at=eligible_at,
            order_eligible_at=eligible_at,
            **self.versions,
        )
        cols.update(extra)
        keys = list(cols)
        self.conn.execute(
            f"insert into decisions ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))})",
            [cols[k] for k in keys],
        )
        return did

    def order(
        self,
        decision_id,
        *,
        portfolio_id=None,
        is_simulated=True,
        eligible_at=NOW + timedelta(minutes=4),
        leg_seq=1,
        side="buy",
        purpose="entry",
        **extra,
    ) -> uuid.UUID:
        oid = uuid.uuid4()
        cols = dict(
            id=oid,
            decision_id=decision_id,
            portfolio_id=portfolio_id or self.primary_id,
            experiment_id=self.experiment_id,
            experiment_phase_id=self.phase_id,
            client_order_id=f"{decision_id}:{leg_seq}",
            leg_seq=leg_seq,
            purpose=purpose,
            symbol="AAPL",
            side=side,
            order_type="market",
            time_in_force="day",
            notional=100,
            is_simulated=is_simulated,
            order_eligible_at=eligible_at,
        )
        cols.update(extra)
        if cols["side"] == "buy" and "reserved_notional_usd" not in cols:
            cols["reserved_notional_usd"] = cols.get("notional") or 100
        keys = list(cols)
        self.conn.execute(
            f"insert into orders ({', '.join(keys)}) values ({', '.join(['%s'] * len(keys))})", [cols[k] for k in keys]
        )
        return oid

    def transition(self, order_id, to_status, reason="test"):
        self.conn.execute(
            "update orders set status = %s, status_reason = %s where id = %s", (to_status, reason, order_id)
        )

    def fill(
        self,
        order_id,
        *,
        portfolio_id=None,
        fill_at,
        source="reconstructed",
        basis="ask",
        broker_fill_id=None,
        half_spread=None,
        side="buy",
    ) -> uuid.UUID:
        fid = uuid.uuid4()
        self.conn.execute(
            """insert into fills (id, order_id, portfolio_id, experiment_id, experiment_phase_id, symbol, side, qty, price, notional, fill_at, fill_source,
               broker_fill_id, reconstruction_basis, half_spread_estimate) values (%s, %s, %s, %s, %s, 'AAPL', %s, 1, 100, 100, %s, %s, %s, %s, %s)""",
            (
                fid,
                order_id,
                portfolio_id or self.primary_id,
                self.experiment_id,
                self.phase_id,
                side,
                fill_at,
                source,
                broker_fill_id,
                basis,
                half_spread,
            ),
        )
        return fid
