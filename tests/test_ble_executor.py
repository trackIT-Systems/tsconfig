"""Tests for the executor that keeps HTTP work off the GLib main loop."""

import asyncio
import queue
import threading

import pytest

from app.bluetooth.executor import SyncExecutor, ThreadedExecutor


class TestSyncExecutor:
    def test_returns_the_result_inline(self):
        out = []
        SyncExecutor().submit(asyncio.sleep(0, "x"), lambda r, e: out.append((r, e)))
        assert out == [("x", None)]

    def test_reports_an_exception_instead_of_raising(self):
        async def boom():
            raise ValueError("nope")

        out = []
        SyncExecutor().submit(boom(), lambda r, e: out.append((r, e)))
        assert out[0][0] is None and isinstance(out[0][1], ValueError)

    def test_timeout_is_reported_as_timeout_error(self):
        out = []
        SyncExecutor().submit(asyncio.sleep(1), lambda r, e: out.append(e), timeout=0.01)
        assert isinstance(out[0], asyncio.TimeoutError)


@pytest.fixture
def threaded():
    """A ThreadedExecutor whose main-loop callbacks are collected, to be run by the test."""
    main_loop_calls = queue.Queue()
    executor = ThreadedExecutor(dispatch=main_loop_calls.put)
    yield executor, main_loop_calls
    executor.shutdown()


def run_callbacks(main_loop_calls, count, timeout=5):
    """Run `count` callbacks the way the main loop would, on this (the calling) thread."""
    for _ in range(count):
        main_loop_calls.get(timeout=timeout)()


class TestThreadedExecutor:
    def test_work_runs_on_another_thread_and_the_callback_on_the_caller(self, threaded):
        executor, calls = threaded
        seen = {}

        async def work():
            seen["worker"] = threading.get_ident()
            return 42

        out = []
        executor.submit(work(), lambda r, e: out.append((r, e, threading.get_ident())))
        assert out == []  # not delivered until the "main loop" runs it
        run_callbacks(calls, 1)
        assert out == [(42, None, threading.get_ident())]
        assert seen["worker"] != threading.get_ident()

    def test_a_slow_request_does_not_block_the_caller_or_other_connections(self, threaded):
        executor, calls = threaded
        release = threading.Event()
        order = []

        async def slow():
            await asyncio.get_running_loop().run_in_executor(None, release.wait, 5)
            return "slow"

        async def fast():
            return "fast"

        executor.submit(slow(), lambda r, e: order.append(r), key="A")
        executor.submit(fast(), lambda r, e: order.append(r), key="B")
        run_callbacks(calls, 1)
        assert order == ["fast"]  # B was served while A is still waiting
        release.set()
        run_callbacks(calls, 1)
        assert order == ["fast", "slow"]

    def test_requests_of_one_connection_run_in_order(self, threaded):
        executor, calls = threaded
        started = []

        async def job(name, delay):
            started.append(name)
            await asyncio.sleep(delay)
            return name

        out = []
        executor.submit(job("first", 0.15), lambda r, e: out.append(r), key="A")
        executor.submit(job("second", 0), lambda r, e: out.append(r), key="A")
        run_callbacks(calls, 2)
        assert out == ["first", "second"]
        assert started == ["first", "second"]

    def test_a_failed_request_does_not_stop_the_next_one_of_the_connection(self, threaded):
        executor, calls = threaded

        async def boom():
            raise RuntimeError("x")

        out = []
        executor.submit(boom(), lambda r, e: out.append(type(e).__name__), key="A")
        executor.submit(asyncio.sleep(0, "ok"), lambda r, e: out.append(r), key="A")
        run_callbacks(calls, 2)
        assert out == ["RuntimeError", "ok"]

    def test_timeout_is_delivered_as_timeout_error_and_frees_the_connection(self, threaded):
        executor, calls = threaded
        out = []
        executor.submit(asyncio.sleep(30), lambda r, e: out.append(e), key="A", timeout=0.05)
        executor.submit(asyncio.sleep(0, "after"), lambda r, e: out.append(r), key="A")
        run_callbacks(calls, 2)
        assert isinstance(out[0], asyncio.TimeoutError)
        assert out[1] == "after"

    def test_defer_goes_through_the_main_loop_dispatch(self, threaded):
        executor, calls = threaded
        out = []
        executor.defer(lambda: out.append("ran"))
        assert out == []
        run_callbacks(calls, 1)
        assert out == ["ran"]

    def test_run_blocking_waits_for_the_result(self, threaded):
        executor, _ = threaded
        assert executor.run_blocking(asyncio.sleep(0, "done")) == "done"
