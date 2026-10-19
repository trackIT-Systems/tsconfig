"""Firmware readings through the `vcgencmd` package: PMIC, throttling, clocks, voltages.

The columns are the keys vcgencmd returns on a Raspberry Pi 5; keys a board
lacks are published as null.
"""

from typing import Any

from app.telemetry.collectors.base import Collector, Reading, Unavailable

PMIC_RAILS = (
    "3V7_WL_SW",
    "3V3_SYS",
    "1V8_SYS",
    "DDR_VDD2",
    "DDR_VDDQ",
    "1V1_SYS",
    "0V8_SW",
    "VDD_CORE",
    "3V3_DAC",
    "3V3_ADC",
    "0V8_AON",
    "HDMI",
)


class _VcgencmdCollector(Collector):
    # Name of the function in the vcgencmd package
    group: str

    def _module(self) -> Any:
        try:
            import vcgencmd
        except ImportError as e:
            # vcgencmd's own import fails when the vcgencmd binary is missing
            raise Unavailable(f"vcgencmd not available: {e}") from e
        return vcgencmd

    def check(self) -> None:
        try:
            values = getattr(self._module(), self.group)()
        except Unavailable:
            raise
        except Exception as e:
            raise Unavailable(f"vcgencmd {self.group} failed: {e}") from e
        if not values:
            raise Unavailable(f"vcgencmd {self.group} returned nothing")

    def read(self) -> list[Reading]:
        return [getattr(self._module(), self.group)()]


class PmicCollector(_VcgencmdCollector):
    name = "pmic"
    group = "pmic"
    columns = (
        *(f"{rail}_A" for rail in PMIC_RAILS),
        *(f"{rail}_V" for rail in PMIC_RAILS),
        "EXT5V_V",
        "BATT_V",
        "BAT_RTC_V",
    )


class ThrottledCollector(_VcgencmdCollector):
    name = "throttled"
    group = "throttled"
    columns = (
        "under_voltage",
        "freq_capped",
        "throttled",
        "temp_limit",
        "under_voltage_occurred",
        "freq_capped_occurred",
        "throttling_occurred",
        "temp_limit_occurred",
        "raw",
    )


class ClocksCollector(_VcgencmdCollector):
    name = "clocks"
    group = "clocks"
    columns = ("arm", "core", "h264", "hevc", "isp", "v3d", "uart", "pwm", "emmc", "pixel", "vec", "hdmi", "dpi")


class VoltagesCollector(_VcgencmdCollector):
    name = "voltages"
    group = "voltages"
    columns = ("core", "sdram_c", "sdram_i", "sdram_p")
