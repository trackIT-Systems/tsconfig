"""WittyPi 4 power board: supply and output voltage, current, board temperature."""

from typing import Any

from app.telemetry.collectors.base import Collector, Reading, Unavailable


class WittyPiCollector(Collector):
    name = "wittypi"
    columns = ("voltage_in", "voltage_out", "current_out", "power_ldo", "temperature", "firmware_revision")

    def __init__(self, settings):
        super().__init__(settings)
        self._board: Any = None

    def check(self) -> None:
        try:
            from tsschedule.backends.wittypi4 import WittyPi4
        except ImportError as e:
            raise Unavailable(f"tsschedule not installed: {e}") from e
        try:
            # Probes the board once; the instance (and its I²C bus) is kept
            self._board = WittyPi4()
        except Exception as e:
            raise Unavailable(f"no WittyPi 4: {e}") from e

    def read(self) -> list[Reading]:
        board = self._board
        return [
            {
                "voltage_in": board.voltage_in,
                "voltage_out": board.voltage_out,
                "current_out": board.current_out,
                "power_ldo": board.power_ldo,
                "temperature": board.lm75b_temperature,
                "firmware_revision": board.firmware_revision,
            }
        ]
