"""State, addresses and traffic counters of the configured network interfaces, one reading each.

An interface that does not exist (WireGuard not configured, no modem) is
reported with `present` false instead of being left out, so its absence shows.
"""

import ipaddress
import socket

import psutil

from app.telemetry.collectors.base import Collector, Reading


def _global_addresses(addresses, family) -> str | None:
    """Space-separated addresses of one family, without link-local ones."""
    found = []
    for address in addresses:
        if address.family != family:
            continue
        ip = address.address.split("%")[0]
        try:
            if ipaddress.ip_address(ip).is_link_local:
                continue
        except ValueError:
            continue
        found.append(ip)
    return " ".join(found) or None


class NetworkCollector(Collector):
    name = "network"
    columns = ("interface", "present", "up", "ipv4", "ipv6", "rx_bytes", "tx_bytes")

    def read(self) -> list[Reading]:
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
        readings = []
        for interface in self.settings.interfaces:
            if interface not in addresses and interface not in stats:
                readings.append({"interface": interface, "present": False})
                continue
            reading: Reading = {"interface": interface, "present": True}
            if interface in stats:
                reading["up"] = stats[interface].isup
            reading["ipv4"] = _global_addresses(addresses.get(interface, []), socket.AF_INET)
            reading["ipv6"] = _global_addresses(addresses.get(interface, []), socket.AF_INET6)
            if interface in counters:
                reading["rx_bytes"] = counters[interface].bytes_recv
                reading["tx_bytes"] = counters[interface].bytes_sent
            readings.append(reading)
        return readings
