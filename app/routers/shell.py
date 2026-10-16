import asyncio
import codecs
import json
import os
import pty
import pwd
import signal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.session import is_authorized
from app.logging_config import get_logger

# Configure logging
logger = get_logger(__name__)

router = APIRouter(prefix="/api/shell", include_in_schema=False)


def get_user_shell() -> str:
    """Get the user's default shell."""
    try:
        # First, try to get shell from environment variable
        shell = os.environ.get("SHELL")
        if shell and os.path.isfile(shell):
            return shell

        # If SHELL env var is not set or invalid, get from passwd
        user_info = pwd.getpwuid(os.getuid())
        if user_info.pw_shell and os.path.isfile(user_info.pw_shell):
            return user_info.pw_shell

        # Fallback to common shells
        for fallback_shell in ["/bin/zsh", "/bin/bash", "/bin/sh"]:
            if os.path.isfile(fallback_shell):
                return fallback_shell

        # Last resort
        return "/bin/sh"

    except (KeyError, OSError) as e:  # KeyError: uid has no passwd entry
        logger.warning(f"Failed to detect user shell: {e}, falling back to /bin/bash")
        return "/bin/bash"


def get_user_home() -> str:
    """Get the home directory of the user the service runs as (the shell's
    starting directory). Falls back to "/" if it cannot be determined."""
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
        if home and os.path.isdir(home):
            return home
    except (KeyError, OSError) as e:  # KeyError: uid has no passwd entry
        logger.warning(f"Failed to detect the user's home directory: {e}")
    return "/"


class PtyProcess:
    """Manages a pseudo-terminal process for interactive shell sessions."""

    def __init__(self, command: str | None = None):
        # The web shell always runs as whichever user tsconfig.service runs
        # as (pi, in tracker mode) - there is no separate "shell user" to
        # pick, and there never should be: this used to exec `su -l
        # <configured user>`, which (a) only worked because the service ran
        # as root, since `su` to an arbitrary user needs root or the
        # target's password, and (b) meant the target user was a config
        # value instead of simply "whoever we already are". Now it execs
        # the caller's own login shell directly - no su, no privilege
        # change, no configurable target.
        self.command = command or get_user_shell()
        self.pid = None
        self.fd = None
        self.process = None
        self.running = False
        self.exit_code = None  # Store exit code when process exits
        self.cols = 80  # Default terminal width
        self.rows = 24  # Default terminal height

    def start(self):
        """Start the pty process."""
        try:
            # Resolved before forking so the child only has to chdir and exec.
            # The service's own working directory is the tsconfig source tree,
            # which is not where an operator expects a shell to start.
            home = get_user_home()

            # Create a pseudo-terminal pair
            self.pid, self.fd = pty.fork()

            if self.pid == 0:
                # Child process - start in the user's home directory, then exec
                # our own shell as a login shell (leading "-" in argv[0] is the
                # traditional way to signal that to the shell, same as what
                # `su -l`/`login` do).
                try:
                    os.chdir(home)
                except OSError:
                    pass  # keep the inherited directory rather than fail to start a shell
                shell_path = self.command
                argv0 = "-" + os.path.basename(shell_path)
                os.execv(shell_path, [argv0])
            else:
                # Parent process - we have the file descriptor
                self.running = True
                return True

        except OSError as e:
            logger.error(f"Failed to start pty process: {e}")
            return False

    def write(self, data: str):
        """Write data to the pty."""
        if self.fd is not None and self.running:
            try:
                os.write(self.fd, data.encode("utf-8"))
            except (OSError, BrokenPipeError):
                self.running = False

    async def output(self):
        """Yield the shell's output as text until the terminal closes.

        The pty is watched by the event loop (add_reader) instead of polled, so
        an idle shell costs nothing and never blocks other requests. Reading the
        master end fails with EIO once the shell and everything it started have
        closed the terminal, which ends the iteration."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        fd = self.fd

        def on_readable():
            try:
                data = os.read(fd, 4096)
            except OSError:
                data = b""
            if not data:
                loop.remove_reader(fd)
            queue.put_nowait(data)

        # Multi-byte characters can be split across reads
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        loop.add_reader(fd, on_readable)
        try:
            while data := await queue.get():
                text = decoder.decode(data)
                if text:
                    yield text
        finally:
            loop.remove_reader(fd)

    def is_alive(self) -> bool:
        """Check if the process is still running."""
        if not self.running or self.pid is None:
            return False

        try:
            # Check if the process is still alive using waitpid with WNOHANG
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid == 0:
                # Process is still running
                return True
            else:
                # Process has exited - extract exit code
                if os.WIFEXITED(status):
                    self.exit_code = os.WEXITSTATUS(status)
                else:
                    # Process was killed by signal
                    self.exit_code = -1
                logger.info(f"Shell process {self.pid} exited with status {status}, exit code: {self.exit_code}")
                self.running = False
                return False
        except (OSError, ChildProcessError) as e:
            # Process doesn't exist or other error
            logger.info(f"Shell process {self.pid} is no longer alive: {e}")
            self.running = False
            self.exit_code = -1  # Unknown exit code
            return False

    def resize(self, cols: int, rows: int):
        """Resize the terminal."""
        if self.fd is not None and self.running:
            try:
                import fcntl
                import struct
                import termios

                # Update stored dimensions
                self.cols = cols
                self.rows = rows

                # Send TIOCSWINSZ ioctl to resize the terminal
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)

                # Send SIGWINCH to notify the child process
                if self.pid:
                    os.kill(self.pid, signal.SIGWINCH)

            except (OSError, ImportError) as e:
                logger.warning(f"Failed to resize terminal: {e}")

    async def wait(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the shell to exit (and reap it)."""
        for _ in range(max(1, int(timeout / 0.05))):
            if not self.is_alive():
                return True
            await asyncio.sleep(0.05)
        return not self.is_alive()

    async def terminate(self):
        """Hang up the terminal like a closed ssh session and reap the shell.

        SIGHUP lets the shell and its jobs exit cleanly; SIGKILL follows only
        if it doesn't. Either way the child is waited for, so it does not stay
        behind as a zombie."""
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

        if self.pid is not None and self.running:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            if not await self.wait(timeout=1.0):
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.wait(timeout=1.0)

        self.running = False


