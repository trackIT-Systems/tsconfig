"""Tests for app.privileged.cli - argument parsing, stdin/stdout JSON
contract, and error handling (never leaking a traceback to the caller).
"""

import io
import json

import pytest

from app.configs.wireguard import WireguardConfig
from app.privileged import cli, ops


def _run(monkeypatch, capsys, argv, stdin_text=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    exit_code = cli.main(argv)
    captured = capsys.readouterr()
    out = captured.out.strip()
    return exit_code, (json.loads(out) if out else None), captured.err


class TestWriteConfigDispatch:
    def test_missing_stdin_reported_as_json_error(self, monkeypatch, capsys):
        exit_code, payload, _stderr = _run(monkeypatch, capsys, ["write-config", "wireguard"], stdin_text="")
        assert exit_code == 1
        assert payload["success"] is False
        assert "not valid JSON" in payload["error"]

    def test_success_roundtrip(self, monkeypatch, capsys, tmp_path):
        target = tmp_path / "wireguard.conf"
        monkeypatch.setattr(WireguardConfig, "config_file", property(lambda self: target))
        stdin = json.dumps({"config": {"content": "[Interface]\nPrivateKey=x\n[Peer]\nPublicKey=y\n"}})
        exit_code, payload, _stderr = _run(
            monkeypatch, capsys, ["write-config", "wireguard", "--mtime", "1000000000"], stdin_text=stdin
        )
        assert exit_code == 0
        assert payload["success"] is True
        assert payload["mtime_set"] is True
        assert target.exists()

    def test_validation_failure_reported_as_json_error_not_traceback(self, monkeypatch, capsys, tmp_path):
        target = tmp_path / "wireguard.conf"
        monkeypatch.setattr(WireguardConfig, "config_file", property(lambda self: target))
        stdin = json.dumps({"config": {"content": "[Interface]\nPostUp=evil\n[Peer]\n"}})
        exit_code, payload, _stderr = _run(monkeypatch, capsys, ["write-config", "wireguard"], stdin_text=stdin)
        assert exit_code == 1
        assert payload["success"] is False
        assert "PostUp" in payload["error"]
        assert not target.exists()


class TestUnexpectedExceptionHandling:
    def test_unexpected_exception_does_not_leak_details(self, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise RuntimeError("some internal detail: /etc/shadow contents or similar")

        monkeypatch.setattr(ops, "wipe_overlay", boom)
        exit_code, payload, stderr = _run(monkeypatch, capsys, ["wipe-overlay"])
        assert exit_code == 1
        assert payload == {"success": False, "error": "internal error"}
        # Make sure the real exception message went to stderr for
        # troubleshooting, just not to the JSON stdout contract.
        assert "some internal detail" in stderr


class TestDeleteConfigDispatch:
    def test_success(self, monkeypatch, capsys, tmp_path):
        target = tmp_path / "wireguard.conf"
        target.write_text("x")
        monkeypatch.setattr(WireguardConfig, "config_file", property(lambda self: target))
        exit_code, payload, _stderr = _run(monkeypatch, capsys, ["delete-config", "wireguard"])
        assert exit_code == 0
        assert payload["deleted"] is True


class TestUnsetGsmFieldsDispatch:
    def test_success_roundtrip_passes_parsed_stdin_through(self, monkeypatch, capsys):
        captured = {}

        def fake_unset(connection, fields):
            captured["args"] = (connection, fields)
            return {"modified": True, "removed": fields, "netplan_file": "/etc/netplan/x.yaml"}

        monkeypatch.setattr(ops, "unset_gsm_fields", fake_unset)
        stdin = json.dumps({"fields": ["password", "pin"]})
        exit_code, payload, _stderr = _run(monkeypatch, capsys, ["unset-gsm-fields", "cellular"], stdin_text=stdin)
        assert exit_code == 0
        assert payload["success"] is True
        assert payload["removed"] == ["password", "pin"]
        assert captured["args"] == ("cellular", ["password", "pin"])

    def test_missing_stdin_reported_as_json_error(self, monkeypatch, capsys):
        exit_code, payload, _stderr = _run(monkeypatch, capsys, ["unset-gsm-fields", "cellular"], stdin_text="")
        assert exit_code == 1
        assert payload["success"] is False
        assert "not valid JSON" in payload["error"]
