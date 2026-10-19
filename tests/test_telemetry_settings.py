"""/etc/tsconfig/tstelemetry.yml: data only, with per-collector defaults."""

from pathlib import Path

import yaml

from app.configs import registry
from app.telemetry.collectors import COLLECTORS
from app.telemetry.settings import CollectorsSettings, TelemetrySettings, load_settings


def test_every_collector_has_settings_and_every_setting_a_collector():
    assert [cls.name for cls in COLLECTORS] == list(CollectorsSettings.model_fields)


def test_missing_file_means_defaults(tmp_path):
    assert load_settings(tmp_path / "tstelemetry.yml") == TelemetrySettings()


def test_disabling_keeps_the_collectors_own_default_interval(tmp_path):
    path = tmp_path / "tstelemetry.yml"
    path.write_text("collectors:\n  modem:\n    enabled: false\n  network: {}\n")
    settings = load_settings(path)
    assert settings.collectors.modem.enabled is False
    assert settings.collectors.modem.interval == 3600
    assert settings.collectors.network.interval == 600
    assert settings.collectors.system.interval == 60


def test_shipped_default_file_equals_the_defaults():
    shipped = yaml.safe_load((Path(__file__).parent.parent / "configs" / "tstelemetry.yml").read_text())
    assert TelemetrySettings.model_validate(shipped) == TelemetrySettings()


def test_mqttutil_conf_is_retired_and_tstelemetry_is_no_bundle_member():
    assert "mqttutil.conf" in registry.RETIRED_CONFIG_FILES
    assert "mqttutil.conf" not in registry.RECOGNIZED_CONFIG_FILES
    assert "tstelemetry.yml" not in registry.RECOGNIZED_CONFIG_FILES