# Global dictionary to store active shell sessions
active_sessions: dict[str, PtyProcess] = {}


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for interactive shell sessions."""
    # AuthenticationMiddleware refuses unauthenticated handshakes as well;
    # this check stays so the shell does not depend on the middleware being
    # installed. Closing before accept() rejects the handshake.
    if not is_authorized(websocket):
        logger.warning("Rejected unauthenticated shell websocket connection")
        await websocket.close(code=1008)
        return

    await websocket.accept()

    # The web UI picks a new session id for every connection. A second
    # connection to a running session would compete for its output, so it is
    # refused instead of attached.
    if session_id in active_sessions:
        await websocket.send_text(json.dumps({"type": "error", "data": "Shell session is already in use"}))
        await websocket.close(code=1008)
        return

    pty_process = PtyProcess()
    if not pty_process.start():
        await websocket.send_text(json.dumps({"type": "error", "data": "Failed to start shell session"}))
        await websocket.close()
        return

    active_sessions[session_id] = pty_process
    logger.info(f"Shell session {session_id} started (shell: {pty_process.command})")

    # Send initial prompt
    await websocket.send_text(
        json.dumps(
            {
                "type": "output",
                "data": f"Shell session {session_id} started. Type 'exit' to close.\r\n",
            }
        )
    )

    async def read_from_pty():
        async for text in pty_process.output():
            await websocket.send_text(json.dumps({"type": "output", "data": text}))

        # The terminal closed: the shell exited (or is about to)
        await pty_process.wait(timeout=1.0)
        exit_code = pty_process.exit_code
        exit_message = "Shell process has exited"
        if exit_code == 0:
            exit_message = "Shell process exited normally"
        elif exit_code is not None:
            exit_message = f"Shell process exited with code {exit_code}"
        try:
            await websocket.send_text(json.dumps({"type": "exit", "data": exit_message, "exit_code": exit_code}))
            await websocket.close(code=1000, reason="Shell process exited")
        except Exception as e:
            # Usually the browser is already gone
            logger.debug(f"Error sending exit notification: {e}", exc_info=True)

    read_task = asyncio.create_task(read_from_pty())
    try:
        # Handle incoming messages from websocket
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
            except json.JSONDecodeError:
                # Invalid JSON, ignore
                continue

            if data.get("type") == "input":
                pty_process.write(data.get("data", ""))
            elif data.get("type") == "resize":
                cols = data.get("cols", 80)
                rows = data.get("rows", 24)
                # Ensure cols and rows are valid integers
                if isinstance(cols, int) and isinstance(rows, int) and cols > 0 and rows > 0:
                    pty_process.resize(cols, rows)

    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError: receive after the reader closed the websocket
        logger.info(f"Shell session {session_id} disconnected")
    except Exception:
        logger.exception(f"Error in shell session {session_id}")
    finally:
        # Stop reading before the pty is closed, then hang up and reap the shell
        read_task.cancel()
        await asyncio.gather(read_task, return_exceptions=True)
        await pty_process.terminate()
        active_sessions.pop(session_id, None)
        logger.info(f"Shell session {session_id} cleaned up")
