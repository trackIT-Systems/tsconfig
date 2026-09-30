"""Tests for app.utils.privileged - the pi-side client that shells out to
`pkexec tsconfig-privileged <op>` (or directly to `systemctl`/`pkcheck`, with
no elevation prefix at all, for the operations authorized by their own
native polkit rules).

The actual subprocess is mocked throughout: these tests are about the
client's own contract (argv construction, stdin encoding, JSON parsing,
error mapping), not about pkexec/systemctl/the privileged CLI themselves,
which are covered elsewhere (app/privileged's own test suite, and manual
verification against a real polkit/pkexec setup - see docs/security.md).
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.privileged import (
    ALLOWED_SYSTEMCTL_VERBS,
    PrivilegedCallError,
    run_privileged_op,
    schedule_reboot,
    systemctl_action,
)


class FakeProcess:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input=None):
        self._sent_stdin = input
        return self._stdout, self._stderr

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
class TestRunPrivilegedOp:
    async def test_success_returns_result_without_success_key(self):
        payload = {"success": True, "config_type": "wireguard", "mtime_set": True}
        fake = FakeProcess(0, json.dumps(payload).encode())
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as mock_exec:
            result = await run_privileged_op("write-config", ["wireguard"], stdin_json={"config": {"content": "x"}})
        assert result == {"config_type": "wireguard", "mtime_set": True}
        argv = mock_exec.call_args.args
        assert argv == ("pkexec", "/usr/local/sbin/tsconfig-privileged", "write-config", "wireguard")

    async def test_stdin_is_json_encoded(self):
        fake = FakeProcess(0, json.dumps({"success": True}).encode())
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            await run_privileged_op("write-config", ["wireguard"], stdin_json={"config": {"content": "hello"}})
        sent = json.loads(fake._sent_stdin.decode())
        assert sent == {"config": {"content": "hello"}}

    async def test_op_level_failure_raises_with_its_message(self):
        payload = {"success": False, "error": "validation failed: bad content"}
        fake = FakeProcess(1, json.dumps(payload).encode())
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            with pytest.raises(PrivilegedCallError, match="validation failed: bad content"):
                await run_privileged_op("write-config", ["wireguard"], stdin_json={"config": {}})

    async def test_empty_stdout_uses_stderr_as_error(self):
        fake = FakeProcess(1, b"", b"Error executing command as another user: Not authorized")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            with pytest.raises(PrivilegedCallError, match="Not authorized"):
                await run_privileged_op("wipe-overlay")

    async def test_malformed_json_output_raises(self):
        fake = FakeProcess(0, b"not json at all")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            with pytest.raises(PrivilegedCallError, match="malformed"):
                await run_privileged_op("wipe-overlay")

    async def test_timeout_kills_process_and_raises(self):
        class HangingProcess(FakeProcess):
            async def communicate(self, input=None):
                import asyncio

                await asyncio.sleep(10)

        fake = HangingProcess(0, b"")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            with pytest.raises(PrivilegedCallError, match="timed out"):
                await run_privileged_op("wipe-overlay", timeout=0.01)


@pytest.mark.asyncio
class TestRunPrivilegedOpAlreadyRootBypass:
    """When the caller is already root (tsconfig's own CLI during image
    build, inside a chroot with no live polkit authority), run_privileged_op
    must skip pkexec entirely and call app.privileged.ops directly.
    """

    async def test_skips_pkexec_and_calls_ops_directly(self):
        from app.privileged import ops as privileged_ops

        with patch("os.geteuid", return_value=0), patch.object(
            privileged_ops, "wipe_overlay", return_value={"wiped": True, "removed": []}
        ) as mock_wipe, patch("asyncio.create_subprocess_exec") as mock_exec:
            result = await run_privileged_op("wipe-overlay")
        assert result == {"wiped": True, "removed": []}
        mock_wipe.assert_called_once()
        mock_exec.assert_not_called()

    async def test_passes_stdin_json_and_argv_through(self):
        from app.privileged import ops as privileged_ops

        with patch("os.geteuid", return_value=0), patch.object(
            privileged_ops, "write_config", return_value={"config_type": "wireguard"}
        ) as mock_write:
            result = await run_privileged_op(
                "write-config", ["wireguard", "--mtime", "1000000000"], stdin_json={"config": {"content": "x"}}
            )
        assert result == {"config_type": "wireguard"}
        mock_write.assert_called_once_with("wireguard", {"content": "x"}, 1000000000.0)

    async def test_op_error_translated_to_privileged_call_error(self):
        from app.privileged import ops as privileged_ops

        with patch("os.geteuid", return_value=0), patch.object(
            privileged_ops, "wipe_overlay", side_effect=privileged_ops.PrivilegedOpError("overlay missing")
        ):
            with pytest.raises(PrivilegedCallError, match="overlay missing"):
                await run_privileged_op("wipe-overlay")


@pytest.mark.asyncio
class TestSystemctlAction:
    @pytest.mark.parametrize("verb", sorted(ALLOWED_SYSTEMCTL_VERBS))
    async def test_success_execs_exact_argv_no_elevation(self, verb):
        fake = FakeProcess(0, b"", b"")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as mock_exec:
            ok, err = await systemctl_action("mqttutil.service", verb)
        assert ok is True
        assert err is None
        argv = mock_exec.call_args.args
        assert argv == ("/usr/bin/systemctl", verb, "mqttutil.service")

    async def test_denied_by_polkit_reports_failure_not_exception(self):
        fake = FakeProcess(1, b"", b"Failed to restart unit: Access denied")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            ok, err = await systemctl_action("ssh.service", "restart")
        assert ok is False
        assert "Access denied" in err

    async def test_unsupported_verb_rejected_before_any_subprocess_call(self):
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            ok, err = await systemctl_action("mqttutil.service", "enable")
        assert ok is False
        assert "unsupported systemctl action" in err
        mock_exec.assert_not_called()


@pytest.mark.asyncio
class TestScheduleReboot:
    async def test_denied_by_pkcheck_raises(self):
        fake = FakeProcess(1, b"", b"")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)):
            with pytest.raises(PrivilegedCallError, match="not authorized to reboot"):
                await schedule_reboot()

    async def test_authorized_schedules_a_task_and_returns_immediately(self):
        fake = FakeProcess(0, b"", b"")
        # Real create_task() so the scheduled coroutine is actually consumed
        # (a bare Mock would leave it dangling and warn "never awaited");
        # cancel it immediately after so it never actually sleeps/reboots.
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as mock_exec:
            result = await schedule_reboot(delay=5)
        assert result == {"scheduled": True, "delay_seconds": 5}
        for task in asyncio.all_tasks() - {asyncio.current_task()}:
            task.cancel()
        await asyncio.sleep(0)  # let the loop deliver the cancellation before teardown
        argv = mock_exec.call_args.args
        assert argv == ("pkcheck", "--action-id", "org.freedesktop.login1.reboot", "--process", str(os.getpid()))
