"""What the daemon knows about the station's position in `stationary` mode, and when it needs a fine fix.

Kept apart from the receiver and the outputs, so the rules of docs/location.md
("stationary", "Coarse check") can be tested without hardware:

* `candidates` are the latest fine fixes that agree with each other within their
  accuracy, at most `settle_count`: the run of fixes at the station's current
  position, whatever the reference says. A fix that does not agree with them
  starts a new run.
* The state follows from the latest fix and the reference: `confirmed` within
  `threshold`, else `proposed` once the run is `settle_count` long, else
  `deviating`. A station without a reference counts as deviating, so its fixes
  settle into a proposed reference too.
* `cells` are the serving cells seen at the current position. Entering
  `deviating`, or a new run while deviating, starts a new list.

The whole state is kept in /var/lib/tslocation/state.json, so a station that
boots several times a day continues where it stopped.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.location.geo import Fix, Position, agree, average, distance, write_atomic
from app.location.modem import Cell
from app.location.settings import LocationSettings

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"
CONFIRMED = "confirmed"
DEVIATING = "deviating"
PROPOSED = "proposed"
TRACKING = "tracking"
NO_FIX = "no_fix"


@dataclass
class Tracker:
    settings: LocationSettings
    state: str = UNKNOWN
    # The latest fine fix
    fix: Fix | None = None
    candidates: list[Fix] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    # Whether the latest fine fix was away from the reference; kept through no_fix
    deviated: bool = False

    # --- the state

    @property
    def proposed(self) -> Fix | None:
        if self.state == PROPOSED:
            return average(self.candidates)
        return None

    def deviation(self, reference: Position | None) -> float | None:
        if self.fix is None or reference is None:
            return None
        return distance(self.fix, reference)

    def _evaluate(self, reference: Position | None) -> str:
        if self.fix is None:
            return UNKNOWN
        deviation = self.deviation(reference)
        if deviation is not None and deviation <= self.settings.threshold:
            return CONFIRMED
        if len(self.candidates) >= self.settings.settle_count:
            return PROPOSED
        return DEVIATING

    def known(self, cell: Cell) -> bool:
        return cell in self.cells

    # --- events

    def on_fix(self, fix: Fix, reference: Position | None, cell: Cell | None) -> None:
        """A fine fix was made; `cell` is the serving cell at that time (None without a modem)."""
        was_deviating = self.deviated
        run = [*self.candidates, fix]
        while not agree(run):
            run.pop(0)
        moved = len(run) == 1 and bool(self.candidates)
        self.candidates = run[-self.settings.settle_count :]
        self.fix = fix
        self.state = self._evaluate(reference)
        self.deviated = self.state in (DEVIATING, PROPOSED)
        if self.deviated and (not was_deviating or moved):
            if self.cells:
                logger.info("Position changed, forgetting %d known cells", len(self.cells))
            self.cells = []
        if cell is not None and cell not in self.cells:
            self.cells.append(cell)

    def on_no_fix(self) -> None:
        self.state = NO_FIX

    def on_reference(self, reference: Position | None) -> None:
        """The reference changed (adopted on the server): the latest fix is judged against the new one."""
        if self.fix is None:
            return
        state = self._evaluate(reference)
        self.deviated = state in (DEVIATING, PROPOSED)
        if self.state != NO_FIX:
            self.state = state

    # --- when a fine fix is due

    def fix_age(self, now: float) -> float | None:
        if self.fix is None or self.fix.time is None:
            return None
        return now - self.fix.time.timestamp()

    def due(self, now: float) -> str | None:
        """Why a fine fix is due by its age, or None; `now` is Unix time."""
        age = self.fix_age(now)
        if age is None:
            return "there is no fine fix yet"
        if self.deviated and age > self.settings.recheck_interval:
            return f"the station is deviating and the last fine fix is {age / 3600:.1f} h old"
        if age > self.settings.fix_max_age:
            return f"the last fine fix is {age / 86400:.1f} d old"
        return None

    # --- state.json

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "deviated": self.deviated,
            "fix": self.fix.as_dict() if self.fix else None,
            "candidates": [f.as_dict() for f in self.candidates],
            "cells": [c.as_dict() for c in self.cells],
        }

    @classmethod
    def from_dict(cls, settings: LocationSettings, data: dict[str, Any]) -> "Tracker":
        state = data.get("state", UNKNOWN)
        return cls(
            settings,
            state=state if state in (UNKNOWN, CONFIRMED, DEVIATING, PROPOSED, NO_FIX) else UNKNOWN,
            fix=Fix.from_dict(data["fix"]) if data.get("fix") else None,
            candidates=[Fix.from_dict(f) for f in data.get("candidates", [])][-settings.settle_count :],
            cells=[Cell(**c) for c in data.get("cells", [])],
            deviated=bool(data.get("deviated", False)),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, json.dumps(self.as_dict(), indent=1) + "\n")

    @classmethod
    def load(cls, settings: LocationSettings, path: Path) -> "Tracker":
        """The state in `path`; a missing or broken file starts from nothing."""
        try:
            return cls.from_dict(settings, json.loads(path.read_text()))
        except FileNotFoundError:
            return cls(settings)
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning("Ignoring %s, starting without a known position: %s", path, e)
            return cls(settings)
