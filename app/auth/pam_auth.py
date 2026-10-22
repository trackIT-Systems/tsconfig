"""Password verification for the operator (`pi`) and staff (`root`) logins.

tsconfig runs as `pi`, and `pam_unix` verifies a caller's *own* password through
its setuid `unix_chkpwd` helper without needing root. That is how `pi` is
checked, in-process through libpam.

`root` cannot be checked that way: for a non-root caller `unix_chkpwd` refuses
every account but the caller's own, and the alternative - letting `pi` read
/etc/shadow - would expose every password hash. Instead the staff login asks
the setuid `su` binary, which authenticates its *target* account as root:
`su -c true root` under a pty. That adds no root-side code of ours and no
capability `pi` did not already have (it could always try `su`). A root with no
password (`passwd -l`, the default when the image was built without
TSOS_ROOT_PASSWORD) simply fails the check, which is what makes the staff
login optional.

This is a small ctypes wrapper around libpam rather than a third-party package,
because the image installs Python dependencies via apt with `pip --no-deps`
(see tsOS-base.Pifile) and libpam is already part of the base OS.

The PAM service name `tsconfig` is defined by /etc/pam.d/tsconfig (shipped in
the tsOS-base image); it includes the system's common-auth/common-account stacks.
"""

import asyncio
import ctypes
import ctypes.util
import os
import pty
import select
import subprocess
import threading
import time
from ctypes import CFUNCTYPE, POINTER, Structure, c_char_p, c_int, c_void_p, cast, pointer, sizeof

from app.identity import OPERATOR_USER, STAFF_USER
from app.logging_config import get_logger

logger = get_logger(__name__)

# OPERATOR_USER is checked directly through PAM. STAFF_USER is checked through
# `su` (see the module docstring); logging in as it grants no more than logging
# in as the operator - the session, the API and the web shell stay `pi`-level.
LOGIN_USERS = (OPERATOR_USER, STAFF_USER)
PAM_SERVICE = "tsconfig"


def loggable_username(username: object) -> str:
    """The user name for a failed-login log line: only the accounts that can log in are named.

    Anything else may be a password typed into the user name field, which must
    not reach the journal (or the journal topics over MQTT).
    """
    return repr(username) if username in LOGIN_USERS else "an unknown user"


# The setuid su binary, a constant so tests can point it at a fake.
SU_PATH = "/usr/bin/su"
# Whole `su` attempt, including pam_unix's ~2 s failure delay.
SU_TIMEOUT_SECONDS = 15.0
# At most this many `su` checks at once; further attempts are refused rather
# than queued, so a burst of parallel guesses cannot fork unbounded processes.
SU_MAX_CONCURRENT = 2

# Upper bound for password length (bytes). PAM itself has no hard limit, but an
# unbounded value is pointless and cheap to reject up front.
MAX_PASSWORD_LENGTH = 512

PAM_SUCCESS = 0
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4
PAM_CONV_ERR = 19


class _PamMessage(Structure):
    _fields_ = [("msg_style", c_int), ("msg", c_char_p)]


class _PamResponse(Structure):
    _fields_ = [("resp", c_void_p), ("resp_retcode", c_int)]


_CONV_FUNC = CFUNCTYPE(c_int, c_int, POINTER(POINTER(_PamMessage)), POINTER(POINTER(_PamResponse)), c_void_p)


class _PamConv(Structure):
    _fields_ = [("conv", _CONV_FUNC), ("appdata_ptr", c_void_p)]


_libpam = None
_libc = None
_load_lock = threading.Lock()


def _load_libs():
    """Load libpam/libc lazily, so importing this module never fails on hosts
    without PAM (e.g. a developer laptop running the unit tests)."""
    global _libpam, _libc
    with _load_lock:
        if _libpam is not None:
            return _libpam, _libc

        pam_name = ctypes.util.find_library("pam")
        c_name = ctypes.util.find_library("c")
        if not pam_name or not c_name:
            raise OSError("libpam/libc not found")
        libpam = ctypes.CDLL(pam_name)
        libc = ctypes.CDLL(c_name)

        libc.calloc.restype = c_void_p
        libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        libc.strdup.restype = c_void_p
        libc.strdup.argtypes = [c_char_p]

        libpam.pam_start.restype = c_int
        libpam.pam_start.argtypes = [c_char_p, c_char_p, POINTER(_PamConv), POINTER(c_void_p)]
        libpam.pam_authenticate.restype = c_int
        libpam.pam_authenticate.argtypes = [c_void_p, c_int]
        libpam.pam_acct_mgmt.restype = c_int
        libpam.pam_acct_mgmt.argtypes = [c_void_p, c_int]
        libpam.pam_end.restype = c_int
        libpam.pam_end.argtypes = [c_void_p, c_int]

        _libpam, _libc = libpam, libc
        return _libpam, _libc


