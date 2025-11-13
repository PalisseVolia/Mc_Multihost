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
        path: Directory containing `server.jar` or a launcher script (e.g. run.sh).
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

        If `server.jar` exists, runs:
          <java> -Xmx{xmx}G -Xms{xms}G -jar server.jar nogui

        Otherwise, if a known launcher script exists (run.sh, serverstart.sh,
        startserver.sh, start.sh), updates memory in user_jvm_args.txt then runs:
          ./<script> nogui

        In both cases, the command is executed in the server directory and
        stdout/stderr are piped to a timestamped log file.
        """
        try:
            logger = logging.getLogger(__name__)
            if self.xmx <= 0 or self.xms <= 0 or self.xmx < self.xms:
                logger.error(
                    "Invalid memory settings for %s: xmx=%s xms=%s", self.name, self.xmx, self.xms
                )
                return -1
            # Resolve a suitable Java executable for this server (best effort)
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
            use_jar = os.path.isfile(self.jar)
            script_candidates = ["run.sh", "serverstart.sh", "startserver.sh", "start.sh"]
            script_name = None
            if not use_jar:
                for cand in script_candidates:
                    p = os.path.join(self.path, cand)
                    if os.path.isfile(p):
                        script_name = cand
                        break
                if not script_name:
                    logger.error("Missing server.jar or launcher script in %s", self.path)
                    return -1
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
            if use_jar:
                cmd = [
                    java_exe,
                    f"-Xmx{int(self.xmx)}G",
                    f"-Xms{int(self.xms)}G",
                    "-jar",
                    "server.jar",
                    "nogui",
                ]
                logger.info(
                    "Starting (jar) %s | MC=%s Java=%s | cmd=%s | cwd=%s | log=%s",
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
            else:
                # Update memory settings in user_jvm_args.txt for script-based launchers
                try:
                    jvm_args = os.path.join(self.path, "user_jvm_args.txt")
                    lines: list[str] = []
                    if os.path.isfile(jvm_args):
                        with open(jvm_args, "r", encoding="utf-8", errors="ignore") as fh:
                            lines = [ln.rstrip("\n") for ln in fh]
                    # Filter out existing -Xmx/-Xms and append new ones in GB
                    def _keep(l: str) -> bool:
                        ls = l.strip()
                        return not (ls.startswith("-Xmx") or ls.startswith("-Xms"))
                    kept = [l for l in lines if _keep(l)]
                    kept.append(f"-Xmx{int(self.xmx)}G")
                    kept.append(f"-Xms{int(self.xms)}G")
                    with open(jvm_args, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(kept) + "\n")
                except Exception:
                    logger.exception("Failed to update user_jvm_args.txt for %s", self.name)

                # Build environment to prioritize resolved Java (prepend its bin to PATH)
                env = os.environ.copy()
                try:
                    java_bin = os.path.dirname(java_exe) if java_exe else None
                    if java_bin:
                        env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
                except Exception:
                    pass

                cmd = [f"./{script_name}", "nogui"]  # type: ignore[list-item]
                logger.info(
                    "Starting (script) %s via %s | MC=%s Java=%s | cmd=%s | cwd=%s | log=%s",
                    self.name,
                    script_name,
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
                    env=env,
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
