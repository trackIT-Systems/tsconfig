"""Tests for the characteristics running their API requests off the main loop:
writes return at once, the result follows as notifications; reads reply when the
API has answered; nothing of a closed connection reaches a later one; responses
are chunked to the MTU of the connection they are for."""

import asyncio
import json
import math
from unittest.mock import AsyncMock

import cbor2
import pytest

from app.bluetooth import executor as ble_executor
from app.bluetooth.executor import SyncExecutor
from tests.test_ble_auth import DEVICE, OTHER, FakeService, chars, make_write_char  # noqa: F401 (fixtures)


class QueueExecutor:
    """Holds submitted requests until the test runs them (the main loop stays free meanwhile)."""

    def __init__(self):
        self.jobs = []  # (coro, on_done, key, timeout)
        self.deferred = []

    def submit(self, coro, on_done, key=None, timeout=None):
        self.jobs.append((coro, on_done, key, timeout))

    def defer(self, fn):
        self.deferred.append(fn)

    def run_all(self):
        while self.jobs:
            coro, on_done, key, timeout = self.jobs.pop(0)
            SyncExecutor().submit(coro, on_done, key, timeout)

    def run_deferred(self):
        while self.deferred:
            self.deferred.pop(0)()


@pytest.fixture
def queued(monkeypatch):
    executor = QueueExecutor()
    monkeypatch.setattr(ble_executor, "_executor", executor)
    yield executor
    for coro, *_ in executor.jobs:  # requests a test chose not to run
        coro.close()


def write(char, data, device=DEVICE, **options):
    char.WriteValue(list(data), {"device": device, **options})


def read(char, device=DEVICE, **options):
    replies = []
    char.ReadValue({"device": device, **options}, replies.append, replies.append)
    return replies


def metadata(reply):
    return cbor2.loads(bytes(reply[0]))


class TestAsyncWrites:
    def test_write_returns_before_the_handler_runs_and_the_response_follows(self, chars, queued):
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"service": "x"}')
        assert sent == [] and handler.await_count == 0  # nothing happened on the main loop
        assert len(queued.jobs) == 1
        queued.run_all()
        handler.assert_awaited_once_with({"service": "x"})
        assert sent == [{"ok": True}]

    def test_requests_of_a_connection_share_a_key_so_they_stay_in_order(self, chars, queued):
        char, _, _ = make_write_char(chars, None)
        write(char, b'{"a": 1}', DEVICE)
        write(char, b'{"a": 2}', DEVICE)
        write(char, b'{"a": 3}', OTHER)
        assert [job[2] for job in queued.jobs] == [DEVICE, DEVICE, OTHER]

    def test_response_is_dropped_when_the_connection_closed_meanwhile(self, chars, queued):
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"service": "x"}')
        char.on_disconnect(DEVICE)
        queued.run_all()
        assert sent == []

    def test_a_response_of_an_old_connection_does_not_reach_the_next_one_on_the_same_path(self, chars, queued):
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"n": 1}')
        char.on_disconnect(DEVICE)
        write(char, b'{"n": 2}')  # the client reconnected
        queued.run_all()
        assert sent == [{"ok": True}]  # only the answer to the second request
        assert [c.args[0] for c in handler.await_args_list] == [{"n": 1}, {"n": 2}]

    def test_handler_error_is_notified_and_a_request_error_keeps_its_code(self, chars, queued):
        failing = AsyncMock(side_effect=ValueError("bad request"))
        char, _, sent = make_write_char(chars, None, failing)
        write(char, b"{}")
        queued.run_all()
        assert sent == [{"error": "bad request", "code": 500}]

    def test_handler_sees_the_connection_it_serves_even_when_run_later(self, chars, queued):
        seen = []

        async def handler(data):
            seen.append(char.current_device)
            return {}

        char, _, _ = make_write_char(chars, None, handler)
        write(char, b'{"a": 1}', DEVICE)
        write(char, b'{"a": 2}', OTHER)
        assert char.current_device is None
        queued.run_all()
        assert seen == [DEVICE, OTHER]
        assert char.current_device is None


