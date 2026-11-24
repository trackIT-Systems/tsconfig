"""Tests for app.config_loader's settings from tsconfig.yml."""

import ipaddress
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


@pytest.mark.parametrize(
    ("content", "seconds"),
    [
        ("system:\n  power_recent_s: 60\n", 60),
        ("system:\n  status_refresh_interval: 5\n", 300),
        ("services: []\n", 300),
    ],
)
def test_power_recent_s_comes_from_tsconfig_yml(tmp_path, content, seconds):
    config_file = tmp_path / "tsconfig.yml"
    config_file.write_text(content)
    assert ConfigLoader(config_file).get_power_recent_s() == seconds


@pytest.mark.parametrize(
    ("content", "hosts"),
    [
        ('deploy:\n  hosts: ["10.0.0.1", "fd00::1"]\n', {"10.0.0.1", "fd00::1"}),
        ("deploy:\n  hosts: []\n", set()),
        ("deploy:\n", set()),
        ('deploy:\n  hosts: ["10.0.0.1", "wg-server", 42]\n', {"10.0.0.1"}),
        ('deploy:\n  hosts: "10.0.0.1"\n', {"10.0.0.1"}),
    ],
)
def test_deploy_hosts_come_from_tsconfig_yml(tmp_path, content, hosts):
    config_file = tmp_path / "tsconfig.yml"
    config_file.write_text(content)
    assert ConfigLoader(config_file).get_deploy_hosts() == {ipaddress.ip_address(h) for h in hosts}


def test_the_shipped_tsconfig_yml_trusts_the_wireguard_server_for_deployment():
    assert ConfigLoader(Path("configs/tsconfig.yml")).get_deploy_hosts() == {ipaddress.ip_address("172.16.0.1")}


def test_a_device_config_without_deploy_section_uses_the_shipped_hosts(tmp_path):
    # ecohub keeps deploying to a device whose tsconfig.yml predates the setting
    config_file = tmp_path / "tsconfig.yml"
    config_file.write_text("services: []\n")
    shipped = ConfigLoader(Path("configs/tsconfig.yml")).get_deploy_hosts()
    assert ConfigLoader(config_file).get_deploy_hosts() == shipped
