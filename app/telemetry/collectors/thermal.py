"""SoC, RP1 and PMIC temperatures in °C."""

import psutil

from app.telemetry.collectors.base import Collector, Reading, Unavailable

# psutil sensor name (the hwmon `name`, independent of the hwmon number) -> column
SENSORS = {"cpu_thermal": "soc", "rp1_adc": "rp1"}


class ThermalCollector(Collector):
    name = "thermal"
    columns = ("soc", "rp1", "pmic")

    @staticmethod
    def _sensors() -> dict:
        # psutil has no sensors_temperatures() on macOS
        return getattr(psutil, "sensors_temperatures", dict)()

    def check(self) -> None:
        if not self._sensors() and self._vcgencmd() is None:
            raise Unavailable("no temperature sensors")

    @staticmethod
    def _vcgencmd():
        try:
            import vcgencmd
        except ImportError:
            return None
        return vcgencmd

    def read(self) -> list[Reading]:
        reading: Reading = {}
        for sensor, entries in self._sensors().items():
            if sensor in SENSORS and entries:
                reading[SENSORS[sensor]] = entries[0].current
        vcgencmd = self._vcgencmd()
        if vcgencmd is not None:
            # Only vcgencmd reads the PMIC's sensor (Raspberry Pi 5)
            reading["pmic"] = vcgencmd.temperature().get("pmic")
        return [reading]
