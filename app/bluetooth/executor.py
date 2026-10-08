"""Run the gateway's HTTP work off the GLib main loop.

BlueZ calls into the gateway on the GLib main loop, which also serves every other
D-Bus call and every other connection. A handler that waits for the tsconfig API
(up to its 30 s timeout) must therefore not run there. The `ThreadedExecutor`
runs coroutines on one persistent asyncio loop in a background thread and hands
the outcome back to the main loop; requests of the same connection (`key`) run
one after the other, in the order they were submitted.

`SyncExecutor` runs everything inline; it is the default and what the tests use.
"""

import asyncio
import threading
from typing import Any, Awaitable, Callable, Dict, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

# on_done(result, error): exactly one of them is meaningful (error is None on success)
DoneCallback = Callable[[Any, Optional[BaseException]], None]


class SyncExecutor:
    """Runs a coroutine to completion inside `submit` and calls `on_done` inline."""

    def submit(
        self,
        coro: Awaitable[Any],
        on_done: DoneCallback,
        key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        loop = asyncio.new_event_loop()
        try:
            result, error = loop.run_until_complete(asyncio.wait_for(coro, timeout) if timeout else coro), None
        except Exception as e:  # noqa: BLE001 - reported to the caller
            result, error = None, e
        finally:
            loop.close()
        on_done(result, error)

    def defer(self, fn: Callable[[], None]) -> None:
        fn()

    def run_blocking(self, coro: Awaitable[Any], timeout: float = 2.0) -> Any:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(coro, timeout))
        finally:
            loop.close()

    def shutdown(self) -> None:
        pass


class ThreadedExecutor:
    """Runs coroutines on a background asyncio loop; callbacks run on the main loop.

    Args:
        dispatch: Schedules a function on the main loop (e.g. `GLib.idle_add`);
            `on_done` and `defer` callbacks are delivered through it.
    """

    def __init__(self, dispatch: Callable[[Callable[[], None]], None]):
        self._dispatch = dispatch
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._tails: Dict[str, "asyncio.Future[None]"] = {}  # key -> completion of its newest request

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            ready = threading.Event()

            def run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()

            self._thread = threading.Thread(target=run, name="ble-executor", daemon=True)
            self._thread.start()
            ready.wait()
        return self._loop

    async def _run(self, coro: Awaitable[Any], key: Optional[str], timeout: Optional[float]) -> Any:
        previous = self._tails.get(key) if key is not None else None
        finished: "asyncio.Future[None]" = asyncio.get_running_loop().create_future()
        if key is not None:
            self._tails[key] = finished
        try:
            if previous is not None:
                await asyncio.wait([previous])  # never raises: the outcome of the previous request is not ours
            return await (asyncio.wait_for(coro, timeout) if timeout else coro)
        finally:
            finished.set_result(None)
            if key is not None and self._tails.get(key) is finished:
                del self._tails[key]

    def submit(
        self,
        coro: Awaitable[Any],
        on_done: DoneCallback,
        key: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        future = asyncio.run_coroutine_threadsafe(self._run(coro, key, timeout), self._ensure_loop())

        def deliver(done):
            error = done.exception() if not done.cancelled() else asyncio.CancelledError()
            result = None if error else done.result()
            self._dispatch(lambda: on_done(result, error))

        future.add_done_callback(deliver)

    def defer(self, fn: Callable[[], None]) -> None:
        self._dispatch(fn)

    def run_blocking(self, coro: Awaitable[Any], timeout: float = 2.0) -> Any:
        """Run a coroutine on the background loop and wait for it (shutdown paths)."""
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop()).result(timeout)

    def shutdown(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)


_executor: Any = SyncExecutor()


def get_executor():
    """The executor characteristics use (inline unless the server installed a threaded one)."""
    return _executor


def set_executor(executor) -> None:
    global _executor
    _executor = executor
