from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Optional, Tuple


_VER_RE = re.compile(r"(?P<maj>\d+)\.(?P<min>\d+)(?:\.(?P<patch>\d+))?")


def _parse_version_num(text: str) -> Optional[Tuple[int, int, int]]:
    """Parse a semantic MC version like '1.20.4' -> (1, 20, 4).

    Returns None if no version-like token found.
    """
    m = _VER_RE.search(text)
    if not m:
        return None
    maj = int(m.group("maj"))
    min_ = int(m.group("min"))
    patch = int(m.group("patch") or 0)
    return (maj, min_, patch)


def _fmt_mc_version(parts: Tuple[int, int, int]) -> str:
    """Format as '1.<minor>' or '1.<minor>.<patch>' when patch != 0."""
    maj, min_, patch = parts
    if patch:
        return f"{maj}.{min_}.{patch}"
    return f"{maj}.{min_}"


def detect_mc_version(server_path: str) -> Optional[str]:
    """Best-effort detection of the Minecraft version for a server folder.

    Heuristics (in order):
      1) versions/<ver>/server-<ver>.jar -> <ver>
      2) Root jars: minecraft_server.<ver>.jar, forge-<ver>-*.jar, paper-<ver>-*.jar, paperclip-<ver>-*.jar, fabric-*-<ver>*.jar
      3) Root JSON file named like '<ver>.json'
      4) logs/latest.log line: 'Starting minecraft server version <ver>'

    Returns normalized version string like '1.12.2' or None.
    """
    path = os.path.abspath(server_path)
    if not os.path.isdir(path):
        return None

    # 1) versions/<ver>/server-<ver>.jar
    versions_dir = os.path.join(path, "versions")
    if os.path.isdir(versions_dir):
        try:
            for entry in os.listdir(versions_dir):
                sub = os.path.join(versions_dir, entry)
                if not os.path.isdir(sub):
                    continue
                # Check folder name first
                parsed = _parse_version_num(entry)
                if parsed:
                    return _fmt_mc_version(parsed)
                # Or server-<ver>.jar inside
                for f in os.listdir(sub):
                    m = re.search(r"server-(\d+\.\d+(?:\.\d+)?)\.jar", f)
                    if m:
                        return m.group(1)
        except Exception:
            pass

    # 2) Known jar name patterns in root
    try:
        for f in os.listdir(path):
            if not f.endswith(".jar"):
                continue
            # vanilla minecraft_server.x.y[.z].jar
            m = re.search(r"minecraft_server\.(\d+\.\d+(?:\.\d+)?)\.jar", f)
            if m:
                return m.group(1)
            # forge-x.y[.z]-...
            m = re.search(r"forge-(\d+\.\d+(?:\.\d+)?)-", f)
            if m:
                return m.group(1)
            # paper-x.y[.z]-...
            m = re.search(r"paper(?:clip)?-(\d+\.\d+(?:\.\d+)?)-", f)
            if m:
                return m.group(1)
            # fabric ... - x.y[.z]
            m = re.search(r"fabric-.*?(\d+\.\d+(?:\.\d+)?)(?:[^\d]|$)", f)
            if m:
                return m.group(1)
    except Exception:
        pass

    # 3) Root JSON named like '<ver>.json'
    try:
        for f in os.listdir(path):
            if not f.endswith(".json"):
                continue
            parsed = _parse_version_num(f)
            if parsed:
                return _fmt_mc_version(parsed)
    except Exception:
        pass

    # 4) logs/latest.log extraction
    latest_log = os.path.join(path, "logs", "latest.log")
    if os.path.isfile(latest_log):
        try:
            with open(latest_log, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    m = re.search(r"Starting minecraft server version (\d+\.\d+(?:\.\d+)?)", line)
                    if m:
                        return m.group(1)
        except Exception:
            pass

    return None


def java_major_for_mc(mc_version: str) -> int:
    """Map a MC version (e.g. '1.12.2' or '1.21.1') to a Java major.

    Rules:
      - 1.8 → 1.16.x  => Java 8
      - 1.17.x        => Java 16
      - 1.18.x → 1.20.4 => Java 17
      - 1.20.5+ and 1.21+ => Java 21

    If parsing fails, default to Java 17 as a safe modern default.
    """
    parsed = _parse_version_num(mc_version)
    if not parsed:
        return 17
    _, minor, patch = parsed
    if minor <= 16:
        return 8
    if minor == 17:
        return 16
    if minor == 20 and patch >= 5:
        return 21
    if minor >= 21:
        return 21
    return 17


def _read_release_java_version(java_home: str) -> Optional[int]:
    """Parse the 'release' file inside a JDK/JRE to get major (8/11/17/21...)."""
    rel = os.path.join(java_home, "release")
    try:
        if os.path.isfile(rel):
            with open(rel, "r", encoding="utf-8", errors="ignore") as fh:
                txt = fh.read()
            m = re.search(r'JAVA_VERSION="([^"]+)"', txt)
            if m:
                v = m.group(1)
                # examples: 1.8.0_402, 17.0.11, 21
                if v.startswith("1."):
                    return 8
                n = re.match(r"(\d+)", v)
                if n:
                    return int(n.group(1))
    except Exception:
        pass
    return None


def _bin_java(java_home: str) -> str:
    exe = os.path.join(java_home, "bin", "java")
    if os.name == "nt":
        exe += ".exe"
    return exe


def _major_from_dirname(java_home: str) -> Optional[int]:
    """Try to infer Java major from directory name (Debian/Ubuntu style)."""
    base = os.path.basename(java_home)
    # matches java-8-openjdk-amd64, java-11-openjdk-amd64, java-17-openjdk-amd64, java-21-openjdk-amd64
    m = re.search(r"java-(\d+)(?:[^\d]|$)", base)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    # matches java-1.8.0-openjdk-amd64 -> 8
    m = re.search(r"java-1\.(\d+)\.\d+", base)
    if m:
        try:
            val = int(m.group(1))
            return 8 if val == 8 else None
        except Exception:
            return None
    return None


def _list_candidate_java_homes() -> list[str]:
    """Return a list of plausible JAVA_HOME directories to probe on this host."""
    cands: list[str] = []

    # Common Linux locations
    for root in ("/usr/lib/jvm", "/usr/lib64/jvm", "/usr/java"):
        if os.path.isdir(root):
            try:
                for entry in os.listdir(root):
                    home = os.path.join(root, entry)
                    if os.path.isdir(home) and os.path.isfile(_bin_java(home)):
                        cands.append(home)
            except Exception:
                pass

    # macOS
    mac_root = "/Library/Java/JavaVirtualMachines"
    if os.path.isdir(mac_root):
        try:
            for entry in os.listdir(mac_root):
                home = os.path.join(mac_root, entry, "Contents", "Home")
                if os.path.isdir(home) and os.path.isfile(_bin_java(home)):
                    cands.append(home)
        except Exception:
            pass

    # Windows Program Files
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if pf and os.path.isdir(pf):
            try:
                java_dir = os.path.join(pf, "Java")
                if os.path.isdir(java_dir):
                    for entry in os.listdir(java_dir):
                        home = os.path.join(java_dir, entry)
                        if os.path.isdir(home) and os.path.isfile(_bin_java(home)):
                            cands.append(home)
            except Exception:
                pass

    # De-duplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for h in cands:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _find_java_by_env(major: int) -> Optional[str]:
    """Optionally resolve via env vars like JAVA_8, JAVA_17, JAVA_21 or JAVA_HOME_17."""
    # Direct path to binary
    direct = os.getenv(f"JAVA_{major}")
    if direct and os.path.isfile(direct):
        return direct
    # JAVA_HOME_<major>
    home = os.getenv(f"JAVA_HOME_{major}")
    if home:
        exe = os.path.join(home, "bin", "java")
        if os.name == "nt":
            exe += ".exe"
        if os.path.isfile(exe):
            return exe
    return None


def find_java_for_major(major: int) -> Optional[str]:
    """Locate a java executable for the requested major version.

    Resolution order:
      1) Env overrides: JAVA_<major> (path to binary), JAVA_HOME_<major>
      2) Scan known install roots for matching majors (via 'release' file)
      3) Fallback to 'java' on PATH if it matches the requested major

    Returns absolute path to the java binary or None.
    """
    # 1) Env overrides
    env = _find_java_by_env(major)
    if env:
        return env

    # 2) Probe known homes
    for home in _list_candidate_java_homes():
        exe = _bin_java(home)
        home_major = _read_release_java_version(home)
        if home_major is None:
            # Try directory name hint (Debian/Ubuntu)
            home_major = _major_from_dirname(home)
        if home_major is None and os.path.isfile(exe):
            # As a last resort, run '<home>/bin/java -version'
            try:
                proc = subprocess.run([exe, "-version"], capture_output=True, text=True)
                ver_out = proc.stderr or proc.stdout
                if "\"1." in ver_out and major == 8:
                    home_major = 8
                else:
                    m = re.search(r'"(\d+)', ver_out)
                    if m:
                        home_major = int(m.group(1))
            except Exception:
                pass
        if home_major == major and os.path.isfile(exe):
            return exe

    # 3) Fallback to PATH 'java' if it matches
    try:
        proc = subprocess.run(["java", "-version"], capture_output=True, text=True)
        ver_out = proc.stderr or proc.stdout
        # Outputs examples: 'openjdk version "17.0.9"' or 'java version "1.8.0_402"'
        if "version" in ver_out:
            if "\"1." in ver_out and major == 8:
                return "java"
            m = re.search(r'"(\d+)', ver_out)
            if m and int(m.group(1)) == major:
                return "java"
    except Exception:
        pass

    return None


def resolve_java_for_server(server_path: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Return (java_exe, mc_version, java_major) for a given server folder.

    If detection fails at any step, returns None for that element.
    """
    mc = detect_mc_version(server_path)
    if not mc:
        return (None, None, None)
    major = java_major_for_mc(mc)
    java_exe = find_java_for_major(major)
    return (java_exe, mc, major)
