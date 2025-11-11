from __future__ import annotations

import os
from typing import List, Optional

from Classes.MinecraftServer import MinecraftServer


def _servers_root(servers_root: Optional[str] = None) -> str:
    """Return absolute path to the `Servers` directory.

    Resolves relative to this file so callers don't depend on CWD.
    """
    if servers_root:
        return os.path.abspath(servers_root)
    here = os.path.dirname(__file__)
    project_root = os.path.dirname(here)
    return os.path.abspath(os.path.join(project_root, "Servers"))


def get_servers(servers_root: Optional[str] = None) -> List[MinecraftServer]:
    """Discover all server folders and return MinecraftServer objects.

    - A server is any direct subdirectory of the Servers/ folder.
    - Each object is initialized with the provided xmx/xms.
    """
    xmx = 4
    xms = 2
    root = _servers_root(servers_root)
    servers: list[MinecraftServer] = []
    if not os.path.isdir(root):
        return servers
    for entry in os.listdir(root):
        full = os.path.join(root, entry)
        if os.path.isdir(full):
            servers.append(MinecraftServer(path=full, xmx=int(xmx), xms=int(xms), name=entry))
    # Sort by name for a stable order
    servers.sort(key=lambda s: (s.name or "").lower())
    return servers
