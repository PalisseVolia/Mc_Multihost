from __future__ import annotations

import os
import subprocess
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from Utils.McJava import resolve_java_for_server


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
    log_path: Optional[str] = None

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

        Runs: <java> -Xmx{xmx}G -Xms{xms}G -jar server.jar nogui
        in the server directory.
        """
        try:
            logger = logging.getLogger(__name__)
            if self.xmx <= 0 or self.xms <= 0 or self.xmx < self.xms:
                logger.error(
                    "Invalid memory settings for %s: xmx=%s xms=%s", self.name, self.xmx, self.xms
                )
                return -1
            if not os.path.isfile(self.jar):
                logger.error("Missing server.jar in %s", self.path)
                return -1
            # Resolve a suitable Java executable for this server
            java_exe, _mc_version, _java_major = resolve_java_for_server(self.path)
            if not java_exe:
                # Fallback to PATH 'java'
                java_exe = "java"
                logger.warning(
                    "Could not resolve specific Java for %s (MC=%s, target Java=%s). Falling back to PATH 'java'",
                    self.name,
                    _mc_version,
                    _java_major,
                )

            cmd = [
                java_exe,
                f"-Xmx{int(self.xmx)}G",
                f"-Xms{int(self.xms)}G",
                "-jar",
                "server.jar",
                "nogui",
            ]
            # Prepare log file to capture stdout/stderr from Java
            log_dir = os.path.join(self.path, "bot-logs")
            try:
                os.makedirs(log_dir, exist_ok=True)
            except Exception:
                logger.exception("Failed to create log directory: %s", log_dir)
                log_dir = self.path
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log_path = os.path.join(log_dir, f"console-{timestamp}.log")
            try:
                log_fh = open(log_path, "w", encoding="utf-8")
                self.log_path = log_path
            except Exception:
                logger.exception("Failed to open log file, will discard output: %s", log_path)
                log_fh = subprocess.DEVNULL

            logger.info(
                "Starting server %s | MC=%s Java=%s | cmd=%s | cwd=%s | log=%s",
                self.name,
                _mc_version,
                _java_major,
                " ".join(cmd),
                self.path,
                self.log_path,
            )

            self.proc = subprocess.Popen(
                cmd,
                cwd=self.path,
                stdin=subprocess.PIPE,
                stdout=log_fh,
                stderr=log_fh,
                text=True,
            )
            self.pid = self.proc.pid
            return self.pid
        except Exception:
            logging.getLogger(__name__).exception("Failed to start server %s", getattr(self, "name", "?"))
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
