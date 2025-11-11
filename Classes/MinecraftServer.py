from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class MinecraftServer:
    """Simple Minecraft server wrapper.

    Attributes:
        path: Directory containing `server.jar`.
        xmx: Max heap size in GB.
        xms: Initial heap size in GB.
        name: Optional display name; defaults to the folder name.
        pid: Process ID of the running server (0 if stopped).
    """

    path: str
    xmx: int
    xms: int
    name: Optional[str] = None
    pid: int = 0
    proc: Optional[subprocess.Popen] = None

    def __post_init__(self) -> None:
        # Normalize and set default name
        self.path = os.path.abspath(self.path)
        if not self.name:
            self.name = os.path.basename(self.path)
        # Ensure integers
        try:
            self.xmx = int(self.xmx)
            self.xms = int(self.xms)
        except Exception:
            self.xmx = -1
            self.xms = -1

    @property
    def jar(self) -> str:
        return os.path.join(self.path, "server.jar")

    def is_running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return True
        else:
            return True

    def start(self) -> int:
        """Start the server; return PID or -1 on error.

        Runs: java -Xmx{xmx}G -Xms{xms}G -jar server.jar nogui
        in the server directory.
        """
        try:
            if self.xmx <= 0 or self.xms <= 0 or self.xmx <= self.xms:
                return -1
            if not os.path.isfile(self.jar):
                return -1
            cmd = [
                "java",
                f"-Xmx{int(self.xmx)}G",
                f"-Xms{int(self.xms)}G",
                "-jar",
                "server.jar",
                "nogui",
            ]
            self.proc = subprocess.Popen(
                cmd,
                cwd=self.path,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self.pid = self.proc.pid
            return self.pid
        except Exception:
            return -1

    def stop(self) -> int:
        """Send "stop" to the server and return immediately.

        Returns 0 if the command was sent successfully, -1 on error.
        """
        try:
            if self.pid <= 0:
                return -1
            return 0 if self.send_command("stop") == 0 else -1
        except Exception:
            return -1

    def send_command(self, command: str) -> int:
        """Send a console command to the server via stdin.

        Works only if this process started the server (self.proc present).
        For cross-process control or after a restart, prefer enabling RCON
        in server.properties and using an RCON client.

        Returns 0 on success, -1 on error.
        """
        try:
            if not self.proc or not self.proc.stdin or not self.is_running():
                return -1
            # Ensure newline-terminated command
            line = command if command.endswith("\n") else command + "\n"
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
            return 0
        except Exception:
            return -1
