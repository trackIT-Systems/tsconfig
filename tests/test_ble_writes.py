"""Tests for multi-write BLE requests: a request that arrives in several GATT writes
is collected per connection, and an aborted or malformed one must never block the
characteristic for later requests (it used to, until the service was restarted)."""

import json
import types

import pytest

from app.bluetooth.protocol import RequestParser
from tests.test_ble_auth import DEVICE, OTHER, FakeService, chars, make_write_char  # noqa: F401 (fixtures)

classify = RequestParser.classify_json


class TestClassifyJson:
    def test_complete_request(self):
        assert classify(b'{"a": 1}') == ("complete", {"a": 1})

    @pytest.mark.parametrize(
        "body",
        [b"", b'{"a"', b'{"a": ', b'{"a": "unterminated', b'{"a": [1, 2', b'{"a": tr', b'{"a": -', b'{"a": 1.', b'{"a": 1e+']
        + [b'{"a": nul', b'{"a": fals', b'{"a": -Infin'],
    )
    def test_truncated_request_is_incomplete(self, body):
        assert classify(body)[0] == "incomplete"

    @pytest.mark.parametrize("body", [b"nonsense", b"}", b'{"a": 1}{', b'{"a" 1}', b'{"a": 1} trailing', b"\xff\xfe"])
    def test_garbage_is_invalid(self, body):
        assert classify(body)[0] == "invalid"

    def test_leftovers_of_an_aborted_request_followed_by_a_new_one_are_invalid(self):
        assert classify(b'{"password": "nat{"password": "natur"}')[0] == "invalid"

    @pytest.mark.parametrize("ensure_ascii", [True, False])
    def test_every_prefix_of_a_valid_request_is_incomplete_never_invalid(self, ensure_ascii):
        # Writes are cut at arbitrary byte positions: inside strings, inside \uXXXX
        # escapes (ensure_ascii) and inside multi-byte UTF-8 characters (not ensure_ascii)
        payload = json.dumps(
            {"filename": "bundle é.zip", "content": "äሴ😀 line\nbreak \\ \" end", "n": [1, 2.5, None, True]},
            ensure_ascii=ensure_ascii,
        ).encode()
        for cut in range(len(payload)):
            assert classify(payload[:cut])[0] == "incomplete", payload[:cut]
        assert classify(payload)[0] == "complete"


def write(char, data, device=DEVICE):
    char.WriteValue(list(data), {"device": device})


class TestMultiWriteRequests:
    def test_request_split_over_several_writes_reaches_the_handler_once(self, chars):
        char, handler, sent = make_write_char(chars, None)
        body = b'{"service": "chrony", "action": "restart"}'
        for i in range(0, len(body), 7):
            write(char, body[i : i + 7])
        handler.assert_awaited_once_with({"service": "chrony", "action": "restart"})

    def test_aborted_request_does_not_block_later_requests_after_a_disconnect(self, chars):
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"password": "nat')
        char.on_disconnect(DEVICE)
        write(char, b'{"service": "x"}')
        handler.assert_awaited_once_with({"service": "x"})

    def test_aborted_request_of_one_connection_does_not_affect_another(self, chars):
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"password": "nat', OTHER)
        write(char, b'{"service": "x"}', DEVICE)
        handler.assert_awaited_once_with({"service": "x"})

    def test_leftovers_are_rejected_loudly_and_the_next_request_works(self, chars):
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"password": "nat')
        write(char, b'{"password": "natur"}')  # retry on the same connection
        assert sent == [{"error": "Invalid JSON request", "code": 400}]
        handler.assert_not_called()
        write(char, b'{"service": "x"}')
        handler.assert_awaited_once_with({"service": "x"})

    def test_garbage_is_rejected_and_does_not_poison_the_buffer(self, chars):
        char, handler, sent = make_write_char(chars, None)
        write(char, b"hello")
        assert sent == [{"error": "Invalid JSON request", "code": 400}]
        write(char, b'{"service": "x"}')
        handler.assert_awaited_once_with({"service": "x"})

    def test_incomplete_request_is_dropped_after_the_idle_timeout(self, chars, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(chars, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"password": "nat')
        now[0] += chars.WRITE_IDLE_TIMEOUT + 1
        write(char, b'{"service": "x"}')
        handler.assert_awaited_once_with({"service": "x"})

    def test_continuation_within_the_idle_timeout_is_kept(self, chars, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(chars, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"service": ')
        now[0] += chars.WRITE_IDLE_TIMEOUT - 1
        write(char, b'"x"}')
        handler.assert_awaited_once_with({"service": "x"})

    def test_oversized_request_is_refused_with_413_and_dropped(self, chars, monkeypatch):
        monkeypatch.setattr(chars, "MAX_WRITE_BUFFER", 32)
        char, handler, sent = make_write_char(chars, None)
        write(char, b'{"content": "' + b"a" * 10)
        write(char, b"a" * 30)
        assert sent == [{"error": "Request too large", "code": 413}]
        assert not char.write_buffers[DEVICE]
        handler.assert_not_called()
        write(char, b'{"service": "x"}')
        handler.assert_awaited_once_with({"service": "x"})

    def test_disconnect_forgets_the_connections_buffer(self, chars):
        char, _, _ = make_write_char(chars, None)
        write(char, b'{"a": ')
        assert char.write_buffers[DEVICE]
        char.on_disconnect(DEVICE)
        assert DEVICE not in char.write_buffers

    def test_disconnect_of_an_unknown_device_is_harmless(self, chars):
        char, _, _ = make_write_char(chars, None)
        char.on_disconnect(DEVICE)
