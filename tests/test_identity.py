import pwd
from pathlib import Path

import app.identity as identity


def test_home_comes_from_the_passwd_entry(monkeypatch):
    entry = pwd.struct_passwd(("pi", "x", 1000, 1000, "", "/srv/operator", "/bin/sh"))
    monkeypatch.setattr(pwd, "getpwnam", lambda name: entry)
    assert identity._operator_home() == Path("/srv/operator")


def test_home_falls_back_without_the_account(monkeypatch):
    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(pwd, "getpwnam", missing)
    assert identity._operator_home() == Path("/home") / identity.OPERATOR_USER


def test_names():
    assert (identity.OPERATOR_USER, identity.STAFF_USER) == ("pi", "root")
