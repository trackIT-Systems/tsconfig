"""Tests for app.configs.radiotracking: radiotracking has no dashboard any more, but
radiotracking.ini files from before still have a [dashboard] section. They must keep
loading, validating and saving; the settings page used to refuse to save them."""

from app.configs.radiotracking import RadioTrackingConfig
from app.routers.radiotracking import RadioTrackingConfigUpdate

INI = """[optional arguments]
verbose = 0
calibrate = False
config = '/boot/firmware/radiotracking.ini'
schedule = []

[rtl-sdr]
device = ['0']
calibration = [0.0]
center_freq = 150155000
sample_rate = 300000
sdr_callback_length = None
gain = 49.6
lna_gain = 15
mixer_gain = 14
vga_gain = 7
sdr_max_restart = 3
sdr_timeout_s = 2

[analysis]
fft_nperseg = 256
fft_window = 'hamming'
signal_threshold_dbw = -95.0
snr_threshold_db = 3.0
signal_min_duration_ms = 8.0
signal_max_duration_ms = 50.0

[matching]
matching_timeout_s = 2.0
matching_time_diff_s = 0.1
matching_bandwidth_hz = 4000
matching_duration_diff_ms = 5.0

[publish]
sig_stdout = False
match_stdout = False
path = 'data'
csv = True
export_config = True
mqtt = True
mqtt_host = 'localhost'
mqtt_port = 1883
"""

LEGACY_DASHBOARD = """
[dashboard]
dashboard = True
dashboard_host = 'localhost'
dashboard_port = 8050
dashboard_signals = 100
"""


def _config(tmp_path, text):
    (tmp_path / "radiotracking.ini").write_text(text)
    return RadioTrackingConfig(tmp_path)


def test_a_config_without_dashboard_loads_and_validates(tmp_path):
    config = _config(tmp_path, INI)
    data = config.load()
    assert "dashboard" not in data
    assert config.validate(data) == []


def test_a_legacy_dashboard_section_is_not_shown_or_required(tmp_path):
    config = _config(tmp_path, INI + LEGACY_DASHBOARD)
    data = config.load()
    assert "dashboard" not in data
    assert config.validate(data) == []


def test_saving_leaves_a_legacy_dashboard_section_out(tmp_path):
    config = _config(tmp_path, INI + LEGACY_DASHBOARD)
    config.save(config.load())
    assert "dashboard" not in (tmp_path / "radiotracking.ini").read_text()
    assert config.validate(config.load()) == []


def test_the_update_model_does_not_need_a_dashboard(tmp_path):
    payload = _config(tmp_path, INI).load()
    update = RadioTrackingConfigUpdate(
        optional_arguments=payload["optional arguments"],
        rtl_sdr={**payload["rtl-sdr"], "device": ["0", "1", "2", "3", "4"], "calibration": [0.0] * 5},
        analysis=payload["analysis"],
        matching=payload["matching"],
        publish=payload["publish"],
    )
    assert update.rtl_sdr.device == ["0", "1", "2", "3", "4"]
    assert "dashboard" not in update.model_dump()
