"""§8.7 halts: recorded in `halts`, emailed, and consulted by the risk desk and the scan cycle."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from tradeagent.domain.enums import HaltScope
from tradeagent.ops.notifications import Notifier
from tradeagent.persistence.db import Database

log = logging.getLogger("tradeagent.halts")


@dataclass(frozen=True)
class HaltState:
    all_halted: bool
    entries_halted: bool
    codes: tuple[str, ...]


class Halter:
    def __init__(self, db: Database, notifier: Notifier | None, experiment_id: UUID):
        self.db, self.notifier, self.experiment_id = db, notifier, experiment_id

    def halt(self, code: str, scope: HaltScope, detail: dict[str, Any]) -> UUID:
        hid = self.db.record_halt(self.experiment_id, code, scope, detail)
        log.error("HALT %s (%s): %s", code, scope.value, detail)
        if self.notifier is not None:
            body = f"Halt {code} (scope {scope.value}).\n\n{json.dumps(detail, indent=2, default=str)}\n\nClear it in the halts table once resolved."
            self.notifier.send("halt", f"HALT {code}", body, payload={"halt_id": str(hid), "code": code, **detail})
        return hid

    def state(self) -> HaltState:
        rows = self.db.open_halts(self.experiment_id)
        scopes = {str(r["scope"]) for r in rows}
        return HaltState(
            all_halted=HaltScope.ALL.value in scopes,
            entries_halted=bool(scopes),
            codes=tuple(str(r["code"]) for r in rows),
        )
