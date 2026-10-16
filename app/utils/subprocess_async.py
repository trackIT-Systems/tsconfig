"""Async subprocess utilities for non-blocking subprocess execution."""

import asyncio
import subprocess


async def run_subprocess_async(
    cmd: list[str],
    capture_output: bool = True,
    text: bool = True,
    timeout: float | None = None,
    check: bool = False,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess asynchronously without blocking the event loop.

    This function provides the same interface as subprocess.run() but executes
    asynchronously, allowing other coroutines to run concurrently.

    Args:
        cmd: Command to run as a list of strings
        capture_output: If True, capture stdout and stderr
        text: If True, decode stdout/stderr as text (default encoding)
        timeout: Maximum time in seconds to wait for the process
        check: If True, raise CalledProcessError on non-zero exit code
        input_data: Optional bytes written to the process's stdin - e.g. for
            piping a secret in rather than passing it as a command-line
            argument, where it would be visible via `ps`

    Returns:
        CompletedProcess instance with returncode, stdout, stderr attributes

    Raises:
        subprocess.CalledProcessError: If check=True and process returns non-zero
        asyncio.TimeoutError: If timeout is exceeded
        FileNotFoundError: If command executable is not found
    """
    # Determine stdout/stderr handling
    if capture_output:
        stdout = asyncio.subprocess.PIPE
        stderr = asyncio.subprocess.PIPE
    else:
        stdout = None
        stderr = None

    try:
        # Create subprocess asynchronously
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if input_data is not None else None,
            stdout=stdout,
            stderr=stderr,
        )

        # Wait for process to complete with optional timeout
        if timeout is not None:
            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(input=input_data), timeout=timeout
                )
            except TimeoutError:
                # Kill the process if timeout exceeded
                process.kill()
                await process.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
        else:
            stdout_data, stderr_data = await process.communicate(input=input_data)

        # Get return code
        returncode = await process.wait()

        # Decode output if text mode requested
        if text:
            stdout_str = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
            stderr_str = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        else:
            stdout_str = stdout_data
            stderr_str = stderr_data

        # Create CompletedProcess with same interface as subprocess.run()
        result = subprocess.CompletedProcess(
            args=cmd,
            returncode=returncode,
            stdout=stdout_str,
            stderr=stderr_str,
        )

        # Raise exception if check=True and returncode is non-zero
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                cmd,
                output=stdout_str,
                stderr=stderr_str,
            )

        return result

    except FileNotFoundError:
        # Re-raise FileNotFoundError as-is
        raise
    except subprocess.TimeoutExpired:
        # Re-raise TimeoutExpired as-is
        raise
    except subprocess.CalledProcessError:
        # Re-raise CalledProcessError as-is
        raise
