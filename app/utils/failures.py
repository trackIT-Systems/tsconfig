"""Log a failure that repeats (every poll, retry or loop) once per change of state.

docs/logging.md in tsOS-base, "Quiet in normal operation": a failure is logged
when it starts, again only when its error changes, and once when it recovers
("... works again"). The repeats go to DEBUG. This is the pattern of
`Daemon._failed()` / `Daemon._succeeded()` in app/telemetry/daemon.py, for the
places outside the telemetry daemon that need it.
"""

import logging


def describe(error: BaseException | str) -> str:
    """`TypeName: text` of an exception; a string is taken as it is."""
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {error}"
    return error


class FailureLog:
    """Remembers what last failed, by key, so a repeating failure is logged once.

    Args:
        logger: Where to log.
        level: Level of the first failure (and of a changed error); WARNING by default.
    """

    def __init__(self, logger: logging.Logger, level: int = logging.WARNING):
        self.logger = logger
        self.level = level
        self._failures: dict[str, str] = {}

    def failed(self, key: str, error: BaseException | str, level: int | None = None) -> None:
        """`key` failed with `error`: logged at `level` if new or changed, else at DEBUG.

        An exception's traceback is only logged at DEBUG.
        """
        message = describe(error)
        if self._failures.get(key) != message:
            self.logger.log(self.level if level is None else level, "%s failed: %s", key, message)
        else:
            self.logger.debug("%s failed again: %s", key, message)
        if isinstance(error, BaseException):
            self.logger.debug("%s traceback", key, exc_info=error)
        self._failures[key] = message

    def succeeded(self, key: str) -> None:
        """`key` worked: logged once at INFO if it had failed before."""
        if self._failures.pop(key, None) is not None:
            self.logger.info("%s works again", key)

    def failing(self, key: str) -> bool:
        """Whether `key` failed last time."""
        return key in self._failures
