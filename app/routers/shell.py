import os
import pty
import select
import subprocess
import threading
import time
import json
import asyncio
import pwd
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import logging

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/shell", tags=["shell"])

class ShellCommand(BaseModel):
    command: str

class ShellResponse(BaseModel):
    stdout: str
    stderr: str
    returncode: int

def get_user_shell() -> str:
    """Get the user's default shell."""
    try:
        # First, try to get shell from environment variable
        shell = os.environ.get('SHELL')
        if shell and os.path.isfile(shell):
            return shell
        
        # If SHELL env var is not set or invalid, get from passwd
        user_info = pwd.getpwuid(os.getuid())
        if user_info.pw_shell and os.path.isfile(user_info.pw_shell):
            return user_info.pw_shell
        
        # Fallback to common shells
        for fallback_shell in ['/bin/zsh', '/bin/bash', '/bin/sh']:
            if os.path.isfile(fallback_shell):
                return fallback_shell
        
        # Last resort
        return '/bin/sh'
        
    except Exception as e:
        logger.warning(f"Failed to detect user shell: {e}, falling back to /bin/bash")
        return '/bin/bash'

class PtyProcess:
    """Manages a pseudo-terminal process for interactive shell sessions."""
    
    def __init__(self, command: str = None):
        self.command = command or get_user_shell()
        self.pid = None
        self.fd = None
        self.process = None
        self.running = False
        self.cols = 80  # Default terminal width
        self.rows = 24  # Default terminal height
        
    def start(self):
        """Start the pty process."""
        try:
            # Create a pseudo-terminal pair
            self.pid, self.fd = pty.fork()
            
            if self.pid == 0:
                # Child process - execute the shell
                os.execvp(self.command, [self.command])
            else:
                # Parent process - we have the file descriptor
                self.running = True
                return True
                
        except Exception as e:
            logger.error(f"Failed to start pty process: {e}")
            return False
    
    def write(self, data: str):
        """Write data to the pty."""
        if self.fd is not None and self.running:
            try:
                os.write(self.fd, data.encode('utf-8'))
            except (OSError, BrokenPipeError):
                self.running = False
    
    def read(self, timeout: float = 0.1) -> str:
        """Read data from the pty with timeout."""
        if self.fd is None or not self.running:
            return ""
        
        try:
            # Use select to check if data is available
            ready, _, _ = select.select([self.fd], [], [], timeout)
            if ready:
                data = os.read(self.fd, 1024)
                return data.decode('utf-8', errors='replace')
        except (OSError, BrokenPipeError):
            self.running = False
        
        return ""
    
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
                # Process has exited
                logger.info(f"Shell process {self.pid} exited with status {status}")
                self.running = False
                return False
        except (OSError, ChildProcessError) as e:
            # Process doesn't exist or other error
            logger.info(f"Shell process {self.pid} is no longer alive: {e}")
            self.running = False
            return False
    
    def resize(self, cols: int, rows: int):
        """Resize the terminal."""
        if self.fd is not None and self.running:
            try:
                import fcntl
                import termios
                import struct
                
                # Update stored dimensions
                self.cols = cols
                self.rows = rows
                
                # Send TIOCSWINSZ ioctl to resize the terminal
                winsize = struct.pack('HHHH', rows, cols, 0, 0)
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
                
                # Send SIGWINCH to notify the child process
                if self.pid:
                    os.kill(self.pid, 28)  # SIGWINCH
                    
            except (OSError, ImportError) as e:
                logger.warning(f"Failed to resize terminal: {e}")

    def terminate(self):
        """Terminate the pty process."""
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        
        if self.pid is not None:
            try:
                os.kill(self.pid, 9)  # SIGKILL
            except (OSError, ProcessLookupError):
                pass
        
        self.running = False
        self.pid = None
        self.fd = None

