"""Tests for logging to the journal (python3-systemd) and to stderr."""

import logging
import os

import pytest

from app import logging_config
from app.logging_config import setup_logging, stderr_is_journal


class FakeJournalHandler(logging.Handler):
    """Stands in for python3-systemd's JournalHandler, which does not install off the station."""

    def __init__(self, **fields):
        super().__init__()
        self.fields = fields


@pytest.fixture
def restore_logging(monkeypatch):
    """Set up logging for the terminal again after a test installed a journal handler."""
    yield
    monkeypatch.undo()
    setup_logging()


def test_stderr_is_journal(monkeypatch, tmp_path):
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    assert not stderr_is_journal()

    with open(tmp_path / "stderr", "w") as stderr:
        stat = os.fstat(stderr.fileno())
        monkeypatch.setattr(logging_config.sys, "stderr", stderr)
        monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino}")
        assert stderr_is_journal()
        monkeypatch.setenv("JOURNAL_STREAM", f"{stat.st_dev}:{stat.st_ino + 1}")
        assert not stderr_is_journal()
        monkeypatch.setenv("JOURNAL_STREAM", "garbage")
        assert not stderr_is_journal()


def test_setup_logging_uses_journal_under_systemd(monkeypatch, restore_logging):
    monkeypatch.setattr(logging_config, "stderr_is_journal", lambda: True)
    monkeypatch.setattr(logging_config, "JournalHandler", FakeJournalHandler)

    setup_logging(verbosity=1, identifier="tstest")
    (handler,) = logging.getLogger().handlers
    record = logging.makeLogRecord({"name": "uvicorn.error", "levelname": "INFO", "msg": "Started"})

    assert isinstance(handler, FakeJournalHandler)
    assert handler.fields == {"SYSLOG_IDENTIFIER": "tstest"}
    assert handler.format(record) == "uvicorn.error: Started"


def test_setup_logging_on_terminal(monkeypatch, capsys):
    monkeypatch.setattr(logging_config, "stderr_is_journal", lambda: False)
    monkeypatch.setattr(logging_config, "JournalHandler", FakeJournalHandler)

    setup_logging()
    logging.getLogger("app.x").warning("Careful")

    assert capsys.readouterr().err == "WARNING app.x: Careful\n"


def test_setup_logging_without_python_systemd(monkeypatch, capsys, restore_logging):
    monkeypatch.setattr(logging_config, "stderr_is_journal", lambda: True)
    monkeypatch.setattr(logging_config, "JournalHandler", None)

    setup_logging()
    logging.getLogger("app.x").warning("Careful")

    assert capsys.readouterr().err == "WARNING app.x: Careful\n"


def test_journal_fields(monkeypatch, restore_logging):
    """The fields python3-systemd sends; runs where it is installed (on a station)."""
    pytest.importorskip("systemd.journal")
    monkeypatch.setattr(logging_config, "stderr_is_journal", lambda: True)
    setup_logging(identifier="tstest")
    (handler,) = logging.getLogger().handlers
    sent = []
    handler.send = lambda MESSAGE, **fields: sent.append({"MESSAGE": MESSAGE, **fields})

    logging.getLogger("app.x").warning("Cannot read %s", "/dev/x")
    try:
        raise ValueError("broken")
    except ValueError:
        logging.getLogger("app.x").exception("Failed")
    warning, error = sent

    assert warning["MESSAGE"] == "app.x: Cannot read /dev/x"
    assert warning["PRIORITY"] == "4"
    assert warning["SYSLOG_IDENTIFIER"] == "tstest"
    assert warning["CODE_FILE"] == __file__
    assert warning["CODE_FUNC"] == "test_journal_fields"
    assert warning["CODE_LINE"] > 0
    # A traceback is part of MESSAGE, one entry
    assert error["PRIORITY"] == "3"
    assert error["MESSAGE"].startswith("app.x: Failed\nTraceback")
    assert error["MESSAGE"].endswith("ValueError: broken")


def test_setup_logging_overrides_uvicorns_own_levels(monkeypatch):
    monkeypatch.setattr(logging_config, "stderr_is_journal", lambda: False)
    # uvicorn's dictConfig sets these to INFO before our lifespan re-applies the setup
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    setup_logging()

    assert not logging.getLogger("uvicorn.error").isEnabledFor(logging.INFO)
    assert logging.getLogger("uvicorn.error").isEnabledFor(logging.WARNING)