def _authenticate_blocking(username: str, password: str) -> bool:
    """Run the PAM auth + account stacks synchronously. May block for a couple
    of seconds on failure (pam_unix's failure delay), so callers on the event
    loop must use `authenticate_operator()` instead."""
    libpam, libc = _load_libs()
    password_bytes = password.encode("utf-8")

    @_CONV_FUNC
    def conv(num_msg, msg, resp, _appdata):
        # PAM owns the response array and frees it with free(), so it has to
        # be allocated with the C allocator, not ctypes' own.
        array = libc.calloc(num_msg, sizeof(_PamResponse))
        if not array:
            return PAM_CONV_ERR
        responses = cast(array, POINTER(_PamResponse))
        for i in range(num_msg):
            style = msg[i].contents.msg_style
            if style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                responses[i].resp = libc.strdup(password_bytes)
                responses[i].resp_retcode = 0
        resp[0] = cast(array, POINTER(_PamResponse))
        return PAM_SUCCESS

    conversation = _PamConv(conv, None)
    handle = c_void_p()
    rc = libpam.pam_start(PAM_SERVICE.encode(), username.encode(), pointer(conversation), pointer(handle))
    if rc != PAM_SUCCESS:
        logger.error("pam_start failed with code %s", rc)
        return False
    try:
        rc = libpam.pam_authenticate(handle, 0)
        if rc != PAM_SUCCESS:
            return False
        return libpam.pam_acct_mgmt(handle, 0) == PAM_SUCCESS
    finally:
        libpam.pam_end(handle, rc)


def _has_control_characters(password: str) -> bool:
    """The staff check types the password into a tty line, where the line
    discipline would interpret ^C, ^D, ^U, backspace etc. instead of passing them."""
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in password)


def _check_staff_via_su_blocking(password: str) -> bool:
    """Verify the staff (root) password with `su -c true root` under a pty.

    Blocking (up to SU_TIMEOUT_SECONDS); callers on the event loop must use
    `authenticate_operator()`. Returns True only if `su` exits 0. Everything
    `su` prints is discarded - it is never logged.

    `su` is started with subprocess (not pty.fork): this process is
    multi-threaded, and forking one without exec-ing immediately can deadlock
    the child.
    """
    deadline = time.monotonic() + SU_TIMEOUT_SECONDS
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [
                SU_PATH,
                "-s",
                "/bin/sh",  # do not run root's own shell (and its startup files) for a no-op
                "-c",
                "true",
                STAFF_USER,
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"},  # nothing from the service
            start_new_session=True,
            close_fds=True,
        )
    except OSError as e:
        logger.error("Cannot start su for the staff login: %s", e)
        os.close(master)
        return False
    finally:
        os.close(slave)

    try:
        seen = b""
        prompted = False
        while proc.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("su timed out during the staff login")
                return False
            ready, _, _ = select.select([master], [], [], min(0.2, remaining))
            if not ready:
                continue
            try:
                chunk = os.read(master, 1024)
            except OSError:  # EIO: su closed its end
                continue
            if chunk and not prompted:
                seen = (seen + chunk)[-512:]
                # su discards pending input when it turns echo off, so the password
                # must only be written once the prompt ("Password:") has appeared.
                if b"assword" in seen:
                    os.write(master, password.encode("utf-8") + b"\n")
                    prompted = True
        return proc.returncode == 0
    finally:
        try:
            os.close(master)
        except OSError:
            pass
        if proc.poll() is None:  # timed out: do not leave su behind
            proc.kill()
            proc.wait()


_su_slots = asyncio.Semaphore(SU_MAX_CONCURRENT)


async def authenticate_operator(username: str, password: str) -> bool:
    """Check `username`/`password` for the operator (`pi`) or staff (`root`) login.

    Only `LOGIN_USERS` are ever accepted, whatever the system would say about
    other accounts. `pi` goes straight through PAM; `root` through `su` (see the
    module docstring). Both run in a worker thread because they block.
    """
    if username not in LOGIN_USERS:
        return False
    if not password or "\x00" in password or len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        return False

    try:
        if username == STAFF_USER:
            if _has_control_characters(password):
                logger.debug("Staff password with control characters refused (cannot be typed into su)")
                return False
            if _su_slots.locked():
                logger.warning("Too many concurrent staff logins, refusing this attempt")
                return False
            async with _su_slots:
                return await asyncio.to_thread(_check_staff_via_su_blocking, password)
        return await asyncio.to_thread(_authenticate_blocking, username, password)
    except OSError as e:
        logger.error("Password check unavailable: %s", e)
        return False
    except Exception:  # never let a PAM/su problem become a 500 with details
        logger.exception("Password check failed")
        return False
