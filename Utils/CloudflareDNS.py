from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable, Optional

from Utils.UtilsServer import get_server_info
from Utils.env import get_env, load_env_from_file

logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"
PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ifconfig.me/ip",
)


@dataclass(frozen=True)
class DnsSyncResult:
    name: str
    ip: str
    action: str
    record_id: str


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _looks_like_ip(value: str) -> bool:
    try:
        socket.inet_aton(value)
        return True
    except OSError:
        return False


def _extract_hostname(address: object) -> Optional[str]:
    raw = str(address or "").strip()
    if not raw:
        return None

    host = raw
    if raw.startswith("[") and "]" in raw:
        host = raw[1 : raw.index("]")]
    elif raw.count(":") == 1:
        maybe_host, maybe_port = raw.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_host

    host = host.strip().strip(".").lower()
    if not host or _looks_like_ip(host):
        return None
    return host


def _names_from_server_info() -> list[str]:
    names: set[str] = set()
    info = get_server_info()
    if not isinstance(info, dict):
        return []

    for entry in info.values():
        if not isinstance(entry, dict):
            continue
        name = _extract_hostname(entry.get("ip") or entry.get("address"))
        if name:
            names.add(name)
    return sorted(names)


def _names_from_env() -> list[str]:
    raw = get_env("CLOUDFLARE_DNS_NAMES")
    if not raw:
        return []
    names: set[str] = set()
    for part in raw.replace(";", ",").split(","):
        name = _extract_hostname(part)
        if name:
            names.add(name)
    return sorted(names)


def _infer_zone_name(hostnames: Iterable[str]) -> Optional[str]:
    suffixes: set[str] = set()
    for hostname in hostnames:
        labels = [label for label in hostname.split(".") if label]
        if len(labels) < 2:
            continue
        suffixes.add(".".join(labels[-2:]))
    if len(suffixes) == 1:
        return next(iter(suffixes))
    return None


def detect_public_ipv4() -> str:
    last_error: Optional[Exception] = None
    for url in PUBLIC_IP_ENDPOINTS:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                value = response.read().decode("utf-8").strip()
            if _looks_like_ip(value):
                return value
            raise ValueError(f"Invalid IPv4 returned by {url}: {value!r}")
        except Exception as exc:
            last_error = exc
            logger.warning("Public IP lookup failed via %s: %s", url, exc)
    raise RuntimeError("Unable to determine public IPv4 address") from last_error


def _cloudflare_request(
    path: str,
    token: str,
    method: str = "GET",
    query: Optional[dict[str, object]] = None,
    payload: Optional[dict[str, object]] = None,
) -> dict:
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    body = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare API {method} {path} failed: {exc.code} {error_body}") from exc

    data = json.loads(raw)
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare API {method} {path} returned an error: {data}")
    return data


def _get_zone_id(token: str, zone_name: str) -> str:
    data = _cloudflare_request("/zones", token=token, query={"name": zone_name})
    zones = data.get("result") or []
    if not zones:
        raise RuntimeError(f"Cloudflare zone not found: {zone_name}")
    zone_id = zones[0].get("id")
    if not zone_id:
        raise RuntimeError(f"Cloudflare zone missing id: {zone_name}")
    return str(zone_id)


def _find_a_record(token: str, zone_id: str, fqdn: str) -> Optional[dict]:
    data = _cloudflare_request(
        f"/zones/{zone_id}/dns_records",
        token=token,
        query={"name": fqdn, "type": "A"},
    )
    records = data.get("result") or []
    return records[0] if records else None


def sync_dns_records(
    *,
    token: str,
    zone_name: str,
    hostnames: Iterable[str],
    public_ip: Optional[str] = None,
) -> list[DnsSyncResult]:
    unique_names = sorted({name.strip().strip(".").lower() for name in hostnames if name})
    if not unique_names:
        return []

    current_ip = public_ip or detect_public_ipv4()
    zone_id = _get_zone_id(token, zone_name)
    results: list[DnsSyncResult] = []

    for hostname in unique_names:
        record = _find_a_record(token, zone_id, hostname)
        if record:
            record_id = str(record.get("id") or "")
            old_ip = str(record.get("content") or "")
            if old_ip == current_ip:
                results.append(
                    DnsSyncResult(name=hostname, ip=current_ip, action="unchanged", record_id=record_id)
                )
                continue

            _cloudflare_request(
                f"/zones/{zone_id}/dns_records/{record_id}",
                token=token,
                method="PATCH",
                payload={"content": current_ip},
            )
            results.append(DnsSyncResult(name=hostname, ip=current_ip, action="updated", record_id=record_id))
            continue

        data = _cloudflare_request(
            f"/zones/{zone_id}/dns_records",
            token=token,
            method="POST",
            payload={
                "type": "A",
                "name": hostname,
                "content": current_ip,
                "ttl": 1,
                "proxied": False,
            },
        )
        created = data.get("result") or {}
        results.append(
            DnsSyncResult(
                name=hostname,
                ip=current_ip,
                action="created",
                record_id=str(created.get("id") or ""),
            )
        )

    return results


def sync_cloudflare_dns_from_env() -> list[DnsSyncResult]:
    token = get_env("CLOUDFLARE_API_TOKEN")
    if not token:
        logger.info("Skipping Cloudflare DNS sync: CLOUDFLARE_API_TOKEN is not set")
        return []

    env_names = _names_from_env()
    info_names = _names_from_server_info()
    hostnames = sorted(set(env_names) | set(info_names))
    if not hostnames:
        logger.info("Skipping Cloudflare DNS sync: no DNS names found")
        return []

    zone_name = get_env("CLOUDFLARE_ZONE_NAME") or _infer_zone_name(hostnames)
    if not zone_name:
        raise RuntimeError(
            "Unable to infer Cloudflare zone name. Set CLOUDFLARE_ZONE_NAME explicitly."
        )

    public_ip = get_env("PUBLIC_IPV4") or None
    results = sync_dns_records(
        token=token,
        zone_name=zone_name,
        hostnames=hostnames,
        public_ip=public_ip,
    )

    for result in results:
        logger.info("Cloudflare DNS %s: %s -> %s", result.action, result.name, result.ip)
    return results


def maybe_sync_cloudflare_dns_on_startup() -> list[DnsSyncResult]:
    enabled = _parse_bool(get_env("CLOUDFLARE_SYNC_ON_START"), default=True)
    if not enabled:
        logger.info("Skipping Cloudflare DNS sync: CLOUDFLARE_SYNC_ON_START is disabled")
        return []
    return sync_cloudflare_dns_from_env()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )
    load_env_from_file()
    results = sync_cloudflare_dns_from_env()
    if not results:
        logger.info("No Cloudflare DNS changes were needed")
        return
    changed = [result for result in results if result.action != "unchanged"]
    logger.info(
        "Cloudflare DNS sync complete: %d record(s), %d changed",
        len(results),
        len(changed),
    )


if __name__ == "__main__":
    main()
