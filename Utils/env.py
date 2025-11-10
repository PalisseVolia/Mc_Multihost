from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def load_env_from_file(path: str = "config/.env") -> None:
    """Load key=value pairs from a .env file into os.environ.

    - Does not overwrite variables already set in the environment.
    - Supports lines like `KEY=VALUE`, quotes, and `export KEY=VALUE`.
    - Ignores blank lines and comments starting with `#`.
    """
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].lstrip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and (
                    (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
                ):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("Failed to load environment from %s", path)


def get_env(name: str, required: bool = False) -> Optional[str]:
    value = os.getenv(name)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def parse_int_ids(ids: Optional[str]) -> list[int]:
    if not ids:
        return []
    result: list[int] = []
    for part in ids.replace(";", ",").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            result.append(int(token))
        except ValueError:
            logger.warning("Ignoring invalid guild id: %r", token)
    # Deduplicate while preserving order
    seen: set[int] = set()
    unique: list[int] = []
    for gid in result:
        if gid not in seen:
            seen.add(gid)
            unique.append(gid)
    return unique

