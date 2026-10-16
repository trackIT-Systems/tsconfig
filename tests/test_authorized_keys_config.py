"""Tests for app.configs.authorized_keys in tracker mode: the operator's own
~/.ssh/authorized_keys ("user" keys, edited on the SSH keys page) and the boot
partition's authorized_keys ("server" keys, from config bundles), which sshd
reads in place - so the page must show that very file."""

from pathlib import Path

import pytest

from app.configs import authorized_keys as module
from app.configs.authorized_keys import AuthorizedKeysConfig

USER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIUserKeyUserKeyUserKeyUserKeyUserKey operator@laptop"
SERVER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIServerKeyServerKeyServerKeyServerKey fleet@server"


@pytest.fixture
def files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    boot = tmp_path / "boot" / "authorized_keys"
    boot.parent.mkdir()
    monkeypatch.setattr(module, "OPERATOR_HOME", home)
    monkeypatch.setattr(module, "BOOT_AUTHORIZED_KEYS", boot)
    user = home / ".ssh" / "authorized_keys"
    user.write_text(f"# operator keys\n{USER_KEY}\n")
    boot.write_text(f"{SERVER_KEY}\n")
    return user, boot


def test_server_keys_are_the_boot_file_that_sshd_reads():
    assert AuthorizedKeysConfig().server_keys_file == Path("/boot/firmware/authorized_keys")
    assert AuthorizedKeysConfig(is_config_upload=True).config_file == Path("/boot/firmware/authorized_keys")


def test_load_tags_user_and_server_keys_by_file(files):
    keys = AuthorizedKeysConfig().load()["keys"]
    assert [(k["full_line"], k["source"]) for k in keys] == [(USER_KEY, "user"), (SERVER_KEY, "server")]


def test_a_changed_boot_file_shows_up_immediately(files):
    _user, boot = files
    boot.write_text("")
    assert [k["source"] for k in AuthorizedKeysConfig().load()["keys"]] == ["user"]


def test_saving_writes_only_user_keys_and_leaves_the_boot_file_alone(files):
    user, boot = files
    config = AuthorizedKeysConfig()
    keys = config.load()["keys"]
    keys.append({"full_line": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQNewKey new@host", "source": "user"})
    config.save({"keys": keys})
    assert user.read_text() == f"{USER_KEY}\nssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQNewKey new@host\n"
    assert boot.read_text() == f"{SERVER_KEY}\n"


def test_server_keys_cannot_be_removed_on_the_device(files):
    config = AuthorizedKeysConfig()
    server_index = next(i for i, k in enumerate(config.load()["keys"]) if k["source"] == "server")
    with pytest.raises(ValueError, match="server-managed"):
        config.remove_key(server_index)


def test_a_server_key_cannot_be_added_again_as_a_user_key(files):
    with pytest.raises(ValueError, match="already exists in server keys"):
        AuthorizedKeysConfig().add_key(SERVER_KEY)
