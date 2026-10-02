"""Tests for app.configs.hotspot: the bundle-supplied hotspot.nmconnection is
merged with the on-disk profile ([connection] and ssid always come from disk),
validated, applied through NetworkManager and rolled back if NM rejects it."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.configs import hotspot as hs
from app.configs import registry
from app.configs.hotspot import HotspotConfig, merge_with_existing, normalise
from app.privileged import ops

EXISTING = """[connection]
id=hotspot
type=wifi
uuid=c0ffee00-a000-4000-8000-000000000001
interface-name=wlan0
autoconnect=true
autoconnect-priority=0

[ipv4]
method=shared
address1=169.254.0.1/24

[ipv6]
method=ignore

[wifi]
ssid=tsos-default-name
mode=ap
band=bg
channel=1

[wifi-security]
key-mgmt=wpa-psk
proto=rsn;wpa;
psk=BirdsAndBats

[proxy]
"""

SUPPLIED = """[connection]
id=evil
type=ethernet
uuid=11111111-2222-3333-4444-555555555555
interface-name=eth0
permissions=user:root:;

[ipv4]
method=shared
address1=10.9.9.1/24

[wifi]
ssid=attacker-chosen
mode=ap
band=a
channel=36

[wifi-security]
key-mgmt=wpa-psk
psk=Sup3rSecretPSK
"""


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "hotspot.nmconnection").write_text(EXISTING)
    return HotspotConfig(config_dir=tmp_path)


def nmcli_ok(calls=None, active="hotspot\n"):
    def run(self_or_args, args=None):
        args = args if args is not None else self_or_args
        if calls is not None:
            calls.append(args)
        if args[:2] == ["connection", "load"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "--active" in args:
            return SimpleNamespace(returncode=0, stdout=active, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


class TestMerge:
    def test_connection_section_and_ssid_come_from_disk(self):
        merged = merge_with_existing(SUPPLIED, EXISTING)
        assert "id=hotspot" in merged and "id=evil" not in merged
        assert "uuid=c0ffee00-a000-4000-8000-000000000001" in merged
        assert "interface-name=wlan0" in merged and "eth0" not in merged
        assert "permissions=" not in merged
        assert "ssid=tsos-default-name" in merged and "attacker-chosen" not in merged

    def test_everything_else_comes_from_the_bundle(self):
        merged = merge_with_existing(SUPPLIED, EXISTING)
        assert "psk=Sup3rSecretPSK" in merged and "BirdsAndBats" not in merged
        assert "address1=10.9.9.1/24" in merged
        assert "band=a" in merged and "channel=36" in merged

    def test_connection_section_is_first_and_unique(self):
        merged = merge_with_existing(SUPPLIED, EXISTING)
        assert merged.startswith("[connection]")
        assert merged.count("[connection]") == 1

    def test_wifi_section_without_ssid_gets_the_disk_ssid(self):
        merged = merge_with_existing("[wifi]\nmode=ap\n", EXISTING)
        assert "ssid=tsos-default-name" in merged

    def test_merge_is_idempotent(self):
        once = merge_with_existing(SUPPLIED, EXISTING)
        assert merge_with_existing(once, once) == once

    def test_unparseable_existing_profile_is_an_error(self):
        with pytest.raises(ValueError):
            merge_with_existing(SUPPLIED, "[wifi]\nmode=ap\n")


class TestValidate:
    def test_accepts_a_typical_profile(self, cfg):
        assert cfg.validate({"content": SUPPLIED}) == []

    @pytest.mark.parametrize(
        "content,fragment",
        [
            (None, "content"),
            ("", "empty"),
            ("x" * 9000, "at most"),
            ("[wifi]\nmode=ap\n\x01", "control"),
            ("ssid=x\n[wifi]\nmode=ap\n", "header before any settings"),
            ("[wifi]\nmode=ap\nthis is not a setting\n", "key=value"),
            ("[ipv4]\nmethod=shared\n", "Missing [wifi]"),
            ("[wifi]\nmode=infrastructure\n", "mode must be 'ap'"),
            ("[wifi]\nmode=ap\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=short\n", "8-63"),
        ],
    )
    def test_rejects(self, cfg, content, fragment):
        errors = cfg.validate({"content": content})
        assert errors and any(fragment in e for e in errors)

    def test_open_hotspot_without_security_section_is_valid(self, cfg):
        assert cfg.validate({"content": "[wifi]\nmode=ap\n"}) == []


class TestSave:
    def test_writes_merged_profile_with_mode_0600_and_reloads(self, cfg, tmp_path):
        calls = []
        with patch.object(HotspotConfig, "_nmcli", staticmethod(nmcli_ok(calls))):
            result = cfg.save({"content": SUPPLIED})
        assert result == {"changed": True, "applied": True}
        written = (tmp_path / "hotspot.nmconnection").read_text()
        assert "psk=Sup3rSecretPSK" in written and "id=hotspot" in written
        assert (tmp_path / "hotspot.nmconnection").stat().st_mode & 0o777 == 0o600
        assert not list(tmp_path.glob(".*"))  # no temp file left behind
        assert ["connection", "load", str(tmp_path / "hotspot.nmconnection")] in calls
        assert ["--wait", "0", "connection", "up", "hotspot"] in calls

    def test_inactive_hotspot_is_not_reactivated(self, cfg):
        calls = []
        with patch.object(HotspotConfig, "_nmcli", staticmethod(nmcli_ok(calls, active="station\n"))):
            cfg.save({"content": SUPPLIED})
        assert not any(c[:3] == ["--wait", "0", "connection"] for c in calls)

    def test_unchanged_content_touches_nothing(self, cfg):
        calls = []
        with patch.object(HotspotConfig, "_nmcli", staticmethod(nmcli_ok(calls))):
            cfg.save({"content": SUPPLIED})
            calls.clear()
            result = cfg.save({"content": SUPPLIED})
        assert result == {"changed": False, "applied": False}
        assert calls == []  # no reload, no hotspot restart

    def test_equivalent_whitespace_is_not_a_change(self, cfg):
        with patch.object(HotspotConfig, "_nmcli", staticmethod(nmcli_ok())):
            assert cfg.save({"content": normalise(EXISTING)}) == {"changed": False, "applied": False}

    def test_networkmanager_rejection_restores_previous_profile(self, cfg, tmp_path):
        def reject(args):
            return SimpleNamespace(returncode=1, stdout="", stderr="Error: invalid profile")

        with patch.object(HotspotConfig, "_nmcli", staticmethod(reject)):
            with pytest.raises(RuntimeError, match="rejected"):
                cfg.save({"content": SUPPLIED})
        assert (tmp_path / "hotspot.nmconnection").read_text() == EXISTING

    def test_networkmanager_not_running_still_writes(self, cfg, tmp_path):
        def not_running(args):
            return SimpleNamespace(returncode=8, stdout="", stderr="NetworkManager is not running")

        with patch.object(HotspotConfig, "_nmcli", staticmethod(not_running)):
            result = cfg.save({"content": SUPPLIED})
        assert result == {"changed": True, "applied": False}
        assert "Sup3rSecretPSK" in (tmp_path / "hotspot.nmconnection").read_text()

    def test_missing_nmcli_still_writes(self, cfg):
        def missing(args):
            raise FileNotFoundError("nmcli")

        with patch.object(HotspotConfig, "_nmcli", staticmethod(missing)):
            assert cfg.save({"content": SUPPLIED})["applied"] is False

    def test_refuses_when_there_is_no_existing_profile(self, tmp_path):
        with pytest.raises(RuntimeError, match="cannot read"):
            HotspotConfig(config_dir=tmp_path).save({"content": SUPPLIED})


class TestLoad:
    def test_masks_the_password(self, cfg):
        loaded = cfg.load()["content"]
        assert "BirdsAndBats" not in loaded and "psk=<hidden>" in loaded

    def test_unreadable_file_gives_empty_content(self, tmp_path):
        assert HotspotConfig(config_dir=tmp_path).load() == {"content": ""}


class TestRegistryAndOps:
    def test_registered_with_matching_basename(self):
        info = registry.CONFIG_TYPES["hotspot"]
        assert info["basename"] == "hotspot.nmconnection"
        assert registry.RECOGNIZED_CONFIG_FILES["hotspot.nmconnection"] == "hotspot"
        instance = registry.get_privileged_config_instance("hotspot")
        assert instance.config_file.name == info["basename"]
        assert str(instance.config_file) == "/etc/NetworkManager/system-connections/hotspot.nmconnection"

    def test_privileged_delete_is_refused(self):
        with pytest.raises(ops.PrivilegedOpError, match="cannot be deleted"):
            ops.delete_config("hotspot")

    def test_privileged_write_validates_before_touching_disk(self):
        with pytest.raises(ops.PrivilegedOpError, match="validation failed"):
            ops.write_config("hotspot", {"content": "[wifi]\nmode=infrastructure\n"}, None)
