"""Tests for reading password hashes from shadow (app.auth.shadow)."""

import pytest

from app.auth import shadow

PI_HASH = "$6$salt$Wdigest"
SHADOW = f"""root:!:20000:0:99999:7:::
daemon:*:20000:0:99999:7:::
pi:{PI_HASH}:20000:0:99999:7:::
locked:!$6$salt$digest:20000:0:99999:7:::
empty::20000:0:99999:7:::
"""


@pytest.fixture
def shadow_file(tmp_path, monkeypatch):
    path = tmp_path / "shadow"
    path.write_text(SHADOW)
    monkeypatch.setattr(shadow, "SHADOW_PATH", str(path))
    return path


class TestReadHash:
    def test_returns_the_users_hash(self, shadow_file):
        assert shadow.read_hash("pi") == PI_HASH

    @pytest.mark.parametrize("user", ["root", "daemon", "locked", "empty", "nobody"])
    def test_locked_empty_or_missing_accounts_have_none(self, shadow_file, user):
        assert shadow.read_hash(user) is None

    def test_missing_file_is_none_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shadow, "SHADOW_PATH", str(tmp_path / "missing"))
        assert shadow.read_hash("pi") is None

    def test_reads_the_file_again_on_every_call(self, shadow_file):
        assert shadow.read_hash("pi") == PI_HASH
        shadow_file.write_text(SHADOW.replace(PI_HASH, "$6$new$other"))
        assert shadow.read_hash("pi") == "$6$new$other"


class TestHashMatches:
    def test_exact_hash_matches(self, shadow_file):
        assert shadow.hash_matches("pi", PI_HASH)

    @pytest.mark.parametrize("token", ["", "$6$salt$wrong", PI_HASH + "x", PI_HASH[:-1], None, 5])
    def test_anything_else_does_not(self, shadow_file, token):
        assert not shadow.hash_matches("pi", token)

    def test_a_locked_account_never_matches_its_own_field(self, shadow_file):
        assert not shadow.hash_matches("locked", "!$6$salt$digest")
        assert not shadow.hash_matches("empty", "")
