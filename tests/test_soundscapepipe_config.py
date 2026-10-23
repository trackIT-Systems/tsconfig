"""Tests for the koreabird detector in app.configs.soundscapepipe and the soundscapepipe species list."""

import pytest

from app.configs.soundscapepipe import SoundscapepipeConfig
from app.routers.soundscapepipe import _load_species_data


def errors_for(detector: dict) -> list[str]:
    return SoundscapepipeConfig(config_dir=None).validate({"detectors": {"koreabird": detector}})


@pytest.mark.parametrize("strategy", ["mix", "all", 1, "1"])
def test_koreabird_channel_strategies(strategy):
    assert errors_for({"class_threshold": 0.55, "channel_strategy": strategy}) == []


@pytest.mark.parametrize("strategy", ["or", "and", -1])
def test_koreabird_rejects_birdedge_only_strategies(strategy):
    assert any("koreabird" in e and "channel_strategy" in e for e in errors_for({"channel_strategy": strategy}))


def test_koreabird_threshold_range():
    assert errors_for({"class_threshold": 1.5}) == ["Detector 'koreabird' class_threshold must be between 0.0 and 1.0"]


def test_koreabird_species():
    species = _load_species_data()["koreabird"]
    assert len(species) == 387
    pitta = next(s for s in species if s["ebird"] == "faipit1")
    assert pitta["scientific"] == "Pitta nympha"
    assert pitta["english"] == "Fairy Pitta"
    assert pitta["korean"]
    assert "faipit1" in pitta["searchable"] and pitta["display"].endswith("[faipit1]")