# Global dictionary to store active shell sessions
active_sessions: Dict[str, PtyProcess] = {}

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for interactive shell sessions."""
    await websocket.accept()
    
    # Create or get existing session
    if session_id not in active_sessions:
        pty_process = PtyProcess()
        if not pty_process.start():
            await websocket.send_text(json.dumps({
                "type": "error",
                "data": "Failed to start shell session"
            }))
            await websocket.close()
            return
        
        active_sessions[session_id] = pty_process
        logger.info(f"Shell session {session_id} started with shell: {pty_process.command}")
    else:
        pty_process = active_sessions[session_id]
    
    logger.info(f"Shell session {session_id} connected")
    
    # Send initial prompt
    await websocket.send_text(json.dumps({
        "type": "output",
        "data": f"Shell session {session_id} started. Type 'exit' to close.\r\n"
    }))
    
    try:
        # Start a background task to read from pty and send to websocket
        async def read_from_pty():
            consecutive_empty_reads = 0
            max_empty_reads = 100  # If we get 100 consecutive empty reads, check if process died
            
            while pty_process.is_alive():
                data = pty_process.read(timeout=0.1)
                if data:
                    consecutive_empty_reads = 0
                    await websocket.send_text(json.dumps({
                        "type": "output",
                        "data": data
                    }))
                else:
                    consecutive_empty_reads += 1
                    # If we haven't received data for a while, check if process is still alive
                    if consecutive_empty_reads >= max_empty_reads:
                        if not pty_process.is_alive():
                            break
                        consecutive_empty_reads = 0
                
                await asyncio.sleep(0.01)  # Small delay to prevent busy waiting
            
            # Process has died, send termination message and close connection
            if not pty_process.is_alive():
                try:
                    await websocket.send_text(json.dumps({
                        "type": "exit",
                        "data": "Shell process has exited"
                    }))
                    await websocket.close(code=1000, reason="Shell process exited")
                except Exception as e:
                    logger.warning(f"Error sending exit notification: {e}")
        
        # Start the read task
        import asyncio
        read_task = asyncio.create_task(read_from_pty())
        
        # Handle incoming messages from websocket
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
                
                if data.get("type") == "input":
                    input_data = data.get("data", "")
                    pty_process.write(input_data)
                elif data.get("type") == "resize":
                    # Handle terminal resize
                    cols = data.get("cols", 80)
                    rows = data.get("rows", 24)
                    # Ensure cols and rows are valid integers
                    if (isinstance(cols, int) and isinstance(rows, int) and 
                        cols > 0 and rows > 0):
                        pty_process.resize(cols, rows)
                
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Invalid JSON, ignore
                continue
            except Exception as e:
                logger.error(f"Error handling websocket message: {e}")
                break
        
        # Clean up
        read_task.cancel()
        
    except WebSocketDisconnect:
        logger.info(f"Shell session {session_id} disconnected")
    except Exception as e:
        logger.error(f"Error in shell session {session_id}: {e}")
    finally:
        # Clean up session
        if session_id in active_sessions:
            pty_process = active_sessions[session_id]
            if pty_process.is_alive():
                pty_process.terminate()
            del active_sessions[session_id]
            logger.info(f"Shell session {session_id} cleaned up")

@router.post("/execute", response_model=ShellResponse)
async def execute_shell_command(command_data: ShellCommand) -> ShellResponse:
    """Execute a single shell command (non-interactive)."""
    command = command_data.command.strip()
    
    if not command:
        raise HTTPException(status_code=400, detail="Command cannot be empty")
    
    # Security: Basic command validation
    dangerous_chars = [';', '&', '|', '`', '$', '(', ')', '<', '>', '&&', '||']
    if any(char in command for char in dangerous_chars):
        raise HTTPException(
            status_code=403, 
            detail="Command contains potentially dangerous characters"
        )
    
    # Log the command execution attempt
    logger.info(f"Executing shell command: {command}")
    
    try:
        # Execute the command with timeout
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1024 * 1024  # 1MB limit for output
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), 
                timeout=30  # 30 second timeout
            )
        except asyncio.TimeoutError:
            # Kill the process if it times out
            process.kill()
            await process.wait()
            raise HTTPException(
                status_code=408, 
                detail="Command timed out after 30 seconds"
            )
        
        # Decode output
        stdout_str = stdout.decode('utf-8', errors='replace') if stdout else ""
        stderr_str = stderr.decode('utf-8', errors='replace') if stderr else ""
        
        # Truncate output if too long
        max_output_length = 10000  # 10KB max output
        if len(stdout_str) > max_output_length:
            stdout_str = stdout_str[:max_output_length] + "\n... (output truncated)"
        if len(stderr_str) > max_output_length:
            stderr_str = stderr_str[:max_output_length] + "\n... (output truncated)"
        
        return ShellResponse(
            stdout=stdout_str,
            stderr=stderr_str,
            returncode=process.returncode or 0
        )
        
    except Exception as e:
        logger.error(f"Error executing command '{command}': {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to execute command: {str(e)}"
        )

@router.get("/sessions")
async def get_active_sessions() -> Dict[str, Any]:
    """Get list of active shell sessions."""
    # Clean up dead sessions
    dead_sessions = []
    for session_id, pty_process in active_sessions.items():
        if not pty_process.is_alive():
            dead_sessions.append(session_id)
    
    for session_id in dead_sessions:
        logger.info(f"Removing dead session: {session_id}")
        del active_sessions[session_id]
    
    return {
        "active_sessions": list(active_sessions.keys()),
        "total_count": len(active_sessions),
        "cleaned_dead_sessions": len(dead_sessions)
    }

@router.delete("/sessions/{session_id}")
async def terminate_session(session_id: str) -> Dict[str, str]:
    """Terminate a specific shell session."""
    if session_id in active_sessions:
        active_sessions[session_id].terminate()
        del active_sessions[session_id]
        return {"message": f"Session {session_id} terminated"}
    else:
        raise HTTPException(status_code=404, detail="Session not found") 