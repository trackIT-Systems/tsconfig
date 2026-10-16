"""Tests for app.config_loader's settings from tsconfig.yml."""

from pathlib import Path

import pytest

from app.config_loader import ConfigLoader


@pytest.mark.parametrize(
    ("content", "token"),
    [
        ('map:\n  mapbox_token: "pk.abc"\n', "pk.abc"),
        ('map:\n  mapbox_token: ""\n', None),
        ("map:\n", None),
    ],
)
def test_mapbox_token_comes_from_tsconfig_yml(tmp_path, content, token):
    config_file = tmp_path / "tsconfig.yml"
    config_file.write_text(content)
    assert ConfigLoader(config_file).get_mapbox_token() == token


def test_the_shipped_tsconfig_yml_has_a_public_mapbox_token():
    # Only public (pk.) tokens belong here: the page hands it to every browser
    assert ConfigLoader(Path("configs/tsconfig.yml")).get_mapbox_token().startswith("pk.")


def test_a_device_config_without_map_section_uses_the_shipped_token(tmp_path):
    # A device's tsconfig.yml is a copy from image build time; a code update
    # must not quietly turn the satellite map into a street map
    config_file = tmp_path / "tsconfig.yml"
    config_file.write_text("services: []\n")
    shipped = ConfigLoader(Path("configs/tsconfig.yml")).get_mapbox_token()
    assert ConfigLoader(config_file).get_mapbox_token() == shipped
