"""The stationary-mode rules of tslocationd: states, proposed reference, known cells, when a fine fix is due."""

import datetime

import pytest

from app.location.geo import Fix, Position, agree, average, distance, geoclue_text, read_geoclue
from app.location.modem import Cell
from app.location.settings import LocationSettings, load_settings
from app.location.tracker import CONFIRMED, DEVIATING, NO_FIX, PROPOSED, UNKNOWN, Tracker

REFERENCE = Position(50.81, 8.77, 210.0, 5.0)
# about 111 m north of the reference per 0.001 degrees of latitude
MOVED = 50.812
CELL_A = Cell("262", "01", "1A2B", "01234567")
CELL_B = Cell("262", "01", "1A2B", "01234568")
T0 = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.UTC)


def fix(lat=50.81, lon=8.77, accuracy=5.0, hours=0.0):
    return Fix(lat, lon, 210.0, accuracy, time=T0 + datetime.timedelta(hours=hours), sats=9, hdop=0.9)


def tracker(**settings):
    return Tracker(LocationSettings(**settings))


# --- geometry


def test_distance_of_a_thousandth_degree_latitude_is_about_111_m():
    assert distance(Position(50.81, 8.77), Position(50.811, 8.77)) == pytest.approx(111.2, abs=0.2)


def test_average_keeps_the_mean_accuracy_and_the_last_time():
    averaged = average([fix(50.81, accuracy=4.0), fix(50.8102, accuracy=6.0, hours=1)])
    assert averaged.lat == pytest.approx(50.8101)
    assert averaged.accuracy == 5.0
    assert averaged.time == T0 + datetime.timedelta(hours=1)


def test_fixes_agree_within_their_accuracies():
    assert agree([fix(50.81), fix(50.81005)])  # 5.6 m apart, 10 m allowed
    assert not agree([fix(50.81), fix(50.8101)])  # 11 m apart


def test_geoclue_round_trip(tmp_path):
    path = tmp_path / "geolocation"
    path.write_text(geoclue_text(REFERENCE))
    assert read_geoclue(path) == REFERENCE
    assert read_geoclue(tmp_path / "missing") is None
    path.write_text("50.81\n8.77\n")
    with pytest.raises(ValueError):
        read_geoclue(path)


# --- settings


def test_settings_take_durations_as_seconds_or_text(tmp_path):
    path = tmp_path / "tslocation.yml"
    path.write_text("mode: mobile\nfix_max_age: 2d\ncheck_interval: 1800\nrecheck_interval: '3 h'\n")
    settings = load_settings(path)
    assert settings.mode == "mobile"
    assert settings.fix_max_age == 2 * 86400
    assert settings.check_interval == 1800
    assert settings.recheck_interval == 3 * 3600
    assert settings.threshold == 50


def test_missing_settings_file_means_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.yml") == LocationSettings()


@pytest.mark.parametrize("text", ["mode: driving\n", "fix_max_age: soon\n", "unknown_key: 1\n", "[1, 2"])
def test_invalid_settings_are_rejected(tmp_path, text):
    path = tmp_path / "tslocation.yml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_settings(path)


# --- states


def test_first_fix_near_the_reference_confirms():
    t = tracker()
    assert t.state == UNKNOWN
    t.on_fix(fix(), REFERENCE, CELL_A)
    assert t.state == CONFIRMED
    assert t.deviation(REFERENCE) == pytest.approx(0, abs=0.1)
    assert t.cells == [CELL_A]


def test_fix_beyond_the_threshold_deviates_and_settles_into_a_proposal():
    t = tracker(settle_count=3)
    t.on_fix(fix(), REFERENCE, CELL_A)
    t.on_fix(fix(MOVED), REFERENCE, CELL_B)
    assert t.state == DEVIATING
    assert t.proposed is None
    t.on_fix(fix(MOVED + 0.00002, hours=6), REFERENCE, CELL_B)
    assert t.state == DEVIATING
    t.on_fix(fix(MOVED - 0.00002, hours=12), REFERENCE, CELL_B)
    assert t.state == PROPOSED
    assert t.proposed.lat == pytest.approx(MOVED)
    assert t.proposed.accuracy == 5.0


