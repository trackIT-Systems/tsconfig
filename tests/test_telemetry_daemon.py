"""Scheduling, failure handling and MQTT publishing of the telemetry daemon."""

import json
import logging

import cbor2
import pytest

from app.telemetry import daemon
from app.telemetry.collectors import Collector, Unavailable
from app.telemetry.daemon import Daemon, available_collectors, enabled_collectors, next_tick
from app.telemetry.outputs import MqttOutput
from app.telemetry.settings import BrokerSettings, CollectorSettings, TelemetrySettings
from tests.telemetry_helpers import FakeClock, local, sample


class Recorder:
    def __init__(self):
        self.samples = []

    def write(self, sample):
        self.samples.append(sample)


class Broken:
    def write(self, sample):
        raise OSError("disk full")


class Fixed(Collector):
    name = "fixed"
    columns = ("a", "b")

    def __init__(self, readings=None, error=None):
        super().__init__(CollectorSettings())
        self.readings = readings if readings is not None else [{"a": 1, "b": 2}]
        self.error = error

    def read(self):
        if self.error:
            raise self.error
        return self.readings


def test_next_tick_is_the_next_full_interval():
    assert next_tick(120.0, 60) == 180
    assert next_tick(150.3, 60) == 180
    assert next_tick(3599.0, 3600) == 3600


def test_next_tick_does_not_repeat_after_an_early_wakeup():
    assert next_tick(179.999, 60) == 240


def test_only_enabled_collectors_run():
    settings = TelemetrySettings.model_validate({"collectors": {"modem": {"enabled": False}}})
    names = [collector.name for collector in enabled_collectors(settings)]
    assert "modem" not in names and "system" in names


@pytest.mark.asyncio
async def test_unavailable_and_broken_checks_are_left_out():
    class Missing(Fixed):
        def check(self):
            raise Unavailable("no board")

    class Crashing(Fixed):
        def check(self):
            raise ValueError("bug")

    ok = Fixed()
    assert await available_collectors([Missing(), Crashing(), ok]) == [ok]


@pytest.mark.asyncio
async def test_a_tick_stamps_and_writes_every_reading():
    clock = FakeClock(local(2026, 10, 19, 12, 0, 0).timestamp() + 0.4, synced=True)
    recorder = Recorder()
    await Daemon([], [recorder], clock).tick(Fixed([{"a": 1}, {"b": 2, "c": 3}]))

    first, second = recorder.samples
    assert first.time == local(2026, 10, 19, 12, 0, 0)
    assert first.synced is True and first.boottime == clock.boottime()
    assert first.values == (1, None)
    assert second.values == (None, 2)


@pytest.mark.asyncio
async def test_a_failing_output_does_not_stop_the_other():
    clock = FakeClock(0.0)
    recorder = Recorder()
    await Daemon([], [Broken(), recorder], clock).tick(Fixed())
    assert len(recorder.samples) == 1


@pytest.mark.asyncio
async def test_a_repeating_failure_is_warned_about_once(caplog):
    clock = FakeClock(0.0)
    collector = Fixed(error=OSError("I2C read failed"))
    runner = Daemon([collector], [Recorder()], clock)
    with caplog.at_level(logging.INFO, logger="tstelemetry"):
        for _ in range(3):
            await runner.tick(collector)
        collector.error = None
        await runner.tick(collector)
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "works again" in caplog.records[-1].getMessage()


def test_a_broken_settings_file_falls_back_to_the_defaults(tmp_path, caplog):
    path = tmp_path / "tstelemetry.yml"
    path.write_text("collectors:\n  system:\n    func: \"__import__('os')\"\n")
    with caplog.at_level(logging.ERROR, logger="tstelemetry"):
        assert daemon._settings(path) == TelemetrySettings()
    assert "using the defaults" in caplog.text


class FakeClient:
    def __init__(self):
        self.published = []
        self.will = None
        self.on_connect = None

    def will_set(self, topic, payload, qos, retain):
        self.will = (topic, json.loads(payload), retain)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def test_mqtt_publishes_each_format_on_its_own_topic():
    client = FakeClient()
    output = MqttOutput("station", BrokerSettings(), ["csv", "cbor"], client=client)
    output.write(sample(FakeClock(local(2026, 10, 19, 12).timestamp(), synced=True)))

    topics = [topic for topic, _payload, _retain in client.published]
    assert topics == ["station/tsconfig/system/csv", "station/tsconfig/system/cbor"]
    assert client.published[0][1].endswith(b";True;1.5;2")
    assert cbor2.loads(client.published[1][1])[1:] == [True, 1.5, 2]
    assert not any(retain for _topic, _payload, retain in client.published)


def test_mqtt_state_is_retained_with_offline_as_last_will():
    client = FakeClient()
    output = MqttOutput("station", BrokerSettings(), ["json"], client=client)
    assert client.will == ("station/tsconfig/state", {"state": "offline"}, True)

    class Success:
        is_failure = False

    output._on_connect(client, None, None, Success())
    assert client.published == [("station/tsconfig/state", json.dumps({"state": "online"}), True)]
