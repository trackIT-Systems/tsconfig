"""Uptime, load, memory and CPU frequency."""

from pathlib import Path

import psutil

from app.telemetry.collectors.base import Collector, Reading


class SystemCollector(Collector):
    name = "system"
    columns = (
        "uptime",
        "load_1",
        "load_5",
        "load_15",
        "mem_total",
        "mem_available",
        "mem_used",
        "mem_percent",
        "mem_free",
        "mem_active",
        "mem_inactive",
        "mem_buffers",
        "mem_cached",
        "mem_shared",
        "mem_slab",
        "cpu_freq",
        "cpu_freq_min",
        "cpu_freq_max",
    )

    proc_uptime = Path("/proc/uptime")

    def read(self) -> list[Reading]:
        # From /proc/uptime, not psutil.boot_time(): that one is derived from
        # the wall clock, which may still be wrong
        uptime = float(self.proc_uptime.read_text().split()[0])
        load_1, load_5, load_15 = psutil.getloadavg()
        reading: Reading = {
            "uptime": uptime,
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
        }
        # Every field psutil has on this platform (Linux: all of the columns)
        reading |= {f"mem_{key}": value for key, value in psutil.virtual_memory()._asdict().items()}
        freq = psutil.cpu_freq()
        if freq:
            reading |= {"cpu_freq": freq.current, "cpu_freq_min": freq.min, "cpu_freq_max": freq.max}
        return [reading]
