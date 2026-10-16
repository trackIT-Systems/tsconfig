"""Tests for app.privileged.ops - the operations that run as root via pkexec.

See app/privileged/__init__.py for the security model these enforce: every
op re-validates its own input from scratch, on the assumption that whatever
called it (normally the unprivileged tsconfig service, but potentially a
compromised or buggy version of it) cannot be trusted.
"""

import subprocess
from unittest.mock import patch

import pytest

from app.configs.wireguard import WireguardConfig
from app.privileged import ops


class TestWriteConfig:
    def test_unknown_config_type_rejected(self):
        with pytest.raises(ops.PrivilegedOpError, match="Unknown config type"):
            ops.write_config("not-a-real-type", {"content": "x"}, None)

    def test_non_dict_config_rejected(self):
        with pytest.raises(ops.PrivilegedOpError, match="must be a JSON object"):
            ops.write_config("wireguard", "not a dict", None)

    def test_wireguard_hook_injection_rejected(self, tmp_path):
        target = tmp_path / "wireguard.conf"
        with (
            patch.object(WireguardConfig, "config_file", property(lambda self: target)),
            pytest.raises(ops.PrivilegedOpError, match="PostUp"),
        ):
            ops.write_config(
                "wireguard",
                {"content": "[Interface]\nPostUp = evil\n[Peer]\nPublicKey=x\n"},
                None,
            )
        assert not target.exists(), "rejected config must not be written"

    def test_valid_write_succeeds_and_sets_mtime(self, tmp_path):
        target = tmp_path / "wireguard.conf"
        with patch.object(WireguardConfig, "config_file", property(lambda self: target)):
            result = ops.write_config(
                "wireguard",
                {
                    "content": (
                        "[Interface]\nPrivateKey=abc\n[Peer]\nPublicKey=x\nEndpoint=1.2.3.4:1\nAllowedIPs=0.0.0.0/0\n"
                    )
                },
                1_000_000_000.0,
            )
        assert result["config_type"] == "wireguard"
        assert result["mtime_set"] is True
        assert target.exists()
        assert int(target.stat().st_mtime) == 1_000_000_000

    def test_write_without_mtime_does_not_touch_mtime(self, tmp_path):
        target = tmp_path / "wireguard.conf"
        with patch.object(WireguardConfig, "config_file", property(lambda self: target)):
            result = ops.write_config(
                "wireguard",
                {"content": "[Interface]\nPrivateKey=abc\n[Peer]\nPublicKey=x\n"},
                None,
            )
        assert result["mtime_set"] is None


class TestDeleteConfig:
    def test_deletes_existing_file(self, tmp_path):
        target = tmp_path / "wireguard.conf"
        target.write_text("secret")
        with patch.object(WireguardConfig, "config_file", property(lambda self: target)):
            result = ops.delete_config("wireguard")
        assert result["deleted"] is True
        assert not target.exists()

    def test_missing_file_reports_not_deleted_without_error(self, tmp_path):
        target = tmp_path / "wireguard.conf"
        with patch.object(WireguardConfig, "config_file", property(lambda self: target)):
            result = ops.delete_config("wireguard")
        assert result["deleted"] is False

    def test_unknown_config_type_rejected(self):
        with pytest.raises(ops.PrivilegedOpError, match="Unknown config type"):
            ops.delete_config("not-a-real-type")


