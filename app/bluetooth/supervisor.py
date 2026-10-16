"""Keeps the gateway registered with BlueZ and tells systemd it is healthy.

BlueZ forgets everything the gateway registered (GATT application, advertisement,
pairing agent) when bluetoothd restarts or crashes, and drops an advertisement for
other reasons too. The gateway process itself keeps running, so without this the
device silently stops being found. The `Supervisor` decides what has to be
(re)registered and when; the D-Bus calls themselves stay in `BleGattServer`.
It also feeds the systemd watchdog: pings continue while the gateway is healthy or
still recovering, and stop when recovery keeps failing, so systemd restarts it.
"""

import os
import socket
from collections.abc import Callable

from app.logging_config import get_logger

logger = get_logger(__name__)

MAX_BACKOFF_S = 15.0
# Consecutive unhealthy checks (10 s apart) after which watchdog pings stop
MAX_UNHEALTHY_CHECKS = 6


def sd_notify(state: str) -> bool:
    """Send a notification to systemd (READY=1, WATCHDOG=1, ...); no-op outside a notify service."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):  # abstract socket
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode())
        return True
    except OSError as e:
        logger.debug(f"sd_notify({state!r}) failed: {e}")
        return False


class Supervisor:
    """Tracks what is registered with BlueZ and schedules re-registration.

    Args:
        register: Registers whatever is missing (reads `needs_*`); returns False or
            raises if it could not even start. Registrations that finish later
            report back through `mark_*` / `registration_failed`.
        schedule: Calls a function once after the given number of seconds.
        notify: Sends a systemd notification (`sd_notify`).
    """

    def __init__(
        self,
        register: Callable[[], bool | None],
        schedule: Callable[[float, Callable[[], None]], None],
        notify: Callable[[str], object] = sd_notify,
        max_unhealthy: int = MAX_UNHEALTHY_CHECKS,
    ):
        self._register = register
        self._schedule = schedule
        self._notify = notify
        self._max_unhealthy = max_unhealthy
        self.bluez_present = True
        self.app_registered = False
        self.adv_registered = False
        self.agent_registered = False
        self.unhealthy_checks = 0
        self._pending = False
        self._backoff = 1.0

    @property
    def needs_registration(self) -> bool:
        return not (self.app_registered and self.adv_registered)

    # --- events -----------------------------------------------------------

    def bluez_changed(self, present: bool) -> None:
        """bluetoothd appeared on or vanished from the bus (it forgets all registrations)."""
        self.bluez_present = present
        self.app_registered = self.adv_registered = self.agent_registered = False
        if present:
            logger.info("bluetoothd is (back) on the bus, registering again")
            self.request(1.0)
        else:
            logger.warning("bluetoothd left the bus, waiting for it to return")

    def advertisement_released(self) -> None:
        """BlueZ dropped our advertisement."""
        logger.warning("Advertisement was released by BlueZ, registering it again")
        self.adv_registered = False
        self.request(0.5)

    def registration_ok(self) -> None:
        """BlueZ confirmed a registration."""
        self._backoff = 1.0

    def registration_failed(self, what: str, error: object) -> None:
        """BlueZ refused a registration: try again, a little later each time."""
        logger.error(f"Registering {what} failed: {error}")
        self._backoff = min(self._backoff * 2, MAX_BACKOFF_S)
        self.request(self._backoff)

    # --- scheduling -------------------------------------------------------

    def request(self, delay: float = 0.0) -> None:
        """Ask for a registration attempt (one at a time)."""
        if self._pending:
            return
        self._pending = True
        self._schedule(delay, self._run)

    def _run(self) -> None:
        self._pending = False
        if not self.bluez_present:
            return  # bluez_changed(True) will request again
        try:
            ok = self._register()
        except Exception as e:  # noqa: BLE001 - any failure means "try again later"
            logger.warning(f"Registering with BlueZ failed: {e}")
            ok = False
        if ok is False:
            self._backoff = min(self._backoff * 2, MAX_BACKOFF_S)
            self.request(self._backoff)

    # --- periodic health check ---------------------------------------------

    def check(self, active_instances: int | None) -> bool:
        """Periodic self-check; returns whether the gateway looks healthy.

        Args:
            active_instances: BlueZ's `ActiveInstances` (advertisements in use on the
                adapter) or None if unknown. Zero while we believe ours is registered
                means BlueZ lost it. (It cannot tell ours from someone else's.)
        """
        if not self.bluez_present:
            # Nothing a restart of the gateway could fix: stay up and wait for bluetoothd
            self._notify("WATCHDOG=1")
            return False
        healthy = not self.needs_registration
        if healthy and active_instances == 0:
            logger.warning("BlueZ has no active advertisement although one is registered")
            self.adv_registered = False
            healthy = False
        if healthy:
            self.unhealthy_checks = 0
        else:
            self.unhealthy_checks += 1
            self.request(0.0)
        if healthy or self.unhealthy_checks <= self._max_unhealthy:
            self._notify("WATCHDOG=1")
        elif self.unhealthy_checks == self._max_unhealthy + 1:
            logger.error("Not healthy for too long, no longer pinging the watchdog (systemd will restart us)")
        return healthy
