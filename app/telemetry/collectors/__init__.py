"""Every collector the daemon knows, in publishing order.

A new kind of data means a new module here and a matching entry in
app.telemetry.settings.CollectorsSettings; there is no plugin mechanism.
"""

from app.telemetry.collectors.base import Collector, Reading, Unavailable
from app.telemetry.collectors.configs import ConfigsCollector
from app.telemetry.collectors.disk import DiskCollector
from app.telemetry.collectors.journalstats import JournalStatsCollector
from app.telemetry.collectors.modem import ModemCollector
from app.telemetry.collectors.network import NetworkCollector
from app.telemetry.collectors.release import ReleaseCollector
from app.telemetry.collectors.system import SystemCollector
from app.telemetry.collectors.thermal import ThermalCollector
from app.telemetry.collectors.vcgencmd import ClocksCollector, PmicCollector, ThrottledCollector, VoltagesCollector
from app.telemetry.collectors.wittypi import WittyPiCollector

COLLECTORS: tuple[type[Collector], ...] = (
    SystemCollector,
    ThermalCollector,
    DiskCollector,
    PmicCollector,
    ThrottledCollector,
    ClocksCollector,
    VoltagesCollector,
    WittyPiCollector,
    NetworkCollector,
    ModemCollector,
    ConfigsCollector,
    ReleaseCollector,
    JournalStatsCollector,
)

__all__ = ["COLLECTORS", "Collector", "Reading", "Unavailable"]
