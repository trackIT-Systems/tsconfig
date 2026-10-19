"""Collectors against faked system sources."""

import socket
import sys
import types
from collections import namedtuple

import psutil
import pytest

from app.system.modem import ModemInfo
from app.telemetry.collectors import Unavailable
from app.telemetry.collectors import configs as configs_collector
from app.telemetry.collectors import modem as modem_collector
from app.telemetry.collectors.configs import ConfigsCollector
from app.telemetry.collectors.disk import DiskCollector
from app.telemetry.collectors.modem import ModemCollector
from app.telemetry.collectors.network import NetworkCollector
from app.telemetry.collectors.release import ReleaseCollector
from app.telemetry.collectors.system import SystemCollector
from app.telemetry.collectors.thermal import ThermalCollector
from app.telemetry.collectors.vcgencmd import PmicCollector, ThrottledCollector
from app.telemetry.collectors.wittypi import WittyPiCollector
from app.telemetry.settings import CollectorsSettings

SETTINGS = CollectorsSettings()
Temp = namedtuple("Temp", "label current high critical")
Addr = namedtuple("Addr", "family address netmask broadcast ptp")
Stats = namedtuple("Stats", "isup")
Counters = namedtuple("Counters", "bytes_recv bytes_sent")


@pytest.fixture
def fake_vcgencmd(monkeypatch):
    module = types.ModuleType("vcgencmd")
    module.temperature = lambda: {"soc": 49.4, "pmic": 46.5}
    module.pmic = lambda: {"VDD_CORE_A": 0.67, "EXT5V_V": 5.14, "NEW_RAIL_V": 1.0}
    module.throttled = lambda: {"under_voltage": False, "raw": 0}
    monkeypatch.setitem(sys.modules, "vcgencmd", module)
    return module


@pytest.fixture
def no_vcgencmd(monkeypatch):
    # None in sys.modules makes `import vcgencmd` raise ImportError
    monkeypatch.setitem(sys.modules, "vcgencmd", None)


def test_system_reads_uptime_from_proc_not_the_wall_clock(tmp_path, monkeypatch):
    uptime = tmp_path / "uptime"
    uptime.write_text("19980.97 75000.00\n")
    monkeypatch.setattr(SystemCollector, "proc_uptime", uptime)
    monkeypatch.setattr(psutil, "getloadavg", lambda: (0.219, 0.235, 0.212))
    (reading,) = SystemCollector(SETTINGS.system).read()
    assert reading["uptime"] == 19980.97
    assert (reading["load_1"], reading["load_5"], reading["load_15"]) == (0.22, 0.23, 0.21)
    assert reading["mem_total"] > 0


def test_thermal_finds_sensors_by_name_not_hwmon_number(monkeypatch, fake_vcgencmd):
    sensors = {"rp1_adc": [Temp("", 46.2, None, None)], "cpu_thermal": [Temp("", 50.7, None, None)]}
    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: sensors, raising=False)
    assert ThermalCollector(SETTINGS.thermal).read() == [{"soc": 50.7, "rp1": 46.2, "pmic": 46.5}]


def test_thermal_without_any_sensor_is_unavailable(monkeypatch, no_vcgencmd):
    monkeypatch.setattr(psutil, "sensors_temperatures", dict, raising=False)
    with pytest.raises(Unavailable):
        ThermalCollector(SETTINGS.thermal).check()


def test_disk_skips_mount_points_that_are_not_mounted(monkeypatch):
    settings = SETTINGS.disk.model_copy(update={"mounts": ["/", "/not-mounted"]})
    readings = DiskCollector(settings).read()
    assert [reading["mount"] for reading in readings] == ["/"]


def test_network_reports_missing_interfaces_as_not_present(monkeypatch):
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "wlan0": [
                Addr(socket.AF_INET, "192.168.1.5", None, None, None),
                Addr(socket.AF_INET6, "fe80::1%wlan0", None, None, None),
                Addr(socket.AF_INET6, "2001:db8::5", None, None, None),
            ]
        },
    )
    monkeypatch.setattr(psutil, "net_if_stats", lambda: {"wlan0": Stats(True)})
    monkeypatch.setattr(psutil, "net_io_counters", lambda pernic: {"wlan0": Counters(10, 20)})
    settings = SETTINGS.network.model_copy(update={"interfaces": ["wireguard", "wlan0"]})
    assert NetworkCollector(settings).read() == [
        {"interface": "wireguard", "present": False},
        {
            "interface": "wlan0",
            "present": True,
            "up": True,
            "ipv4": "192.168.1.5",
            "ipv6": "2001:db8::5",
            "rx_bytes": 10,
            "tx_bytes": 20,
        },
    ]


