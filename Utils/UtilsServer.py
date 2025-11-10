from __future__ import annotations

import logging
import os
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)


def _default_servers_root() -> str:
    """Return absolute path to the `Servers` directory.

    Resolves relative to this file's parent directory so callers
    don't need to rely on the current working directory.
    """
    here = os.path.dirname(__file__)
    project_root = os.path.dirname(here)
    return os.path.abspath(os.path.join(project_root, "Servers"))


def list_servers(servers_root: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """List available Minecraft servers under the `Servers/` directory.

    A "server" is any direct subdirectory of `Servers/`.

    Args:
        servers_root: Optional explicit path to the servers root directory.
                      Defaults to the project's `Servers/` directory.

    Returns:
        (names, paths):
          - names: list of server directory names
          - paths: list of absolute paths corresponding to each name
    """
    root = os.path.abspath(servers_root or _default_servers_root())

    if not os.path.exists(root):
        logger.warning("Servers directory does not exist: %s", root)
        return [], []

    names: list[str] = []
    paths: list[str] = []

    try:
        for entry in os.listdir(root):
            full = os.path.join(root, entry)
            if os.path.isdir(full):
                names.append(entry)
                paths.append(os.path.abspath(full))
    except Exception:
        logger.exception("Failed to read servers from %s", root)
        return [], []

    # Keep output stable and aligned: sort by name and reorder paths to match
    order = sorted(range(len(names)), key=lambda i: names[i].lower())
    names = [names[i] for i in order]
    paths = [paths[i] for i in order]

    return names, paths
