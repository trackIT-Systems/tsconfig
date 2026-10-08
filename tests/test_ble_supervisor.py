"""Tests for the BlueZ supervision: what is registered again, when, and the watchdog policy."""

import os
import shutil
import socket
import tempfile

import pytest

from app.bluetooth.supervisor import Supervisor, sd_notify


class Harness:
    """A Supervisor with a manual scheduler and recorded registrations / notifications."""

    def __init__(self, register_result=None, max_unhealthy=3):
        self.scheduled = []  # (delay, fn)
        self.notified = []
        self.registered = 0
        self.register_result = register_result
        self.sup = Supervisor(
            register=self._register,
            schedule=lambda delay, fn: self.scheduled.append((delay, fn)),
            notify=self.notified.append,
            max_unhealthy=max_unhealthy,
        )

    def _register(self):
        self.registered += 1
        if isinstance(self.register_result, Exception):
            raise self.register_result
        return self.register_result

    def run_scheduled(self):
        delay, fn = self.scheduled.pop(0)
        fn()
        return delay

    def all_registered(self):
        self.sup.app_registered = self.sup.adv_registered = True


class TestRegistration:
    def test_requests_are_not_stacked(self):
        h = Harness()
        h.sup.request(0)
        h.sup.request(0)
        assert len(h.scheduled) == 1

    def test_a_scheduled_request_registers_once_and_allows_the_next_request(self):
        h = Harness()
        h.sup.request(0)
        h.run_scheduled()
        assert h.registered == 1
        h.sup.request(0)
        assert len(h.scheduled) == 1

    @pytest.mark.parametrize("failure", [False, RuntimeError("no adapter")])
    def test_failed_registration_is_retried_with_growing_delay(self, failure):
        h = Harness(register_result=failure)
        h.sup.request(0)
        delays = []
        for _ in range(6):
            delays.append(h.run_scheduled())
        assert delays[1:] == sorted(delays[1:]) and delays[-1] == 15.0  # grows, capped

    def test_refusals_by_bluez_back_off_and_start_over_after_a_success(self):
        h = Harness()
        h.sup.registration_failed("x", "boom")
        assert h.run_scheduled() == 2.0
        h.sup.registration_failed("x", "boom")
        assert h.run_scheduled() == 4.0
        h.sup.registration_ok()
        h.sup.registration_failed("x", "boom")
        assert h.run_scheduled() == 2.0

    def test_bluez_losing_everything_forces_registration_again(self):
        h = Harness()
        h.all_registered()
        h.sup.agent_registered = True
        h.sup.bluez_changed(False)
        h.sup.bluez_changed(True)
        assert not h.sup.app_registered and not h.sup.adv_registered and not h.sup.agent_registered
        assert h.scheduled and h.scheduled[0][0] == 1.0  # give bluetoothd a moment

    def test_nothing_is_registered_while_bluez_is_gone(self):
        h = Harness()
        h.sup.request(0)
        h.sup.bluez_changed(False)
        h.run_scheduled()
        assert h.registered == 0

    def test_released_advertisement_is_registered_again_quickly(self):
        h = Harness()
        h.all_registered()
        h.sup.advertisement_released()
        assert not h.sup.adv_registered and h.sup.app_registered
        assert h.scheduled[0][0] == 0.5


class TestHealthCheck:
    def test_healthy_gateway_pings_the_watchdog_and_schedules_nothing(self):
        h = Harness()
        h.all_registered()
        assert h.sup.check(1) is True
        assert h.notified == ["WATCHDOG=1"] and not h.scheduled

    def test_unknown_instance_count_is_not_a_problem(self):
        h = Harness()
        h.all_registered()
        assert h.sup.check(None) is True

    def test_zero_active_advertisements_means_bluez_lost_ours(self):
        h = Harness()
        h.all_registered()
        assert h.sup.check(0) is False
        assert not h.sup.adv_registered and h.scheduled

    def test_missing_registration_is_requested_and_still_pings_for_a_while(self):
        h = Harness(max_unhealthy=2)
        assert h.sup.check(None) is False
        assert h.scheduled and h.notified == ["WATCHDOG=1"]

    def test_pings_stop_when_recovery_keeps_failing(self):
        h = Harness(max_unhealthy=2)
        for _ in range(4):
            h.sup.check(None)
        assert h.notified == ["WATCHDOG=1", "WATCHDOG=1"]  # 3rd and 4th check: silence

    def test_recovery_resets_the_count(self):
        h = Harness(max_unhealthy=2)
        h.sup.check(None)
        h.sup.check(None)
        h.all_registered()
        assert h.sup.check(1) is True
        assert h.sup.unhealthy_checks == 0

    def test_absent_bluez_is_not_the_gateways_fault(self):
        h = Harness(max_unhealthy=1)
        h.sup.bluez_changed(False)
        for _ in range(5):
            assert h.sup.check(None) is False
        assert h.notified == ["WATCHDOG=1"] * 5  # keeps pinging, there is nothing a restart could fix


class TestSdNotify:
    def test_no_op_outside_a_notify_service(self, monkeypatch):
        monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
        assert sd_notify("READY=1") is False

    def test_sends_the_state_to_the_notify_socket(self, monkeypatch):
        directory = tempfile.mkdtemp(prefix="sd", dir="/tmp")  # unix socket paths are short
        path = os.path.join(directory, "n")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            server.bind(path)
            monkeypatch.setenv("NOTIFY_SOCKET", path)
            assert sd_notify("WATCHDOG=1") is True
            assert server.recv(100) == b"WATCHDOG=1"
        finally:
            server.close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_unreachable_socket_is_not_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "missing"))
        assert sd_notify("READY=1") is False