def test_a_fix_that_disagrees_restarts_the_run():
    t = tracker(settle_count=2)
    t.on_fix(fix(MOVED), REFERENCE, None)
    t.on_fix(fix(MOVED + 0.001), REFERENCE, None)  # moved again by 111 m
    assert t.state == DEVIATING
    assert len(t.candidates) == 1
    t.on_fix(fix(MOVED + 0.001), REFERENCE, None)
    assert t.state == PROPOSED


def test_back_within_the_threshold_confirms_again():
    t = tracker()
    t.on_fix(fix(MOVED), REFERENCE, None)
    t.on_fix(fix(), REFERENCE, None)
    assert t.state == CONFIRMED
    assert t.proposed is None


def test_without_reference_fixes_settle_into_a_proposal():
    t = tracker(settle_count=2)
    t.on_fix(fix(), None, None)
    assert t.state == DEVIATING
    t.on_fix(fix(), None, None)
    assert t.state == PROPOSED


def test_adopting_the_proposal_confirms():
    t = tracker(settle_count=1)
    t.on_fix(fix(MOVED), REFERENCE, CELL_B)
    assert t.state == PROPOSED
    t.on_reference(Position(t.proposed.lat, t.proposed.lon, t.proposed.alt, t.proposed.accuracy))
    assert t.state == CONFIRMED
    assert not t.deviated
    assert t.cells == [CELL_B]


def test_no_fix_keeps_what_was_known():
    t = tracker()
    t.on_fix(fix(MOVED), REFERENCE, None)
    t.on_no_fix()
    assert t.state == NO_FIX
    assert t.deviated
    assert t.fix is not None
    t.on_fix(fix(MOVED), REFERENCE, None)
    assert t.state == DEVIATING


# --- cells


def test_cells_are_collected_at_a_confirmed_position():
    t = tracker()
    t.on_fix(fix(), REFERENCE, CELL_A)
    assert t.known(CELL_A)
    assert not t.known(CELL_B)
    t.on_fix(fix(hours=1), REFERENCE, CELL_B)
    assert t.cells == [CELL_A, CELL_B]


def test_deviating_starts_a_new_cell_list():
    t = tracker()
    t.on_fix(fix(), REFERENCE, CELL_A)
    t.on_fix(fix(MOVED), REFERENCE, CELL_B)
    assert t.cells == [CELL_B]
    # still at the new position: the list grows
    t.on_fix(fix(MOVED), REFERENCE, CELL_A)
    assert t.cells == [CELL_B, CELL_A]


# --- when a fine fix is due


def test_due_without_a_fix():
    assert tracker().due(T0.timestamp()) is not None


def test_due_after_fix_max_age():
    t = tracker(fix_max_age="7d")
    t.on_fix(fix(), REFERENCE, None)
    assert t.due(T0.timestamp() + 6 * 86400) is None
    assert t.due(T0.timestamp() + 8 * 86400) is not None


def test_due_after_recheck_interval_while_deviating():
    t = tracker(recheck_interval="6h")
    t.on_fix(fix(MOVED), REFERENCE, None)
    assert t.due(T0.timestamp() + 5 * 3600) is None
    assert t.due(T0.timestamp() + 7 * 3600) is not None


# --- state.json


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "state.json"
    t = tracker(settle_count=3)
    t.on_fix(fix(), REFERENCE, CELL_A)
    t.on_fix(fix(MOVED), REFERENCE, CELL_B)
    t.save(path)
    loaded = Tracker.load(LocationSettings(settle_count=3), path)
    assert loaded == t


def test_broken_state_file_starts_from_nothing(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken")
    assert Tracker.load(LocationSettings(), path).state == UNKNOWN
