"""Calendar-driven in-process scheduler (§11). Slice 1 registers no jobs; it computes the session timeline so later
slices attach jobs at named offsets. All times ET internally, stored UTC."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from tradeagent.adapters.alpaca.calendar import ET, Session, StaticCalendar

log = logging.getLogger("tradeagent.scheduler")

Job = Callable[[datetime], Awaitable[None]]


@dataclass(frozen=True)
class ScheduledEvent:
    at: datetime
    name: str


@dataclass
class SessionPlan:
    session: Session
    events: list[ScheduledEvent] = field(default_factory=list)


# Offsets relative to the session open/close (ADR-0013 §"Sequence each session"; §6; §7.5; §9).
OFFSETS_FROM_OPEN: tuple[tuple[str, timedelta], ...] = (
    ("corporate_actions", timedelta(minutes=-25)),
    ("stop_rearm", timedelta(minutes=-20)),
    ("preopen_scan", timedelta(minutes=-10)),
    ("post_open_verify_1", timedelta(minutes=1)),
    ("post_open_verify_2", timedelta(minutes=3)),
)
OFFSETS_FROM_CLOSE: tuple[tuple[str, timedelta], ...] = (
    ("daily_reviews", timedelta(minutes=5)),
    ("post_close_jobs", timedelta(minutes=30)),
)
SCAN_FIRST_OFFSET = timedelta(
    minutes=30
)  # first intraday scan at open + 30 min (first 15-min SIP bar retrievable ~10:00, §6.2)


def plan_session(session: Session, scan_interval_min: int) -> SessionPlan:
    plan = SessionPlan(session)
    for name, off in OFFSETS_FROM_OPEN:
        plan.events.append(ScheduledEvent(session.open_at + off, name))
    t = session.open_at + SCAN_FIRST_OFFSET
    while t < session.close_at:
        plan.events.append(ScheduledEvent(t, "intraday_scan"))
        t += timedelta(minutes=scan_interval_min)
    for name, off in OFFSETS_FROM_CLOSE:
        plan.events.append(ScheduledEvent(session.close_at + off, name))
    plan.events.sort(key=lambda e: e.at)
    return plan


class Scheduler:
    def __init__(self, calendar: StaticCalendar, scan_interval_min: int, clock: Callable[[], datetime] | None = None):
        self.calendar = calendar
        self.scan_interval_min = scan_interval_min
        self.clock = clock or (lambda: datetime.now(tz=ET))
        self.jobs: dict[str, Job] = {}

    def register(self, name: str, job: Job) -> None:
        self.jobs[name] = job

    def next_event(self, now: datetime) -> ScheduledEvent:
        """The first event strictly after `now`, skipping non-session days (holidays, weekends)."""
        d = now.astimezone(ET).date()
        for _ in range(30):
            s = self.calendar.session_on(d)
            if s is not None:
                for e in plan_session(s, self.scan_interval_min).events:
                    if e.at > now:
                        return e
            d = d + timedelta(days=1)
        raise LookupError("no scheduled event within 30 days")

    def sessions_this_week(self, today: date) -> list[Session]:
        start = today - timedelta(days=today.weekday())
        return self.calendar.sessions_between(start, start + timedelta(days=6))

    async def run_once(self, now: datetime) -> ScheduledEvent:
        e = self.next_event(now)
        delay = (e.at - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        job = self.jobs.get(e.name)
        if job is None:
            log.info("no job registered for %s at %s (Slice 1)", e.name, e.at.isoformat())
        else:
            await job(e.at)
        return e

    async def run_forever(self) -> None:
        while True:
            await self.run_once(self.clock())