def test_vcgencmd_collectors_read_their_group(fake_vcgencmd):
    pmic = PmicCollector(SETTINGS.pmic)
    pmic.check()
    (reading,) = pmic.read()
    assert reading["VDD_CORE_A"] == 0.67
    assert "VDD_CORE_A" in PmicCollector.columns and "NEW_RAIL_V" not in PmicCollector.columns
    assert ThrottledCollector(SETTINGS.throttled).read() == [{"under_voltage": False, "raw": 0}]


def test_vcgencmd_collectors_are_unavailable_without_vcgencmd(no_vcgencmd):
    with pytest.raises(Unavailable):
        PmicCollector(SETTINGS.pmic).check()


def test_pmic_is_unavailable_when_the_board_has_none(fake_vcgencmd):
    def no_pmic():
        raise RuntimeError("PMIC not available on this board")

    fake_vcgencmd.pmic = no_pmic
    with pytest.raises(Unavailable):
        PmicCollector(SETTINGS.pmic).check()


def test_wittypi_is_unavailable_without_the_board(monkeypatch):
    backends = types.ModuleType("tsschedule.backends.wittypi4")

    class WittyPi4:
        def __init__(self):
            raise OSError("error reading address 0x08")

    backends.WittyPi4 = WittyPi4
    monkeypatch.setitem(sys.modules, "tsschedule.backends.wittypi4", backends)
    with pytest.raises(Unavailable, match="no WittyPi 4"):
        WittyPiCollector(SETTINGS.wittypi).check()


def test_wittypi_keeps_one_board_instance(monkeypatch):
    backends = types.ModuleType("tsschedule.backends.wittypi4")
    created = []

    class WittyPi4:
        voltage_in, voltage_out, current_out, power_ldo = 12.1, 5.1, 0.6, False
        lm75b_temperature, firmware_revision = 31.5, 7

        def __init__(self):
            created.append(self)

    backends.WittyPi4 = WittyPi4
    monkeypatch.setitem(sys.modules, "tsschedule.backends.wittypi4", backends)
    collector = WittyPiCollector(SETTINGS.wittypi)
    collector.check()
    collector.read()
    (reading,) = collector.read()
    assert len(created) == 1
    assert reading["voltage_in"] == 12.1 and reading["temperature"] == 31.5


@pytest.mark.asyncio
async def test_modem_flattens_the_ip_addresses(monkeypatch):
    async def read_modem():
        return ModemInfo(state="connected", ip_addresses=["10.0.0.2", "2001:db8::2"])

    monkeypatch.setattr(modem_collector, "read_modem", read_modem)
    (reading,) = await ModemCollector(SETTINGS.modem).collect()
    assert reading["state"] == "connected"
    assert reading["ip_addresses"] == "10.0.0.2 2001:db8::2"


@pytest.mark.asyncio
async def test_no_modem_means_no_reading(monkeypatch):
    async def read_modem():
        return None

    monkeypatch.setattr(modem_collector, "read_modem", read_modem)
    assert await ModemCollector(SETTINGS.modem).collect() == []


def test_configs_lists_every_config_file(tmp_path, monkeypatch):
    from app.configs import registry

    (tmp_path / "schedule.yml").write_text("x: 1\n")

    class Config:
        def __init__(self, basename):
            self.config_file = tmp_path / basename

    monkeypatch.setattr(
        registry,
        "get_privileged_config_instance",
        lambda config_type: Config(registry.CONFIG_TYPES[config_type]["basename"]),
    )
    readings = {reading["file"]: reading for reading in ConfigsCollector(SETTINGS.configs).read()}
    assert set(readings) == set(registry.RECOGNIZED_CONFIG_FILES)
    assert readings["schedule.yml"]["present"] is True and readings["schedule.yml"]["size"] == 5
    assert readings["radiotracking.ini"] == {"file": "radiotracking.ini", "present": False}
    assert configs_collector.ConfigsCollector.columns == ("file", "present", "mtime", "size")


def test_release_reads_os_release(monkeypatch):
    import platform

    monkeypatch.setattr(
        platform, "freedesktop_os_release", lambda: {"NAME": "tsOS", "VERSION_ID": "2026.10.0", "VARIANT_ID": "base"}
    )
    (reading,) = ReleaseCollector(SETTINGS.release).read()
    assert (reading["name"], reading["version"], reading["variant"]) == ("tsOS", "2026.10.0", "base")
    assert reading["kernel"]