class TestWipeOverlay:
    def test_removes_files_and_directories(self, tmp_path):
        (tmp_path / "etc").mkdir()
        (tmp_path / "etc" / "x.conf").write_text("hi")
        (tmp_path / "plainfile").write_text("bye")
        with patch.object(ops, "OVERLAY_PATH", tmp_path):
            result = ops.wipe_overlay()
        assert result["wiped"] is True
        assert set(result["removed"]) == {"etc", "plainfile"}
        assert list(tmp_path.iterdir()) == []

    def test_missing_overlay_path_rejected(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        with patch.object(ops, "OVERLAY_PATH", missing), pytest.raises(ops.PrivilegedOpError, match="does not exist"):
            ops.wipe_overlay()

    def test_does_not_follow_directory_symlinks(self, tmp_path):
        real_target = tmp_path.parent / "sibling-marker-dir"
        real_target.mkdir(exist_ok=True)
        (real_target / "should-survive").write_text("x")
        overlay = tmp_path / "overlay"
        overlay.mkdir()
        (overlay / "link-to-outside").symlink_to(real_target, target_is_directory=True)
        try:
            with patch.object(ops, "OVERLAY_PATH", overlay):
                ops.wipe_overlay()
            # The symlink itself is gone, but shutil.rmtree refuses to
            # recurse into a path that turns out to be a symlink, so the
            # real target directory and its contents must be untouched.
            assert not (overlay / "link-to-outside").exists()
            assert (real_target / "should-survive").exists()
        finally:
            (real_target / "should-survive").unlink()
            real_target.rmdir()


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestUnsetGsmFields:
    """unset_gsm_fields is the one nmcli-adjacent op still root-mediated:
    nmcli itself cannot unset GSM password/pin fields, and /etc/netplan is
    root-owned. Every other NetworkManager interaction is now a direct,
    unprivileged nmcli call from app.routers.network - see
    docs/security.md's "Network management" section.
    """

    def test_disallowed_connection_rejected(self):
        with pytest.raises(ops.PrivilegedOpError, match="not in the allowed set"):
            ops.unset_gsm_fields("not-a-real-connection", ["password"])

    def test_disallowed_field_rejected(self):
        with pytest.raises(ops.PrivilegedOpError, match="field not allowed"):
            ops.unset_gsm_fields("cellular", ["apn"])

    def test_empty_fields_rejected(self):
        with pytest.raises(ops.PrivilegedOpError, match="non-empty JSON array"):
            ops.unset_gsm_fields("cellular", [])

    def test_connection_not_found_raises(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _completed(1)
            with pytest.raises(ops.PrivilegedOpError, match="connection not found"):
                ops.unset_gsm_fields("cellular", ["password"])

    def test_missing_netplan_file_reports_not_modified(self, tmp_path):
        with patch.object(ops, "NETPLAN_DIR", tmp_path), patch("subprocess.run") as mock_run:
            mock_run.return_value = _completed(0, stdout="connection.uuid:abc-123\n")
            result = ops.unset_gsm_fields("cellular", ["password"])
        assert result["modified"] is False
        assert result["removed"] == []

    def test_removes_fields_and_reloads(self, tmp_path):
        netplan_file = tmp_path / "90-NM-abc-123.yaml"
        netplan_file.write_text(
            "network:\n  modems:\n    cellular:\n      apn: internet\n      password: hunter2\n      pin: '1234'\n"
        )
        uuid_result = _completed(0, stdout="connection.uuid:abc-123\n")
        reload_result = _completed(0)
        with (
            patch.object(ops, "NETPLAN_DIR", tmp_path),
            patch("subprocess.run", side_effect=[uuid_result, reload_result]) as mock_run,
        ):
            result = ops.unset_gsm_fields("cellular", ["password", "pin"])

        assert result["modified"] is True
        assert set(result["removed"]) == {"password", "pin"}
        assert result["reload_returncode"] == 0

        content = netplan_file.read_text()
        assert "password" not in content
        assert "pin" not in content
        assert "apn: internet" in content

        # Second call is the reload triggered internally by unset_gsm_fields().
        assert mock_run.call_args_list[1].args[0] == ["nmcli", "connection", "reload"]

    def test_fields_not_present_reports_not_modified(self, tmp_path):
        netplan_file = tmp_path / "90-NM-abc-123.yaml"
        netplan_file.write_text("network:\n  modems:\n    cellular:\n      apn: internet\n")
        with patch.object(ops, "NETPLAN_DIR", tmp_path), patch("subprocess.run") as mock_run:
            mock_run.return_value = _completed(0, stdout="connection.uuid:abc-123\n")
            result = ops.unset_gsm_fields("cellular", ["password"])
        assert result["modified"] is False
        assert result["removed"] == []
