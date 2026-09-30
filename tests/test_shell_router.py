"""Tests for app.routers.shell's PtyProcess: it must exec the caller's own
login shell directly, never `su` to a separately-configured user - once
tsconfig.service runs as pi, `su -l <anyone>` either needs a password (which
would just hang the pty waiting for one that's never supplied) or, if pi had
blanket sudo, would defeat the entire privilege-separation point of running
as pi in the first place.
"""

from unittest.mock import patch

from app.routers.shell import PtyProcess, get_user_shell


class TestPtyProcessCommand:
    def test_defaults_to_get_user_shell(self):
        with patch("app.routers.shell.get_user_shell", return_value="/bin/zsh"):
            process = PtyProcess()
        assert process.command == "/bin/zsh"

    def test_explicit_command_overrides_default(self):
        process = PtyProcess(command="/bin/bash")
        assert process.command == "/bin/bash"

    def test_never_constructs_an_su_command(self):
        # Regression guard: the old implementation built "su -l <user>" from
        # a configurable shell.user setting. Nothing in this class should
        # ever produce a command containing "su" again.
        with patch("app.routers.shell.get_user_shell", return_value="/bin/zsh"):
            process = PtyProcess()
        assert "su" not in process.command.split("/")


class TestPtyProcessStart:
    def test_child_execs_shell_as_login_shell(self):
        """In the forked child (pid == 0), start() must call os.execv with
        the shell's absolute path and argv[0] prefixed with "-" (the
        standard way to signal a login shell), and must never touch `su`.
        """
        process = PtyProcess(command="/bin/zsh")
        with patch("pty.fork", return_value=(0, 5)), patch("os.execv") as mock_execv:
            process.start()
        mock_execv.assert_called_once_with("/bin/zsh", ["-zsh"])

    def test_parent_process_marks_running(self):
        process = PtyProcess(command="/bin/zsh")
        with patch("pty.fork", return_value=(1234, 5)):
            result = process.start()
        assert result is True
        assert process.running is True
        assert process.pid == 1234


class TestGetUserShell:
    def test_prefers_shell_env_var_if_valid(self):
        with patch.dict("os.environ", {"SHELL": "/bin/zsh"}), patch("os.path.isfile", return_value=True):
            assert get_user_shell() == "/bin/zsh"

    def test_falls_back_to_passwd_entry(self):
        fake_pwent = type("P", (), {"pw_shell": "/bin/bash"})()
        with patch.dict("os.environ", {}, clear=True), patch("pwd.getpwuid", return_value=fake_pwent), patch(
            "os.path.isfile", return_value=True
        ):
            assert get_user_shell() == "/bin/bash"