class TestAsyncReads:
    def make(self, chars, handler):
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, handler)
        char.notifying = True
        notifications = []
        char.send_notification = notifications.append
        return char, notifications

    def test_reply_waits_for_the_api_and_data_follows_after_the_reply(self, chars, queued):
        char, notifications = self.make(chars, AsyncMock(return_value={"hello": "world"}))
        replies = read(char)
        assert replies == []  # the main loop is not held up
        queued.run_all()
        assert metadata(replies)[4] == 0  # STATUS_READY
        assert notifications == []  # the data is sent after the reply (deferred)
        queued.run_deferred()
        assert json.loads(b"".join(notifications)) == {"hello": "world"}

    def test_reads_use_the_short_timeout(self, chars, queued):
        char, _ = self.make(chars, AsyncMock(return_value={}))
        read(char)
        assert queued.jobs[0][3] == chars.READ_TIMEOUT_S

    def test_slow_api_is_answered_with_an_error_in_the_metadata(self, chars, monkeypatch):
        monkeypatch.setattr(chars, "READ_TIMEOUT_S", 0.05)

        async def slow():
            await asyncio.sleep(5)

        char, _ = self.make(chars, slow)
        replies = read(char)
        decoded = metadata(replies)
        assert decoded[4] == 1 and "timed out" in decoded[5]

    def test_api_error_is_answered_with_an_error_in_the_metadata(self, chars):
        char, _ = self.make(chars, AsyncMock(side_effect=RuntimeError("API down")))
        decoded = metadata(read(char))
        assert decoded[4] == 1 and decoded[5] == "API down"

    def test_read_of_a_closed_connection_is_not_delivered(self, chars, queued):
        char, notifications = self.make(chars, AsyncMock(return_value={"a": 1}))
        replies = read(char)
        char.on_disconnect(DEVICE)
        queued.run_all()
        assert metadata(replies)[5] == "Connection closed"
        queued.run_deferred()
        assert notifications == []


class TestChunkingUsesTheConnectionsMtu:
    PAYLOAD = {"data": "x" * 2600}  # about 2.6 kB of JSON

    def run(self, chars, **options):
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, AsyncMock(return_value=self.PAYLOAD))
        char.notifying = True
        notifications = []
        char.send_notification = notifications.append
        replies = read(char, **options)
        ble_executor.get_executor()  # (inline executor: reply and deferred send already happened)
        return metadata(replies), notifications

    def test_notifications_are_sized_for_the_negotiated_mtu(self, chars):
        meta, notifications = self.run(chars, mtu=515)
        size = len(json.dumps(self.PAYLOAD))
        assert meta[1] == size and meta[2] == math.ceil(size / 512) == len(notifications)
        assert max(len(n) for n in notifications) == 512
        assert json.loads(b"".join(notifications)) == self.PAYLOAD

    def test_chunks_never_exceed_the_512_byte_attribute_limit(self, chars):
        # BlueZ reports an ATT MTU of 517 and trims each notification to 512 bytes: chunks of
        # 514 bytes lost their last two bytes (seen on a Pi 5: 2636 of 2646 bytes arrived)
        meta, notifications = self.run(chars, mtu=517)
        size = len(json.dumps(self.PAYLOAD))
        assert max(len(n) for n in notifications) == 512
        assert meta[2] == len(notifications) == math.ceil(size / 512)
        assert json.loads(b"".join(notifications)) == self.PAYLOAD

    def test_without_an_mtu_the_ble_minimum_is_assumed(self, chars):
        meta, notifications = self.run(chars)
        assert meta[2] == len(notifications) and max(len(n) for n in notifications) == 20
        assert json.loads(b"".join(notifications)) == self.PAYLOAD

    def test_each_connection_gets_its_own_chunk_size(self, chars):
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, AsyncMock(return_value=self.PAYLOAD))
        char.notifying = True
        char.send_notification = lambda data: None
        read(char, DEVICE, mtu=185)
        read(char, OTHER, mtu=515)
        assert char.chunker_for(DEVICE).chunk_size == 182
        assert char.chunker_for(OTHER).chunk_size == 512
        assert char.chunker_for("/unknown").chunk_size == 20

    @pytest.mark.parametrize("bad", [None, "x", 0, 5])
    def test_nonsense_mtu_falls_back_to_the_minimum(self, chars, bad):
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, AsyncMock())
        assert char.get_mtu({"mtu": bad}) == 23

    def test_a_disconnected_connections_mtu_is_forgotten(self, chars):
        char = chars.ReadOnlyCharacteristic(None, 1, "u", FakeService(), None, AsyncMock(return_value={}))
        char.notifying = True
        char.send_notification = lambda data: None
        read(char, DEVICE, mtu=515)
        char.on_disconnect(DEVICE)
        assert char.chunker_for(DEVICE).chunk_size == 20

    def test_responses_to_writes_are_chunked_for_the_writing_connection(self, chars):
        big = {"logs": "y" * 1500}
        char, _, _ = make_write_char(chars, None, AsyncMock(return_value=big))
        notifications = []
        del char.send_chunked_response  # use the real method
        char.send_notification = notifications.append
        write(char, b'{"service": "x"}', DEVICE, mtu=185)
        assert max(len(n) for n in notifications) == 182
        assert json.loads(b"".join(notifications)) == big
