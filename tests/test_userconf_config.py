"""Tests for app.configs.userconf.UserconfConfig and its participation in
server mode's versioned-config-group copy-forward (app.config_loader's
VERSIONED_CONFIG_FILES - see app/routers/base.py and
app/routers/authorized_keys.py, which both copy forward from this single,
shared list rather than their own separate hardcoded copies)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.config_loader import VERSIONED_CONFIG_FILES, config_loader
from app.configs.schedule import ScheduleConfig
from app.configs.userconf import UserconfConfig
from app.routers.base import BaseConfigRouter

VALID_HASH = "pi:$6$abcDEFghij0123$" + "x" * 86


class TestUserconfConfigPaths:
    def test_tracker_mode_targets_boot_partition(self):
        assert UserconfConfig().config_file == Path("/boot/firmware/userconf.txt")

    def test_server_mode_targets_config_group_dir(self, tmp_path):
        assert UserconfConfig(tmp_path).config_file == tmp_path / "userconf.txt"


class TestUserconfConfigValidate:
    def test_accepts_valid_hash_line(self):
        assert UserconfConfig().validate({"content": VALID_HASH}) == []

    def test_accepts_a_real_openssl_passwd_hash(self):
        # A real SHA-512 crypt hash, same shape/length as what
        # `openssl passwd -6` actually produces - not just the pattern.
        real_hash = "pi:$6$eC4AjSfg0Mf0s1Ud$Gs0NcBLlfTEx6si1pgd3v9/1kMNlPKutRcN9Q599QNm9lslIumvl0ZmDxWrQ9epmIlUmVMpm4iFeUL9aF.feK/"
        assert UserconfConfig().validate({"content": real_hash}) == []

    def test_rejects_missing_content_field(self):
        assert UserconfConfig().validate({}) != []

    def test_rejects_empty_content(self):
        assert UserconfConfig().validate({"content": ""}) != []

    def test_rejects_other_usernames(self):
        errors = UserconfConfig().validate({"content": "root:$6$abc$" + "x" * 86})
        assert errors and "pi:$6$" in errors[0]

    def test_rejects_multiline_content(self):
        errors = UserconfConfig().validate({"content": f"{VALID_HASH}\n{VALID_HASH}"})
        assert errors == ["userconf.txt must contain exactly one line"]

    def test_rejects_non_sha512_hash_types(self):
        # $1$ (md5), $5$ (sha256), etc. are not accepted - only $6$.
        errors = UserconfConfig().validate({"content": "pi:$1$abc$" + "x" * 20})
        assert errors != []

    def test_rejects_malformed_hash_shape(self):
        errors = UserconfConfig().validate({"content": "pi:not-a-hash"})
        assert errors != []


class TestUserconfConfigSaveLoad:
    def test_save_writes_content_verbatim(self, tmp_path):
        cfg = UserconfConfig(tmp_path)
        cfg.save({"content": VALID_HASH})
        assert (tmp_path / "userconf.txt").read_text() == VALID_HASH

    def test_load_returns_empty_content_when_file_absent(self, tmp_path):
        cfg = UserconfConfig(tmp_path)
        assert cfg.load() == {"content": ""}

    def test_load_round_trips_saved_content(self, tmp_path):
        cfg = UserconfConfig(tmp_path)
        cfg.save({"content": VALID_HASH})
        assert cfg.load() == {"content": VALID_HASH}


class TestVersionedConfigFilesIncludesUserconf:
    def test_userconf_txt_is_in_the_shared_copy_forward_list(self):
        assert "userconf.txt" in VERSIONED_CONFIG_FILES

    @pytest.mark.asyncio
    async def test_existing_userconf_survives_an_unrelated_server_mode_save(self, tmp_path):
        """Regression test for the bug this refactor exists to prevent: before
        VERSIONED_CONFIG_FILES was extracted to a single shared list, saving
        some OTHER config type (e.g. schedule.yml) in server mode would
        silently drop userconf.txt from the new versioned directory, since
        the copy-forward list lived separately (and had drifted) in
        app/routers/base.py and app/routers/authorized_keys.py.
        """
        group_root = tmp_path / "mygroup"
        old_versioned_dir = group_root / "20200101_000000"
        old_versioned_dir.mkdir(parents=True)
        (old_versioned_dir / "userconf.txt").write_text(VALID_HASH)
        old_latest = group_root / "latest"
        old_latest.symlink_to(old_versioned_dir.name, target_is_directory=True)

        with (
            patch.object(config_loader, "is_server_mode", return_value=True),
            patch.object(config_loader, "get_config_root", return_value=tmp_path),
        ):
            router = BaseConfigRouter(ScheduleConfig, "schedule", "schedule")
            result = await router.update_config_helper(
                {
                    "force_on": False,
                    "button_delay": "00:05",
                    "schedule": [],
                },
                config_group="mygroup",
            )

        new_latest = group_root / "latest"
        assert new_latest.resolve() != old_versioned_dir.resolve()
        assert (new_latest / "userconf.txt").read_text() == VALID_HASH
        assert result["message"].startswith("Schedule configuration updated")
