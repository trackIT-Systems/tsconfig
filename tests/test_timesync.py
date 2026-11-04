"""chrony's tracking state and the `timesync` collector."""

import pytest

from app.telemetry.collectors import timesync
from app.utils import chrony

# `chronyc -c tracking`, one line per reference
NTP = "51031B2E,81.3.27.46,3,1793715739.794282105,0.000159037,0.0,0.0,5.2,0.01,0.04,0.020000,0.003000,1031.5,Normal\n"
GPS = "47505300,GPS,1,1793715739.794282105,0.0012,0.0,0.0,5.2,0.01,0.04,0.200000,0.050000,16.0,Normal\n"
RTC = "52544300,RTC,11,1793715739.794282105,0.0040,0.0,0.0,5.2,0.01,0.04,2.000000,0.010000,64.0,Normal\n"
UNSYNCED = "00000000,,0,0.000000000,0,0,0,0,0,0,0,0,0,Not synchronised\n"


@pytest.mark.parametrize(("line", "source"), [(NTP, "ntp"), (GPS, "gnss"), (RTC, "rtc"), (UNSYNCED, "none")])
def test_source_follows_the_reference(line, source):
    assert chrony.parse_tracking(line).source == source


def test_reference_time_and_error():
    tracking = chrony.parse_tracking(NTP)
    assert tracking.ref_time == pytest.approx(1793715739.79)
    assert tracking.stratum == 3
    assert tracking.error == pytest.approx(0.013)  # root delay / 2 + root dispersion
    assert chrony.parse_tracking(UNSYNCED).ref_time is None


def test_unexpected_output_is_an_error():
    with pytest.raises(chrony.ChronyError):
        chrony.parse_tracking("garbage")


@pytest.mark.asyncio
async def test_collector_reports_source_error_and_station_time(monkeypatch):
    async def tracking():
        return chrony.parse_tracking(RTC)

    monkeypatch.setattr(chrony, "tracking", tracking)
    (reading,) = await timesync.TimesyncCollector(None).collect()
    assert reading["source"] == "rtc"
    assert reading["stratum"] == 11
    assert reading["error_s"] == pytest.approx(1.01)
    assert reading["station_time"][:2] == "20"


@pytest.mark.asyncio
async def test_collector_without_a_source_reports_no_error(monkeypatch):
    async def tracking():
        return chrony.parse_tracking(UNSYNCED)

    monkeypatch.setattr(chrony, "tracking", tracking)
    (reading,) = await timesync.TimesyncCollector(None).collect()
    assert reading["source"] == "none"
    assert reading["stratum"] is None and reading["error_s"] is None
