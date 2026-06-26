import asyncio
import copy
import ipaddress
import json
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials


APP_NAME = os.getenv("APP_NAME", "The333-BGP")
WEB_USER = (os.getenv("WEB_USER", "admin").strip() or "admin")
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
if not WEB_PASSWORD or WEB_PASSWORD == "CHANGE_ME_STRONG_PASSWORD":
    raise RuntimeError("WEB_PASSWORD must be set to a strong non-default value")

DATA_DIR = Path("/data")
CONFIG_DIR = Path("/config")
APP_DIR = Path("/app")

SOURCES_FILE = DATA_DIR / "sources.json"
DEFAULT_SOURCES_FILE = CONFIG_DIR / "default_sources.json"

ADVERTISED_FILE = DATA_DIR / "advertised_prefixes.txt"
LAST_GOOD_FILE = DATA_DIR / "last_good_prefixes.txt"
ADVERTISED_ROUTE_ATTRIBUTES_FILE = DATA_DIR / "advertised_route_attributes.json"
STATUS_FILE = DATA_DIR / "status.json"
SOURCES_BACKUP_DIR = DATA_DIR / "sources_backups"
UPDATE_HISTORY_FILE = DATA_DIR / "update_history.jsonl"
UPDATE_HISTORY_MAX_LINES = int(os.getenv("UPDATE_HISTORY_MAX_LINES", "1000"))
JOBS_FILE = DATA_DIR / "jobs.json"
JOB_HISTORY_MAX_ITEMS = int(os.getenv("JOB_HISTORY_MAX_ITEMS", "200"))
COMMUNITY_PROFILES_FILE = DATA_DIR / "community_profiles.json"

BGP_COMMUNITY = os.getenv("BGP_COMMUNITY", "65432:500")
BGP_NEXTHOP = os.getenv("BGP_NEXTHOP", os.getenv("ROUTER_ID", "192.168.1.111"))
LOCAL_AS = os.getenv("LOCAL_AS", "64500")
PEER_AS = os.getenv("PEER_AS", "65455")
PEER_ADDRESS = os.getenv("PEER_ADDRESS", "192.168.1.1")
ROUTER_ID = os.getenv("ROUTER_ID", "192.168.1.111")
GOBGP_API_HOST = os.getenv("GOBGP_API_HOST", "127.0.0.1")
GOBGP_API_PORT = int(os.getenv("GOBGP_API_PORT", "50051"))

REJECT_DEFAULT_ROUTE = os.getenv("REJECT_DEFAULT_ROUTE", "true").lower() in ("1", "true", "yes", "on")
REJECT_PRIVATE_RESERVED = os.getenv("REJECT_PRIVATE_RESERVED", "true").lower() in ("1", "true", "yes", "on")
AGGREGATE_PREFIXES = os.getenv("AGGREGATE_PREFIXES", "true").lower() in ("1", "true", "yes", "on")

MIN_PREFIXLEN = int(os.getenv("MIN_PREFIXLEN", "8"))
MAX_PREFIXLEN = int(os.getenv("MAX_PREFIXLEN", "32"))
MAX_PREFIXES = int(os.getenv("MAX_PREFIXES", "30000"))
MIN_PREFIXES_TO_APPLY = int(os.getenv("MIN_PREFIXES_TO_APPLY", "1"))
MIN_EXPECTED_PREFIXES = int(os.getenv("MIN_EXPECTED_PREFIXES", "0"))
MAX_DELTA_PERCENT = float(os.getenv("MAX_DELTA_PERCENT", "0"))
URL_FETCH_TIMEOUT_SECONDS = float(os.getenv("URL_FETCH_TIMEOUT_SECONDS", "30"))
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "true").lower() in ("1", "true", "yes", "on")
UPDATE_INTERVAL_SECONDS = int(os.getenv("UPDATE_INTERVAL_SECONDS", "21600"))

SERVICE_ROUTES_ENABLED = os.getenv("SERVICE_ROUTES_ENABLED", "false").lower() in ("1", "true", "yes", "on")
SERVICE_DNS_CACHE_GRACE_SECONDS = int(os.getenv("SERVICE_DNS_CACHE_GRACE_SECONDS", "86400"))
SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER = int(os.getenv("SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER", "100"))
SERVICE_GEOSITE_HTTP_TIMEOUT_SECONDS = int(os.getenv("SERVICE_GEOSITE_HTTP_TIMEOUT_SECONDS", "20"))
SERVICE_GEOSITE_INCLUDE_MAX_DEPTH = int(os.getenv("SERVICE_GEOSITE_INCLUDE_MAX_DEPTH", "3"))
SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER = int(os.getenv("SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER", "40"))
SERVICE_DNS_RESOLVE_DELAY_SECONDS = float(os.getenv("SERVICE_DNS_RESOLVE_DELAY_SECONDS", "0.10"))
SERVICE_DNS_RESOLVE_RETRIES = int(os.getenv("SERVICE_DNS_RESOLVE_RETRIES", "3"))
SERVICE_DNS_RESOLVE_RETRY_DELAY_SECONDS = float(os.getenv("SERVICE_DNS_RESOLVE_RETRY_DELAY_SECONDS", "0.50"))
SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER = int(os.getenv("SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER", "500"))
SERVICE_GEOIP_HTTP_TIMEOUT_SECONDS = int(os.getenv("SERVICE_GEOIP_HTTP_TIMEOUT_SECONDS", "20"))

PRODUCT_VERSION_FILE = Path(os.getenv("PRODUCT_VERSION_FILE", str(APP_DIR / "VERSION")))
PRODUCT_VERSION = os.getenv("PRODUCT_VERSION", "").strip()
PRODUCT_CHANNEL = os.getenv("PRODUCT_CHANNEL", "stable").strip() or "stable"
PRODUCT_UPDATE_MANIFEST_URL = os.getenv("PRODUCT_UPDATE_MANIFEST_URL", "").strip()
PRODUCT_UPDATE_ENABLED = os.getenv("PRODUCT_UPDATE_ENABLED", "false").lower() in ("1", "true", "yes", "on")
PRODUCT_UPDATE_COMMAND = os.getenv("PRODUCT_UPDATE_COMMAND", "/opt/the333-bgp/scripts/the333bgp.sh update --non-interactive").strip()
PRODUCT_UPDATE_TIMEOUT_SECONDS = int(os.getenv("PRODUCT_UPDATE_TIMEOUT_SECONDS", "1800"))

PREFIX_RE = re.compile(
    r"(?<![0-9.])"
    r"("
    r"(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])"
    r"(?:\.(?:25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}"
    r"(?:/[0-9]{1,2})?"
    r")"
    r"(?![0-9.])"
)


app = FastAPI(title=APP_NAME)
security = HTTPBasic()
JOBS_LOCK = threading.RLock()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    user_ok = secrets.compare_digest(credentials.username, WEB_USER)
    pass_ok = secrets.compare_digest(credentials.password, WEB_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_product_version() -> str:
    if PRODUCT_VERSION:
        return PRODUCT_VERSION

    try:
        value = PRODUCT_VERSION_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except Exception:
        pass

    return "0.1"


def bundled_update_manifest() -> dict[str, Any]:
    version = read_product_version()
    return {
        "ok": True,
        "product": APP_NAME,
        "current_version": version,
        "current_channel": PRODUCT_CHANNEL,
        "manifest_url": PRODUCT_UPDATE_MANIFEST_URL or None,
        "update_enabled": PRODUCT_UPDATE_ENABLED,
        "versions": [
            {
                "version": version,
                "title": f"{version} stable",
                "channel": "stable",
                "status": "установлена",
                "date": "2026-06",
                "recommended": True,
                "changelog": [
                    "Первая production-подготовленная версия 0.1.",
                    "Портал управления BGP-маршрутами, источниками, сервисными модулями и Community-профилями.",
                    "Каркас безопасной установки и обновления через GitHub manifest.",
                ],
            }
        ],
        "latest": {
            "stable": version,
            "beta": None,
        },
        "time": now_iso(),
    }


async def load_update_manifest() -> dict[str, Any]:
    fallback = bundled_update_manifest()

    if not PRODUCT_UPDATE_MANIFEST_URL:
        return fallback

    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(PRODUCT_UPDATE_MANIFEST_URL)
            response.raise_for_status()
            manifest = response.json()

        if not isinstance(manifest, dict):
            raise ValueError("manifest root must be an object")

        manifest.setdefault("ok", True)
        manifest.setdefault("product", APP_NAME)
        manifest.setdefault("current_version", fallback["current_version"])
        manifest.setdefault("current_channel", PRODUCT_CHANNEL)
        manifest.setdefault("manifest_url", PRODUCT_UPDATE_MANIFEST_URL)
        manifest.setdefault("update_enabled", PRODUCT_UPDATE_ENABLED)
        manifest.setdefault("time", now_iso())
        return manifest
    except Exception as e:
        fallback["ok"] = False
        fallback["error"] = str(e)
        return fallback


def run_cmd(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def gobgp_cli_args(args: list[str]) -> list[str]:
    command_args = args[1:] if args and args[0] == "gobgp" else args
    return [
        "gobgp",
        "-u",
        GOBGP_API_HOST,
        "-p",
        str(GOBGP_API_PORT),
        *command_args,
    ]


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))


def save_status(data: dict[str, Any]) -> dict[str, Any]:
    data["updated_at"] = now_iso()
    write_json_atomic(STATUS_FILE, data)
    return data


def ensure_sources_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if SOURCES_FILE.exists():
        return

    if DEFAULT_SOURCES_FILE.exists():
        SOURCES_FILE.write_text(
            DEFAULT_SOURCES_FILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    else:
        write_json_atomic(SOURCES_FILE, [])


def gobgp_ready() -> bool:
    result = run_cmd(gobgp_cli_args(["global"]), timeout=5)
    return result.returncode == 0


def gobgp_text(args: list[str]) -> str:
    result = run_cmd(gobgp_cli_args(args), timeout=20)
    text = result.stdout if result.stdout else result.stderr
    return text.strip() + ("\n" if text else "")


def parse_prefix(value: str) -> ipaddress.IPv4Network | None:
    value = value.strip()

    if not value:
        return None

    try:
        if "/" not in value:
            value = value + "/32"

        net = ipaddress.ip_network(value, strict=False)

        if not isinstance(net, ipaddress.IPv4Network):
            return None

    except Exception:
        return None

    if REJECT_DEFAULT_ROUTE and net.prefixlen == 0:
        return None

    if net.prefixlen < MIN_PREFIXLEN or net.prefixlen > MAX_PREFIXLEN:
        return None

    if REJECT_PRIVATE_RESERVED:
        if (
            net.is_private
            or net.is_loopback
            or net.is_link_local
            or net.is_multicast
            or net.is_reserved
            or net.is_unspecified
        ):
            return None

    return net


def fetch_url_text(url: str) -> str:
    headers = {
        "User-Agent": f"{APP_NAME}/1.0"
    }

    with httpx.Client(
        timeout=httpx.Timeout(URL_FETCH_TIMEOUT_SECONDS),
        follow_redirects=True,
        headers=headers,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def extract_prefixes_from_text(text: str) -> tuple[set[ipaddress.IPv4Network], int, int]:
    prefixes: set[ipaddress.IPv4Network] = set()
    matches = 0
    ignored = 0

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue

        for match in PREFIX_RE.finditer(stripped):
            matches += 1
            net = parse_prefix(match.group(1))
            if net is None:
                ignored += 1
                continue
            prefixes.add(net)

    return prefixes, matches, ignored


def normalize_manual_entry(value: str) -> tuple[str, str] | None:
    cleaned = value.split("#", 1)[0].strip()

    if not cleaned:
        return None

    prefix = parse_prefix(cleaned)
    if prefix is not None:
        return ("prefix", str(prefix))

    host = cleaned

    if "://" in cleaned:
        parsed = urlparse(cleaned)
        host = parsed.hostname or ""
    elif "/" in cleaned:
        parsed = urlparse(f"//{cleaned}")
        host = parsed.hostname or ""

    host = host.strip().lower().strip(".")

    if host.startswith("*."):
        host = host[2:]

    if not host or len(host) > 253 or "." not in host:
        return None

    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", host):
        return None

    return ("domain", host)


def collect_one_source(source: dict[str, Any]) -> tuple[set[ipaddress.IPv4Network], dict[str, Any]]:
    name = str(source.get("name", "unnamed"))
    enabled = bool(source.get("enabled", False))
    source_type = str(source.get("type", "")).lower()

    stat = {
        "name": name,
        "enabled": enabled,
        "type": source_type,
        "group": source.get("group"),
        "strategy": source.get("strategy"),
        "priority": source.get("priority"),
        "accepted": 0,
        "ignored": 0,
        "error": None,
        "selected": False,
        "skipped": False,
    }

    collected: set[ipaddress.IPv4Network] = set()

    if not enabled:
        stat["skipped"] = True
        return collected, stat

    if source_type == "static":
        raw_prefixes = source.get("prefixes", [])
        if not isinstance(raw_prefixes, list):
            stat["error"] = "prefixes must be a list"
            return collected, stat

        raw_manual_entries = source.get("manual_entries", [])
        if not isinstance(raw_manual_entries, list):
            stat["error"] = "manual_entries must be a list"
            return collected, stat

        manual_stats: list[dict[str, Any]] = []

        for item in raw_prefixes:
            net = parse_prefix(str(item))
            if net is None:
                stat["ignored"] += 1
                continue

            collected.add(net)
            stat["accepted"] += 1

        for item in raw_manual_entries:
            entry = normalize_manual_entry(str(item))

            if entry is None:
                stat["ignored"] += 1
                manual_stats.append(
                    {
                        "entry": str(item),
                        "type": "invalid",
                        "accepted": 0,
                        "ignored": 1,
                        "error": "unsupported manual entry",
                    }
                )
                continue

            entry_type, value = entry

            if entry_type == "prefix":
                net = parse_prefix(value)
                if net is None:
                    stat["ignored"] += 1
                    manual_stats.append(
                        {
                            "entry": str(item),
                            "type": "prefix",
                            "accepted": 0,
                            "ignored": 1,
                            "error": "invalid prefix",
                        }
                    )
                    continue

                collected.add(net)
                stat["accepted"] += 1
                manual_stats.append(
                    {
                        "entry": str(item),
                        "type": "prefix",
                        "value": str(net),
                        "accepted": 1,
                        "ignored": 0,
                        "error": None,
                    }
                )
                continue

            domain_prefixes, domain_stat = dns_resolve_ipv4(value)
            collected.update(domain_prefixes)
            stat["accepted"] += len(domain_prefixes)
            stat["ignored"] += int(domain_stat.get("ignored", 0))
            manual_stats.append(
                {
                    "entry": str(item),
                    "type": "domain",
                    **domain_stat,
                }
            )

        stat["manual_entries"] = len(raw_manual_entries)
        stat["manual_stats"] = manual_stats
        return collected, stat

    if source_type == "url":
        url = str(source.get("url", "")).strip()
        if not url:
            stat["error"] = "url must not be empty"
            return collected, stat

        try:
            body = fetch_url_text(url)
            url_prefixes, matches, ignored = extract_prefixes_from_text(body)
            collected.update(url_prefixes)

            stat["accepted"] = len(url_prefixes)
            stat["ignored"] = ignored
            stat["matches"] = matches
            stat["bytes"] = len(body.encode("utf-8", errors="ignore"))

        except Exception as e:
            stat["error"] = str(e)

        return collected, stat

    stat["error"] = f"unsupported source type: {source_type}"
    return collected, stat


def build_prefix_safety(
    prefixes: list[str],
    force_reannounce: bool = False,
    allow_large: bool = False,
    compare_previous: bool = True,
) -> dict[str, Any]:
    safety = {
        "ok": True,
        "warnings": [],
        "max_prefixes": MAX_PREFIXES,
        "min_expected_prefixes": MIN_EXPECTED_PREFIXES,
        "max_delta_percent": MAX_DELTA_PERCENT,
        "allow_large": allow_large,
        "compare_previous": compare_previous,
    }

    if len(prefixes) > MAX_PREFIXES:
        message = f"too many prefixes: {len(prefixes)} > MAX_PREFIXES={MAX_PREFIXES}"
        safety["warnings"].append(message)

        if not allow_large:
            safety["ok"] = False

    try:
        if safety["ok"] and compare_previous:
            existing_warnings = list(safety["warnings"])
            safety.update(validate_update_safety(prefixes, force_reannounce=force_reannounce))
            safety["warnings"] = existing_warnings + list(safety.get("warnings", []))
    except Exception as e:
        safety["ok"] = False
        safety["warnings"].append(str(e))

    return safety


def diff_prefixes_vs_current(prefixes: list[str]) -> dict[str, Any]:
    current_set = set(read_lines(ADVERTISED_FILE))
    target_set = set(prefixes)
    would_add = sort_prefixes(target_set - current_set)
    would_delete = sort_prefixes(current_set - target_set)

    return {
        "add_count": len(would_add),
        "delete_count": len(would_delete),
        "unchanged_count": len(target_set & current_set),
        "add_first_50": would_add[:50],
        "delete_first_50": would_delete[:50],
    }


def additive_diff_prefixes_vs_current(prefixes: list[str]) -> dict[str, Any]:
    current_set = set(read_lines(ADVERTISED_FILE))
    target_set = set(prefixes)
    would_add = sort_prefixes(target_set - current_set)

    return {
        "add_count": len(would_add),
        "delete_count": 0,
        "unchanged_count": len(target_set & current_set),
        "add_first_50": would_add[:50],
        "delete_first_50": [],
        "mode": "additive",
    }


def preview_source_by_name(source_name: str, allow_large: bool = False) -> dict[str, Any]:
    ensure_sources_file()

    sources = read_json(SOURCES_FILE, [])
    if not isinstance(sources, list):
        raise RuntimeError("sources.json must be a JSON array")

    selected_source = None
    for source in sources:
        if isinstance(source, dict) and str(source.get("name", "")).strip() == source_name:
            selected_source = source
            break

    if selected_source is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_name}")

    preview_source = dict(selected_source)
    original_enabled = bool(preview_source.get("enabled", False))
    preview_source["enabled"] = True

    source_prefixes, stat = collect_one_source(preview_source)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(source_prefixes)))
    else:
        final_networks = sorted(source_prefixes)

    prefixes = [str(net) for net in final_networks]

    return {
        "ok": stat.get("error") is None,
        "mode": "source_preview",
        "preview_relation": "additive",
        "would_apply": False,
        "source": {
            "name": source_name,
            "enabled": original_enabled,
            "type": selected_source.get("type"),
            "group": selected_source.get("group"),
            "strategy": selected_source.get("strategy"),
            "priority": selected_source.get("priority"),
            "description": selected_source.get("description"),
            "url": selected_source.get("url"),
        },
        "stat": stat,
        "summary": {
            "unique_before_aggregation": len(source_prefixes),
            "final_count": len(prefixes),
            "aggregate": AGGREGATE_PREFIXES,
            "first_20": prefixes[:20],
            "last_20": prefixes[-20:],
        },
        "diff_vs_current_advertised": additive_diff_prefixes_vs_current(prefixes),
        "safety": build_prefix_safety(
            prefixes,
            force_reannounce=False,
            allow_large=allow_large,
            compare_previous=False,
        ),
        "time": now_iso(),
    }


def collect_static_prefixes_from_sources(sources: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    collected: set[ipaddress.IPv4Network] = set()
    source_stats: list[dict[str, Any]] = []

    for source in sources:
        source_prefixes, stat = collect_one_source(source)
        collected.update(source_prefixes)
        if stat["accepted"] > 0 and stat["error"] is None:
            stat["selected"] = True
        source_stats.append(stat)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(collected)))
    else:
        final_networks = sorted(collected)

    prefixes = [str(net) for net in final_networks]

    meta = {
        "source_stats": source_stats,
        "group_stats": [],
        "unique_before_aggregation": len(collected),
        "final_count": len(prefixes),
        "aggregate": AGGREGATE_PREFIXES,
    }

    return prefixes, meta


def collect_static_prefixes() -> tuple[list[str], dict[str, Any]]:
    ensure_sources_file()

    sources = read_json(SOURCES_FILE, [])

    if not isinstance(sources, list):
        raise RuntimeError("sources.json must be a JSON array")

    return collect_static_prefixes_from_sources(sources)


def normalize_community(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.fullmatch(r"([0-9]{1,5}):([0-9]{1,5})", text)

    if not match:
        return None

    left = int(match.group(1))
    right = int(match.group(2))

    if left > 65535 or right > 65535:
        return None

    return f"{left}:{right}"


def normalize_large_community(value: Any) -> str | None:
    text = str(value or "").strip()
    standard = normalize_community(text)

    if standard:
        left, right = standard.split(":")
        return f"{left}:{right}:1"

    match = re.fullmatch(r"([0-9]{1,10}):([0-9]{1,10}):([0-9]{1,10})", text)

    if not match:
        return None

    parts = [int(match.group(index)) for index in range(1, 4)]

    if any(part > 4294967295 for part in parts):
        return None

    return ":".join(str(part) for part in parts)


def default_community_profiles() -> dict[str, Any]:
    return {
        "version": 1,
        "profiles": [
            {
                "id": "ai",
                "title": "AI",
                "description": "OpenAI, Anthropic и другие AI-сервисы.",
                "community": f"{LOCAL_AS}:510:1",
                "enabled": False,
                "sources": [],
                "services": ["openai", "anthropic"],
            },
            {
                "id": "video",
                "title": "Видео",
                "description": "Видео и медиа-сервисы.",
                "community": f"{LOCAL_AS}:520:1",
                "enabled": False,
                "sources": [],
                "services": ["youtube-googlevideo"],
            },
            {
                "id": "social",
                "title": "Соцсети",
                "description": "Социальные сети и мессенджеры.",
                "community": f"{LOCAL_AS}:530:1",
                "enabled": False,
                "sources": [],
                "services": ["x-twitter", "instagram", "telegram"],
            },
        ],
        "updated_at": now_iso(),
    }


def ensure_community_profiles_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not COMMUNITY_PROFILES_FILE.exists():
        write_json_atomic(COMMUNITY_PROFILES_FILE, default_community_profiles())


def validate_community_profiles_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="community profiles config must be an object")

    profiles = data.get("profiles", [])
    if not isinstance(profiles, list):
        raise HTTPException(status_code=400, detail="profiles must be a list")

    seen_ids: set[str] = set()
    seen_communities: set[str] = set()
    validated_profiles: list[dict[str, Any]] = []

    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            raise HTTPException(status_code=400, detail=f"profiles[{index}] must be an object")

        profile_id = str(profile.get("id", "")).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,48}", profile_id):
            raise HTTPException(status_code=400, detail=f"profiles[{index}].id is invalid")

        if profile_id in seen_ids:
            raise HTTPException(status_code=400, detail=f"duplicate community profile id: {profile_id}")
        seen_ids.add(profile_id)

        title = str(profile.get("title", "")).strip()
        if not title:
            raise HTTPException(status_code=400, detail=f"{profile_id}: title is required")

        community = normalize_large_community(profile.get("community"))
        if community is None:
            raise HTTPException(status_code=400, detail=f"{profile_id}: community must look like 64500:510:1")

        if community in seen_communities:
            raise HTTPException(status_code=400, detail=f"{profile_id}: duplicate community: {community}")
        seen_communities.add(community)

        enabled = profile.get("enabled", False)
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail=f"{profile_id}: enabled must be boolean")

        sources = profile.get("sources", [])
        services = profile.get("services", [])

        if not isinstance(sources, list) or not all(isinstance(item, str) for item in sources):
            raise HTTPException(status_code=400, detail=f"{profile_id}: sources must be a list of strings")

        if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
            raise HTTPException(status_code=400, detail=f"{profile_id}: services must be a list of strings")

        validated_profiles.append(
            {
                "id": profile_id,
                "title": title[:80],
                "description": str(profile.get("description", "")).strip()[:400],
                "community": community,
                "enabled": enabled,
                "sources": sorted({item.strip() for item in sources if item.strip()}),
                "services": sorted({item.strip() for item in services if item.strip()}),
            }
        )

    return {
        "version": int(data.get("version", 1) or 1),
        "profiles": validated_profiles,
        "updated_at": now_iso(),
    }


def read_community_profiles() -> dict[str, Any]:
    ensure_community_profiles_file()
    data = read_json(COMMUNITY_PROFILES_FILE, default_community_profiles())

    try:
        return validate_community_profiles_config(data)
    except HTTPException:
        return default_community_profiles()


def write_community_profiles(data: dict[str, Any]) -> None:
    validated = validate_community_profiles_config(data)
    write_json_atomic(COMMUNITY_PROFILES_FILE, validated)


def collect_prefixes_for_source_names(source_names: list[str]) -> tuple[set[ipaddress.IPv4Network], list[dict[str, Any]]]:
    if not source_names:
        return set(), []

    ensure_sources_file()
    wanted = set(source_names)
    sources = read_json(SOURCES_FILE, [])

    if not isinstance(sources, list):
        raise RuntimeError("sources.json must be a JSON array")

    collected: set[ipaddress.IPv4Network] = set()
    stats: list[dict[str, Any]] = []
    found: set[str] = set()

    for source in sources:
        if not isinstance(source, dict):
            continue

        name = str(source.get("name", "")).strip()
        if name not in wanted:
            continue

        found.add(name)
        source_prefixes, stat = collect_one_source(source)
        collected.update(source_prefixes)
        stat["selected"] = len(source_prefixes) > 0 and stat.get("error") is None
        stats.append(stat)

    for missing in sorted(wanted - found):
        stats.append(
            {
                "name": missing,
                "accepted": 0,
                "ignored": 0,
                "error": "source not found",
                "selected": False,
            }
        )

    return collected, stats


def collect_prefixes_for_service_ids(service_ids: list[str]) -> tuple[set[ipaddress.IPv4Network], list[dict[str, Any]]]:
    if not service_ids:
        return set(), []

    wanted = set(service_ids)
    catalog = read_service_catalog()
    collected: set[ipaddress.IPv4Network] = set()
    stats: list[dict[str, Any]] = []
    found: set[str] = set()

    for service in catalog:
        service_id = str(service.get("id", "")).strip()
        if service_id not in wanted:
            continue

        found.add(service_id)
        service_prefixes: set[ipaddress.IPv4Network] = set()
        provider_stats: list[dict[str, Any]] = []
        provider_errors: list[str] = []

        providers = service.get("providers", [])

        if not isinstance(providers, list):
            stats.append(
                {
                    "id": service_id,
                    "title": service.get("title", service_id),
                    "accepted": 0,
                    "error": "providers must be a list",
                    "selected": False,
                }
            )
            continue

        for provider in providers:
            if not isinstance(provider, dict):
                provider_errors.append("provider must be an object")
                continue

            provider_prefixes, provider_stat = collect_service_provider(provider)
            service_prefixes.update(provider_prefixes)
            provider_stats.append(provider_stat)

            if provider_stat.get("error"):
                provider_errors.append(f"{provider_stat.get('name')}: {provider_stat.get('error')}")

        collected.update(service_prefixes)
        stats.append(
            {
                "id": service_id,
                "title": service.get("title", service_id),
                "category": service.get("category"),
                "accepted": len(service_prefixes),
                "ignored": sum(int(item.get("ignored", 0)) for item in provider_stats),
                "providers": provider_stats,
                "error": "; ".join(provider_errors[:5]) if provider_errors else None,
                "selected": len(service_prefixes) > 0 and not provider_errors,
            }
        )

    for missing in sorted(wanted - found):
        stats.append(
            {
                "id": missing,
                "accepted": 0,
                "error": "service not found",
                "selected": False,
            }
        )

    return collected, stats


def collapse_networks_by_communities(
    network_communities: dict[ipaddress.IPv4Network, set[str]]
) -> tuple[list[str], dict[str, list[str]]]:
    grouped: dict[tuple[str, ...], set[ipaddress.IPv4Network]] = {}

    for network, communities in network_communities.items():
        key = tuple(sorted(communities))
        grouped.setdefault(key, set()).add(network)

    route_communities: dict[str, list[str]] = {}

    for key, networks in grouped.items():
        if AGGREGATE_PREFIXES:
            collapsed = ipaddress.collapse_addresses(sorted(networks))
        else:
            collapsed = sorted(networks)

        for network in collapsed:
            route_communities[str(network)] = list(key)

    prefixes = sort_prefixes(list(route_communities.keys()))
    return prefixes, route_communities


def build_community_route_plan(
    base_prefixes: list[str] | None = None,
    service_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    profiles_config = read_community_profiles()
    profiles = profiles_config.get("profiles", [])

    network_communities: dict[ipaddress.IPv4Network, set[str]] = {}

    for prefix in base_prefixes or []:
        network = parse_prefix(prefix)
        if network is not None:
            network_communities.setdefault(network, set())

    for prefix in service_prefixes or []:
        network = parse_prefix(prefix)
        if network is not None:
            network_communities.setdefault(network, set())

    profile_stats: list[dict[str, Any]] = []

    for profile in profiles:
        enabled = bool(profile.get("enabled", False))
        community = normalize_large_community(profile.get("community"))
        profile_networks: set[ipaddress.IPv4Network] = set()
        source_stats: list[dict[str, Any]] = []
        service_stats: list[dict[str, Any]] = []
        errors: list[str] = []

        if enabled and community:
            try:
                source_networks, source_stats = collect_prefixes_for_source_names(profile.get("sources", []))
                service_networks, service_stats = collect_prefixes_for_service_ids(profile.get("services", []))
                profile_networks.update(source_networks)
                profile_networks.update(service_networks)
            except Exception as exc:
                errors.append(str(exc))

            for network in profile_networks:
                network_communities.setdefault(network, set()).add(community)

        profile_stats.append(
            {
                "id": profile.get("id"),
                "title": profile.get("title"),
                "community": community,
                "enabled": enabled,
                "sources": profile.get("sources", []),
                "services": profile.get("services", []),
                "unique_before_aggregation": len(profile_networks),
                "source_stats": source_stats,
                "service_stats": service_stats,
                "errors": errors,
            }
        )

    prefixes, route_communities = collapse_networks_by_communities(network_communities)
    tagged_count = sum(1 for communities in route_communities.values() if communities)

    return {
        "ok": True,
        "mode": "community_route_plan",
        "default_community": BGP_COMMUNITY,
        "profiles": profile_stats,
        "profiles_count": len(profiles),
        "enabled_count": sum(1 for item in profile_stats if item.get("enabled")),
        "unique_before_aggregation": len(network_communities),
        "final_count": len(prefixes),
        "tagged_count": tagged_count,
        "aggregate": AGGREGATE_PREFIXES,
        "prefixes": prefixes,
        "route_communities": route_communities,
        "first_20": prefixes[:20],
        "last_20": prefixes[-20:],
        "time": now_iso(),
    }


def read_route_attributes() -> dict[str, list[str]]:
    data = read_json(ADVERTISED_ROUTE_ATTRIBUTES_FILE, {})
    if not isinstance(data, dict):
        return {}

    routes = data.get("routes", data)
    if not isinstance(routes, dict):
        return {}

    result: dict[str, list[str]] = {}

    for prefix, communities in routes.items():
        network = parse_prefix(str(prefix))
        if network is None:
            continue

        if isinstance(communities, list):
            normalized = sorted({item for item in (normalize_large_community(value) for value in communities) if item})
        else:
            normalized = []

        result[str(network)] = normalized

    return result


def write_route_attributes(route_communities: dict[str, list[str]]) -> None:
    normalized_routes: dict[str, list[str]] = {}

    for prefix, communities in route_communities.items():
        network = parse_prefix(prefix)
        if network is None:
            continue

        normalized_routes[str(network)] = sorted({item for item in (normalize_large_community(value) for value in communities) if item})

    write_json_atomic(
        ADVERTISED_ROUTE_ATTRIBUTES_FILE,
        {
            "version": 1,
            "default_community": BGP_COMMUNITY,
            "routes": normalized_routes,
            "updated_at": now_iso(),
        },
    )


def gobgp_add(prefix: str, extra_communities: list[str] | None = None) -> tuple[bool, str]:
    large_communities = sorted({item for item in (normalize_large_community(value) for value in (extra_communities or [])) if item})
    command = [
            "global",
            "rib",
            "add",
            "-a",
            "ipv4",
            prefix,
            "nexthop",
            BGP_NEXTHOP,
            "origin",
            "igp",
            "community",
            BGP_COMMUNITY,
        ]

    if large_communities:
        command.extend(["large-community", ",".join(large_communities)])

    result = run_cmd(
        gobgp_cli_args(command),
        timeout=20,
    )

    ok = result.returncode == 0
    msg = (result.stdout or result.stderr).strip()
    return ok, msg


def gobgp_del(prefix: str) -> tuple[bool, str]:
    result = run_cmd(
        gobgp_cli_args(["global", "rib", "del", "-a", "ipv4", prefix]),
        timeout=20,
    )

    ok = result.returncode == 0
    msg = (result.stdout or result.stderr).strip()
    return ok, msg


def gobgp_current_prefixes() -> set[str]:
    result = run_cmd(
        gobgp_cli_args(["global", "rib", "-a", "ipv4"]),
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "failed to read GoBGP RIB")

    prefixes: set[str] = set()

    for line in result.stdout.splitlines():
        if BGP_NEXTHOP not in line or BGP_COMMUNITY not in line:
            continue

        match = re.search(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}(?![0-9.])", line)
        if not match:
            continue

        network = parse_prefix(match.group(0))
        if network is not None:
            prefixes.add(str(network))

    return prefixes


def sort_prefixes(prefixes: set[str] | list[str]) -> list[str]:
    return sorted(prefixes, key=lambda item: ipaddress.ip_network(item))


def write_prefixes_file(path: Path, prefixes: list[str]) -> None:
    write_text_atomic(
        path,
        "\n".join(prefixes) + ("\n" if prefixes else ""),
    )


def apply_prefixes(
    prefixes: list[str],
    force_reannounce: bool = False,
    route_communities: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    if not gobgp_ready():
        raise RuntimeError("GoBGP is not ready")

    target = set(prefixes)
    file_current = set(read_lines(ADVERTISED_FILE))
    actual_current = gobgp_current_prefixes()
    current = actual_current
    normalized_route_communities: dict[str, list[str]] = {}

    for prefix in target:
        normalized_route_communities[prefix] = sorted(
            {
                item
                for item in (
                    normalize_large_community(value)
                    for value in (route_communities or {}).get(prefix, [])
                )
                if item
            }
        )

    previous_route_communities = read_route_attributes()
    changed_attributes = {
        prefix
        for prefix in target & current
        if previous_route_communities.get(prefix, []) != normalized_route_communities.get(prefix, [])
    }

    if force_reannounce:
        to_delete = sort_prefixes(current)
        to_add = sort_prefixes(target)
    else:
        to_delete = sort_prefixes((current - target) | changed_attributes)
        to_add = sort_prefixes((target - current) | changed_attributes)

    deleted = 0
    added = 0
    errors: list[str] = []

    for prefix in to_delete:
        ok, msg = gobgp_del(prefix)
        if ok:
            deleted += 1
        else:
            errors.append(f"DEL {prefix}: {msg}")

    for prefix in to_add:
        ok, msg = gobgp_add(prefix, normalized_route_communities.get(prefix, []))
        if ok:
            added += 1
        else:
            errors.append(f"ADD {prefix}: {msg}")

    if errors:
        raise RuntimeError("; ".join(errors[:10]))

    final_prefixes = sort_prefixes(target)
    write_prefixes_file(ADVERTISED_FILE, final_prefixes)
    write_route_attributes({prefix: normalized_route_communities.get(prefix, []) for prefix in final_prefixes})

    return {
        "deleted": deleted,
        "added": added,
        "unchanged": len(target & current),
        "attribute_reannounced": len(changed_attributes),
        "force_reannounce": force_reannounce,
        "advertised_count": len(final_prefixes),
        "actual_current_count": len(actual_current),
        "file_current_count": len(file_current),
        "community": BGP_COMMUNITY,
        "profile_communities_count": sum(1 for communities in normalized_route_communities.values() if communities),
        "nexthop": BGP_NEXTHOP,
    }


def apply_last_good(force_reannounce: bool = False) -> dict[str, Any]:
    prefixes = read_lines(LAST_GOOD_FILE)

    if len(prefixes) < MIN_PREFIXES_TO_APPLY:
        raise RuntimeError(
            f"последний удачный набор слишком мал: {len(prefixes)} < MIN_PREFIXES_TO_APPLY={MIN_PREFIXES_TO_APPLY}"
        )

    apply_result = apply_prefixes(prefixes, force_reannounce=force_reannounce)
    write_prefixes_file(LAST_GOOD_FILE, prefixes)

    result = {
        "ok": True,
        "mode": "apply_last_good",
        "prefix_summary": summarize_prefixes(prefixes),
        "apply": apply_result,
        "time": now_iso(),
    }

    save_status(result)
    append_update_history(result, "apply_last_good")
    return result


def summarize_prefixes(prefixes: list[str]) -> dict[str, Any]:
    return {
        "count": len(prefixes),
        "first_20": prefixes[:20],
        "last_20": prefixes[-20:],
    }


def extract_selected_source(result: dict[str, Any]) -> str | None:
    try:
        group_stats = result.get("meta", {}).get("group_stats", [])
        for group in group_stats:
            selected = group.get("selected_source")
            if selected:
                return str(selected)
    except Exception:
        pass
    return None


def compact_history_record(result: dict[str, Any], trigger: str) -> dict[str, Any]:
    apply_data = result.get("apply", {}) if isinstance(result.get("apply"), dict) else {}
    prefix_summary = result.get("prefix_summary", {}) if isinstance(result.get("prefix_summary"), dict) else {}
    meta = result.get("meta", {}) if isinstance(result.get("meta"), dict) else {}

    return {
        "time": result.get("updated_at") or result.get("time") or now_iso(),
        "trigger": trigger,
        "ok": bool(result.get("ok", False)),
        "mode": result.get("mode"),
        "selected_source": extract_selected_source(result),
        "final_count": meta.get("final_count") or prefix_summary.get("count"),
        "advertised_count": apply_data.get("advertised_count"),
        "added": apply_data.get("added"),
        "deleted": apply_data.get("deleted"),
        "unchanged": apply_data.get("unchanged"),
        "force_reannounce": apply_data.get("force_reannounce"),
        "duration_seconds": result.get("duration_seconds"),
        "error": result.get("error"),
    }


def append_update_history(result: dict[str, Any], trigger: str) -> None:
    try:
        UPDATE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        record = compact_history_record(result, trigger)

        with UPDATE_HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

        trim_update_history()
    except Exception:
        # History must never break route serving.
        pass


def trim_update_history() -> None:
    if UPDATE_HISTORY_MAX_LINES <= 0:
        return

    if not UPDATE_HISTORY_FILE.exists():
        return

    lines = UPDATE_HISTORY_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()

    if len(lines) <= UPDATE_HISTORY_MAX_LINES:
        return

    UPDATE_HISTORY_FILE.write_text(
        "\n".join(lines[-UPDATE_HISTORY_MAX_LINES:]) + "\n",
        encoding="utf-8",
    )


def read_update_history(limit: int = 100) -> list[dict[str, Any]]:
    if not UPDATE_HISTORY_FILE.exists():
        return []

    lines = UPDATE_HISTORY_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()

    records: list[dict[str, Any]] = []

    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except Exception:
            records.append(
                {
                    "ok": False,
                    "mode": "history_parse_failed",
                    "raw": line,
                }
            )

    return records


def active_job_statuses() -> set[str]:
    return {"queued", "running", "cancel_requested"}


def read_jobs_state() -> dict[str, Any]:
    state = read_json(JOBS_FILE, None)

    if not isinstance(state, dict):
        state = {"version": 1, "jobs": []}

    jobs = state.get("jobs", [])
    if not isinstance(jobs, list):
        jobs = []

    state["version"] = 1
    state["jobs"] = [job for job in jobs if isinstance(job, dict)]
    return state


def write_jobs_state(state: dict[str, Any]) -> None:
    jobs = state.get("jobs", [])
    if not isinstance(jobs, list):
        jobs = []

    jobs = jobs[-JOB_HISTORY_MAX_ITEMS:] if JOB_HISTORY_MAX_ITEMS > 0 else jobs
    state["version"] = 1
    state["jobs"] = jobs
    state["updated_at"] = now_iso()
    write_json_atomic(JOBS_FILE, state)


def public_job_record(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "kind": job.get("kind"),
        "key": job.get("key"),
        "title": job.get("title"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "progress_percent": job.get("progress_percent"),
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "duration_seconds": job.get("duration_seconds"),
        "payload": job.get("payload"),
        "result_summary": job.get("result_summary"),
        "error": job.get("error"),
        "cancel_requested": bool(job.get("cancel_requested", False)),
    }


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with JOBS_LOCK:
        state = read_jobs_state()
        jobs = state.get("jobs", [])
        selected = list(reversed(jobs))[: max(1, min(limit, 200))]
        return [public_job_record(job) for job in selected]


def find_job(job_id: str) -> dict[str, Any] | None:
    state = read_jobs_state()
    for job in state.get("jobs", []):
        if str(job.get("id")) == job_id:
            return job
    return None


def find_active_job_by_key(key: str) -> dict[str, Any] | None:
    state = read_jobs_state()
    for job in reversed(state.get("jobs", [])):
        if str(job.get("key")) == key and str(job.get("status")) in active_job_statuses():
            return job
    return None


def update_job_record(job_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    with JOBS_LOCK:
        state = read_jobs_state()
        for job in state.get("jobs", []):
            if str(job.get("id")) == job_id:
                job.update(updates)
                write_jobs_state(state)
                return public_job_record(job)

    raise RuntimeError(f"job not found: {job_id}")


def create_job(kind: str, key: str, title: str, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
    with JOBS_LOCK:
        existing = find_active_job_by_key(key)
        if existing:
            return public_job_record(existing), True

        job = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "key": key,
            "title": title,
            "status": "queued",
            "stage": "В очереди",
            "progress_percent": 0,
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "payload": payload or {},
            "result_summary": None,
            "error": None,
            "cancel_requested": False,
        }

        state = read_jobs_state()
        state.setdefault("jobs", []).append(job)
        write_jobs_state(state)
        return public_job_record(job), False


def summarize_job_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
    if kind == "product_update":
        return {
            "ok": bool(result.get("ok", False)),
            "version": result.get("version"),
            "channel": result.get("channel"),
            "returncode": result.get("returncode"),
            "duration_seconds": result.get("duration_seconds"),
            "time": result.get("time"),
        }

    if kind == "service_source_refresh":
        return {
            "ok": bool(result.get("ok", False)),
            "services_checked": result.get("services_checked"),
            "providers_checked": result.get("providers_checked"),
            "accepted": result.get("accepted"),
            "ignored": result.get("ignored"),
            "errors_count": result.get("errors_count"),
            "warnings_count": result.get("warnings_count"),
            "total_bytes": result.get("total_bytes"),
            "source_versions_count": result.get("source_versions_count"),
            "time": result.get("time"),
        }

    if kind == "service_candidates_refresh":
        return {
            "ok": bool(result.get("ok", False)),
            "total_count": result.get("total_count"),
            "importable_count": result.get("importable_count"),
            "existing_count": result.get("existing_count"),
            "duration_seconds": result.get("duration_seconds"),
            "time": result.get("time"),
        }

    if kind == "route_update":
        apply_data = result.get("apply", {}) if isinstance(result.get("apply"), dict) else {}
        prefix_summary = result.get("prefix_summary", {}) if isinstance(result.get("prefix_summary"), dict) else {}
        meta = result.get("meta", {}) if isinstance(result.get("meta"), dict) else {}
        return {
            "ok": bool(result.get("ok", False)),
            "final_count": meta.get("final_count_with_services") or prefix_summary.get("count"),
            "advertised_count": apply_data.get("advertised_count"),
            "added": apply_data.get("added"),
            "deleted": apply_data.get("deleted"),
            "unchanged": apply_data.get("unchanged"),
            "duration_seconds": result.get("duration_seconds"),
            "time": result.get("time"),
        }

    return {"ok": bool(result.get("ok", False)), "time": result.get("time")}


async def run_background_job(job_id: str, kind: str, target: Any) -> None:
    started = time.time()
    update_job_record(
        job_id,
        {
            "status": "running",
            "stage": "Выполняется",
            "progress_percent": 10,
            "started_at": now_iso(),
        },
    )

    try:
        result = await asyncio.to_thread(target)
        update_job_record(
            job_id,
            {
                "status": "succeeded",
                "stage": "Готово",
                "progress_percent": 100,
                "finished_at": now_iso(),
                "duration_seconds": round(time.time() - started, 3),
                "result_summary": summarize_job_result(kind, result),
                "error": None,
            },
        )
    except Exception as e:
        update_job_record(
            job_id,
            {
                "status": "failed",
                "stage": "Ошибка",
                "progress_percent": 100,
                "finished_at": now_iso(),
                "duration_seconds": round(time.time() - started, 3),
                "error": str(e),
            },
        )


def start_background_job(kind: str, key: str, title: str, target: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    job, deduplicated = create_job(kind=kind, key=key, title=title, payload=payload)

    if not deduplicated:
        asyncio.create_task(run_background_job(str(job["id"]), kind, target))

    return {
        "ok": True,
        "job": job,
        "deduplicated": deduplicated,
        "time": now_iso(),
    }


def run_product_update_command(channel: str, version: str | None = None) -> dict[str, Any]:
    if not PRODUCT_UPDATE_ENABLED:
        raise RuntimeError("Product update from portal is disabled")

    if not PRODUCT_UPDATE_COMMAND:
        raise RuntimeError("PRODUCT_UPDATE_COMMAND is empty")

    args = shlex.split(PRODUCT_UPDATE_COMMAND)
    if not args:
        raise RuntimeError("PRODUCT_UPDATE_COMMAND is invalid")

    args.extend(["--channel", channel])
    if version:
        args.extend(["--version", version])

    started = time.time()
    result = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=PRODUCT_UPDATE_TIMEOUT_SECONDS,
        check=False,
    )

    return {
        "ok": result.returncode == 0,
        "version": version,
        "channel": channel,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "duration_seconds": round(time.time() - started, 3),
        "time": now_iso(),
    }


@app.get("/api/product/version")
async def api_product_version(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "product": APP_NAME,
            "version": read_product_version(),
            "channel": PRODUCT_CHANNEL,
            "manifest_url": PRODUCT_UPDATE_MANIFEST_URL or None,
            "update_enabled": PRODUCT_UPDATE_ENABLED,
            "time": now_iso(),
        }
    )


@app.get("/api/product/updates")
async def api_product_updates(_: str = Depends(require_auth)) -> JSONResponse:
    manifest = await load_update_manifest()
    manifest["current_version"] = read_product_version()
    manifest["current_channel"] = PRODUCT_CHANNEL
    manifest["update_enabled"] = PRODUCT_UPDATE_ENABLED
    manifest["time"] = now_iso()
    return JSONResponse(manifest, status_code=200 if manifest.get("ok", True) else 502)


@app.post("/api/product/update/job")
async def api_product_update_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    if not PRODUCT_UPDATE_ENABLED:
        raise HTTPException(
            status_code=409,
            detail="Обновление из портала отключено. Включи PRODUCT_UPDATE_ENABLED=true и настрой PRODUCT_UPDATE_COMMAND на host-updater.",
        )

    body = await request.json()
    channel = str(body.get("channel", PRODUCT_CHANNEL) or PRODUCT_CHANNEL).strip()
    version = str(body.get("version", "") or "").strip() or None

    if channel not in {"stable", "beta"}:
        raise HTTPException(status_code=400, detail="channel must be stable or beta")

    return JSONResponse(
        start_background_job(
            kind="product_update",
            key="product_update",
            title="Обновление The333-BGP",
            target=lambda: run_product_update_command(channel=channel, version=version),
            payload={"channel": channel, "version": version},
        )
    )


@app.get("/api/jobs")
async def api_jobs(limit: int = 50, _: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "jobs": list_jobs(limit),
            "time": now_iso(),
        }
    )


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str, _: str = Depends(require_auth)) -> JSONResponse:
    with JOBS_LOCK:
        job = find_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")

    return JSONResponse({"ok": True, "job": public_job_record(job), "time": now_iso()})


@app.post("/api/jobs/{job_id}/cancel")
async def api_job_cancel(job_id: str, _: str = Depends(require_auth)) -> JSONResponse:
    with JOBS_LOCK:
        state = read_jobs_state()
        for job in state.get("jobs", []):
            if str(job.get("id")) == job_id:
                if str(job.get("status")) not in active_job_statuses():
                    return JSONResponse({"ok": True, "job": public_job_record(job), "time": now_iso()})

                job["cancel_requested"] = True
                job["status"] = "cancel_requested"
                job["stage"] = "Запрошена остановка"
                write_jobs_state(state)
                return JSONResponse({"ok": True, "job": public_job_record(job), "time": now_iso()})

    raise HTTPException(status_code=404, detail=f"job not found: {job_id}")


def validate_update_safety(prefixes: list[str], force_reannounce: bool = False) -> dict[str, Any]:
    new_count = len(prefixes)
    previous_count = len(read_lines(ADVERTISED_FILE))

    checks = {
        "new_count": new_count,
        "previous_count": previous_count,
        "min_expected_prefixes": MIN_EXPECTED_PREFIXES,
        "max_delta_percent": MAX_DELTA_PERCENT,
        "force_reannounce": force_reannounce,
        "ok": True,
        "warnings": [],
    }

    if MIN_EXPECTED_PREFIXES > 0 and new_count < MIN_EXPECTED_PREFIXES:
        checks["ok"] = False
        checks["warnings"].append(
            f"new prefix count {new_count} is below MIN_EXPECTED_PREFIXES={MIN_EXPECTED_PREFIXES}"
        )

    if (
        MAX_DELTA_PERCENT > 0
        and previous_count > 0
        and new_count < previous_count
    ):
        drop_percent = round(((previous_count - new_count) / previous_count) * 100, 3)
        checks["drop_percent"] = drop_percent

        if drop_percent > MAX_DELTA_PERCENT:
            checks["ok"] = False
            checks["warnings"].append(
                f"prefix count drop {drop_percent}% exceeds MAX_DELTA_PERCENT={MAX_DELTA_PERCENT}%"
            )

    if not checks["ok"]:
        raise RuntimeError("; ".join(checks["warnings"]))

    return checks


def update_now(force_reannounce: bool = False, trigger: str = "manual", allow_large: bool = False) -> dict[str, Any]:
    started = time.time()

    prefixes, meta = collect_static_prefixes()

    service_prefixes, service_meta = collect_service_prefixes_for_update()
    community_plan = build_community_route_plan(prefixes, service_prefixes)
    prefixes = community_plan["prefixes"]

    meta["service_routes"] = service_meta
    meta["final_count_with_services"] = len(prefixes)
    meta["community_routes"] = {
        "profiles_count": community_plan.get("profiles_count"),
        "enabled_count": community_plan.get("enabled_count"),
        "tagged_count": community_plan.get("tagged_count"),
        "final_count": community_plan.get("final_count"),
        "profiles": community_plan.get("profiles", []),
    }

    if len(prefixes) < MIN_PREFIXES_TO_APPLY:
        raise RuntimeError(
            f"too few prefixes: {len(prefixes)} < MIN_PREFIXES_TO_APPLY={MIN_PREFIXES_TO_APPLY}"
        )

    if len(prefixes) > MAX_PREFIXES and not allow_large:
        raise RuntimeError(
            f"too many prefixes: {len(prefixes)} > MAX_PREFIXES={MAX_PREFIXES}"
        )

    safety = validate_update_safety(prefixes, force_reannounce=force_reannounce)
    safety["allow_large"] = allow_large
    safety["max_prefixes"] = MAX_PREFIXES

    if len(prefixes) > MAX_PREFIXES:
        safety.setdefault("warnings", [])
        safety["warnings"].append(f"large update allowed: {len(prefixes)} > MAX_PREFIXES={MAX_PREFIXES}")

    apply_result = apply_prefixes(
        prefixes,
        force_reannounce=force_reannounce,
        route_communities=community_plan.get("route_communities", {}),
    )
    write_prefixes_file(LAST_GOOD_FILE, prefixes)

    result = {
        "ok": True,
        "mode": "mvp_static_update",
        "duration_seconds": round(time.time() - started, 3),
        "prefix_summary": summarize_prefixes(prefixes),
        "meta": {
            **meta,
            "safety": safety,
        },
        "apply": apply_result,
        "time": now_iso(),
    }

    save_status(result)
    append_update_history(result, trigger)
    return result


async def auto_update_loop() -> None:
    while True:
        await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

        try:
            try:
                await asyncio.to_thread(refresh_service_candidates_if_due)
            except Exception:
                pass

            try:
                await asyncio.to_thread(refresh_auto_service_sources)
            except Exception:
                pass

            result = await asyncio.to_thread(update_now, False, "auto")
            save_status(
                {
                    **result,
                    "auto_update": True,
                    "auto_update_ok": True,
                    "auto_update_interval_seconds": UPDATE_INTERVAL_SECONDS,
                }
            )
        except Exception as e:
            current_status = read_json(STATUS_FILE, {})
            current_status.update(
                {
                    "ok": False,
                    "mode": "auto_update_failed",
                    "auto_update": True,
                    "auto_update_ok": False,
                    "auto_update_interval_seconds": UPDATE_INTERVAL_SECONDS,
                    "error": str(e),
                    "time": now_iso(),
                }
            )
            save_status(current_status)
            append_update_history(current_status, "auto_failed")


async def startup_update_once() -> None:
    try:
        result = await asyncio.to_thread(apply_last_good, False)
        save_status(
            {
                **result,
                "startup_restore": True,
                "startup_restore_ok": True,
            }
        )
    except Exception as e:
        save_status(
            {
                "ok": False,
                "mode": "startup_restore_failed",
                "startup_restore": True,
                "startup_restore_ok": False,
                "error": str(e),
                "time": now_iso(),
            }
        )


@app.on_event("startup")
async def startup() -> None:
    ensure_sources_file()

    for _ in range(30):
        if gobgp_ready():
            break
        time.sleep(1)

    asyncio.create_task(startup_update_once())

    if AUTO_UPDATE:
        asyncio.create_task(auto_update_loop())


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "app": APP_NAME,
        "gobgp_ready": gobgp_ready(),
        "time": now_iso(),
    }


def validate_sources_config(sources: Any) -> list[dict[str, Any]]:
    if not isinstance(sources, list):
        raise HTTPException(status_code=400, detail="sources must be a JSON array")

    names: set[str] = set()
    validated: list[dict[str, Any]] = []

    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise HTTPException(status_code=400, detail=f"source[{index}] must be an object")

        name = str(source.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail=f"source[{index}].name must not be empty")

        if name in names:
            raise HTTPException(status_code=400, detail=f"duplicate source name: {name}")
        names.add(name)

        source_type = str(source.get("type", "")).strip().lower()
        if source_type not in {"static", "url"}:
            raise HTTPException(status_code=400, detail=f"{name}: unsupported type: {source_type}")

        if "enabled" not in source or not isinstance(source.get("enabled"), bool):
            raise HTTPException(status_code=400, detail=f"{name}: enabled must be boolean")

        if source_type == "static":
            prefixes = source.get("prefixes", [])
            if not isinstance(prefixes, list):
                raise HTTPException(status_code=400, detail=f"{name}: prefixes must be a list")

            for item in prefixes:
                if parse_prefix(str(item)) is None:
                    raise HTTPException(status_code=400, detail=f"{name}: invalid prefix: {item}")

            manual_entries = source.get("manual_entries", [])
            if not isinstance(manual_entries, list):
                raise HTTPException(status_code=400, detail=f"{name}: manual_entries must be a list")

            for item in manual_entries:
                if normalize_manual_entry(str(item)) is None:
                    raise HTTPException(status_code=400, detail=f"{name}: invalid manual entry: {item}")

        if source_type == "url":
            url = str(source.get("url", "")).strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                raise HTTPException(status_code=400, detail=f"{name}: url must start with http:// or https://")

        if source.get("strategy") is not None:
            strategy = str(source.get("strategy", "")).lower()
            if strategy and strategy != "first_success":
                raise HTTPException(status_code=400, detail=f"{name}: unsupported strategy: {strategy}")

        if source.get("priority") is not None:
            try:
                int(source.get("priority"))
            except Exception:
                raise HTTPException(status_code=400, detail=f"{name}: priority must be integer")

        validated.append(source)

    return validated


def backup_sources_file() -> Path | None:
    if not SOURCES_FILE.exists():
        return None

    SOURCES_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = SOURCES_BACKUP_DIR / f"sources-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    backup_path.write_text(SOURCES_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def safe_file_info(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "exists": True,
        }
    except FileNotFoundError:
        return {
            "name": path.name,
            "path": str(path),
            "exists": False,
        }


def gobgp_rib_count() -> int:
    result = run_cmd(gobgp_cli_args(["global", "rib", "-a", "ipv4"]), timeout=60)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gobgp rib failed")

    count = 0
    for line in result.stdout.splitlines():
        if line.startswith("*>"):
            count += 1

    return count


@app.get("/api/update-history")
async def api_update_history(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "history": read_update_history(100),
            "count": len(read_update_history(100)),
            "file": str(UPDATE_HISTORY_FILE),
            "time": now_iso(),
        }
    )


@app.get("/api/diagnostics")
async def api_diagnostics(_: str = Depends(require_auth)) -> JSONResponse:
    advertised_routes = read_lines(ADVERTISED_FILE)
    last_good_routes = read_lines(LAST_GOOD_FILE)
    sources = read_json(SOURCES_FILE, [])
    status_data = read_json(STATUS_FILE, {})

    diagnostics: dict[str, Any] = {
        "ok": True,
        "app": APP_NAME,
        "time": now_iso(),
        "gobgp_ready": gobgp_ready(),
        "gobgp_rib_count": None,
        "gobgp_global": None,
        "gobgp_neighbor": None,
        "advertised_routes_summary": summarize_prefixes(advertised_routes),
        "last_good_routes_summary": summarize_prefixes(last_good_routes),
        "sources_count": len(sources) if isinstance(sources, list) else None,
        "last_status": status_data,
        "safe_env": {
            "AUTO_UPDATE": AUTO_UPDATE,
            "UPDATE_INTERVAL_SECONDS": UPDATE_INTERVAL_SECONDS,
            "JOB_HISTORY_MAX_ITEMS": JOB_HISTORY_MAX_ITEMS,
            "MAX_PREFIXES": MAX_PREFIXES,
            "MIN_PREFIXES_TO_APPLY": MIN_PREFIXES_TO_APPLY,
            "MIN_EXPECTED_PREFIXES": MIN_EXPECTED_PREFIXES,
            "MAX_DELTA_PERCENT": MAX_DELTA_PERCENT,
            "UPDATE_HISTORY_MAX_LINES": UPDATE_HISTORY_MAX_LINES,
            "AGGREGATE_PREFIXES": AGGREGATE_PREFIXES,
            "BGP_COMMUNITY": BGP_COMMUNITY,
            "BGP_NEXTHOP": BGP_NEXTHOP,
            "LOCAL_AS": LOCAL_AS,
            "PEER_AS": PEER_AS,
            "PEER_ADDRESS": PEER_ADDRESS,
            "ROUTER_ID": ROUTER_ID,
            "SERVICE_ROUTES_ENABLED": SERVICE_ROUTES_ENABLED,
            "SERVICE_DNS_CACHE_GRACE_SECONDS": SERVICE_DNS_CACHE_GRACE_SECONDS,
            "SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER": SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER,
            "SERVICE_GEOSITE_HTTP_TIMEOUT_SECONDS": SERVICE_GEOSITE_HTTP_TIMEOUT_SECONDS,
            "SERVICE_GEOSITE_INCLUDE_MAX_DEPTH": SERVICE_GEOSITE_INCLUDE_MAX_DEPTH,
            "SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER": SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER,
            "SERVICE_DNS_RESOLVE_DELAY_SECONDS": SERVICE_DNS_RESOLVE_DELAY_SECONDS,
            "SERVICE_DNS_RESOLVE_RETRIES": SERVICE_DNS_RESOLVE_RETRIES,
            "SERVICE_DNS_RESOLVE_RETRY_DELAY_SECONDS": SERVICE_DNS_RESOLVE_RETRY_DELAY_SECONDS,
            "SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER": SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER,
            "SERVICE_GEOIP_HTTP_TIMEOUT_SECONDS": SERVICE_GEOIP_HTTP_TIMEOUT_SECONDS,
        },
        "files": [
            safe_file_info(SOURCES_FILE),
            safe_file_info(ADVERTISED_FILE),
            safe_file_info(LAST_GOOD_FILE),
            safe_file_info(STATUS_FILE),
            safe_file_info(UPDATE_HISTORY_FILE),
            safe_file_info(JOBS_FILE),
            safe_file_info(SERVICE_CACHE_FILE),
            safe_file_info(SERVICE_DNS_CACHE_FILE),
            safe_file_info(SERVICE_ROUTES_FILE),
            safe_file_info(SERVICE_LAST_GOOD_ROUTES_FILE),
            safe_file_info(SERVICE_SOURCE_REFRESH_FILE),
        ],
    }

    try:
        diagnostics["gobgp_rib_count"] = gobgp_rib_count()
    except Exception as e:
        diagnostics["ok"] = False
        diagnostics["gobgp_rib_count_error"] = str(e)

    diagnostics["gobgp_global"] = gobgp_text(["gobgp", "global"])
    diagnostics["gobgp_neighbor"] = gobgp_text(["gobgp", "neighbor"])

    return JSONResponse(diagnostics)


@app.get("/ready")
async def ready(_: str = Depends(require_auth)) -> JSONResponse:
    advertised_routes = read_lines(ADVERTISED_FILE)
    last_good_routes = read_lines(LAST_GOOD_FILE)
    status_data = read_json(STATUS_FILE, {})

    advertised_count = len(advertised_routes)
    last_good_count = len(last_good_routes)
    status_ok = bool(status_data.get("ok", False))
    gobgp_api_ready = gobgp_ready()

    rib_count = None
    errors: list[str] = []

    if not gobgp_api_ready:
        errors.append("gobgp API is not ready")

    if advertised_count < MIN_PREFIXES_TO_APPLY:
        errors.append(
            f"advertised routes too small: {advertised_count} < MIN_PREFIXES_TO_APPLY={MIN_PREFIXES_TO_APPLY}"
        )

    if not status_ok:
        errors.append("last status is not ok")

    try:
        if gobgp_api_ready:
            rib_count = gobgp_rib_count()
            if rib_count != advertised_count:
                errors.append(f"rib_count mismatch: rib={rib_count}, advertised={advertised_count}")
    except Exception as e:
        errors.append(f"failed to read GoBGP RIB count: {e}")

    is_ready = len(errors) == 0

    payload = {
        "ready": is_ready,
        "app": APP_NAME,
        "gobgp_ready": gobgp_api_ready,
        "rib_count": rib_count,
        "advertised_count": advertised_count,
        "last_good_count": last_good_count,
        "status_ok": status_ok,
        "errors": errors,
        "time": now_iso(),
    }

    return JSONResponse(payload, status_code=200 if is_ready else 503)


def list_source_backups() -> list[dict[str, Any]]:
    if not SOURCES_BACKUP_DIR.exists():
        return []

    backups: list[dict[str, Any]] = []

    for item in sorted(SOURCES_BACKUP_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        info = safe_file_info(item)
        backups.append(
            {
                "name": item.name,
                "size": info.get("size"),
                "mtime": info.get("mtime"),
            }
        )

    return backups


def safe_backup_path(name: str) -> Path:
    clean_name = Path(name).name

    if clean_name != name:
        raise HTTPException(status_code=400, detail="backup name must not contain path separators")

    if not clean_name.endswith(".json"):
        raise HTTPException(status_code=400, detail="backup must be a .json file")

    backup_path = SOURCES_BACKUP_DIR / clean_name

    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"backup not found: {clean_name}")

    return backup_path


@app.get("/api/sources/preview/{source_name}")
async def api_sources_preview(source_name: str, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(preview_source_by_name, source_name))
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "source_preview_failed",
                "would_apply": False,
                "source": {"name": source_name},
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.post("/api/sources/preview-selection")
async def api_sources_preview_selection(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "mode": "sources_selection_removed",
            "message": "source sets were removed; enable or disable individual sources instead",
            "time": now_iso(),
        },
        status_code=410,
    )


@app.post("/api/sources/set-selection")
async def api_sources_set_selection(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": False,
            "mode": "sources_selection_removed",
            "message": "source sets were removed; use /api/sources/set-enabled-update",
            "time": now_iso(),
        },
        status_code=410,
    )


@app.get("/api/sources/manual/{source_name}")
async def api_sources_get_manual(source_name: str, _: str = Depends(require_auth)) -> JSONResponse:
    ensure_sources_file()
    sources = read_json(SOURCES_FILE, [])

    if not isinstance(sources, list):
        raise HTTPException(status_code=500, detail="sources.json must be a JSON array")

    for source in sources:
        if str(source.get("name", "")).strip() != source_name:
            continue

        if str(source.get("type", "")).lower() != "static":
            raise HTTPException(status_code=400, detail=f"{source_name}: source is not static")

        return JSONResponse(
            {
                "ok": True,
                "name": source_name,
                "enabled": bool(source.get("enabled", False)),
                "prefixes": source.get("prefixes", []),
                "manual_entries": source.get("manual_entries", []),
                "time": now_iso(),
            }
        )

    raise HTTPException(status_code=404, detail=f"source not found: {source_name}")


@app.put("/api/sources/manual/{source_name}")
async def api_sources_put_manual(source_name: str, request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    manual_entries = payload.get("manual_entries", [])

    if not isinstance(manual_entries, list):
        raise HTTPException(status_code=400, detail="manual_entries must be a list")

    normalized_entries = [str(item).strip() for item in manual_entries if str(item).strip()]

    ensure_sources_file()
    sources = read_json(SOURCES_FILE, [])

    if not isinstance(sources, list):
        raise HTTPException(status_code=500, detail="sources.json must be a JSON array")

    found = False

    for source in sources:
        if str(source.get("name", "")).strip() != source_name:
            continue

        if str(source.get("type", "")).lower() != "static":
            raise HTTPException(status_code=400, detail=f"{source_name}: source is not static")

        source["manual_entries"] = normalized_entries
        found = True
        break

    if not found:
        raise HTTPException(status_code=404, detail=f"source not found: {source_name}")

    validated = validate_sources_config(sources)
    backup_path = backup_sources_file()
    write_json_atomic(SOURCES_FILE, validated)

    return JSONResponse(
        {
            "ok": True,
            "message": "manual entries saved; routes were not updated automatically",
            "name": source_name,
            "manual_entries_count": len(normalized_entries),
            "backup": str(backup_path) if backup_path else None,
            "time": now_iso(),
        }
    )


@app.post("/api/sources/set-enabled")
async def api_sources_set_enabled(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()

    name = str(payload.get("name", "")).strip()
    enabled = payload.get("enabled")

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    ensure_sources_file()

    sources = read_json(SOURCES_FILE, [])

    if not isinstance(sources, list):
        raise HTTPException(status_code=500, detail="sources.json must be a JSON array")

    found = False

    for source in sources:
        if source.get("name") == name:
            source["enabled"] = enabled
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"source not found: {name}")

    validated = validate_sources_config(sources)
    backup_path = backup_sources_file()
    write_json_atomic(SOURCES_FILE, validated)

    return JSONResponse(
        {
            "ok": True,
            "message": "source enabled flag saved; routes were not updated automatically",
            "name": name,
            "enabled": enabled,
            "backup": str(backup_path) if backup_path else None,
            "time": now_iso(),
        }
    )


@app.post("/api/sources/set-enabled-update")
async def api_sources_set_enabled_update(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()

    name = str(payload.get("name", "")).strip()
    enabled = payload.get("enabled")
    allow_large = bool(payload.get("allow_large", False))

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    ensure_sources_file()

    previous_sources = read_json(SOURCES_FILE, [])

    if not isinstance(previous_sources, list):
        raise HTTPException(status_code=500, detail="sources.json must be a JSON array")

    next_sources = json.loads(json.dumps(previous_sources, ensure_ascii=False))
    found = False

    for source in next_sources:
        if source.get("name") == name:
            source["enabled"] = enabled
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"source not found: {name}")

    validated = validate_sources_config(next_sources)
    backup_path = backup_sources_file()
    write_json_atomic(SOURCES_FILE, validated)

    try:
        update_result = update_now(False, "source_toggle", allow_large)
    except Exception as e:
        write_json_atomic(SOURCES_FILE, previous_sources)

        result = {
            "ok": False,
            "mode": "source_toggle_update_failed",
            "name": name,
            "enabled_requested": enabled,
            "rolled_back": True,
            "error": str(e),
            "backup": str(backup_path) if backup_path else None,
            "time": now_iso(),
        }
        save_status(result)
        append_update_history(result, "source_toggle_failed")
        return JSONResponse(result, status_code=409)

    return JSONResponse(
        {
            "ok": True,
            "message": "source enabled flag saved and routes were updated",
            "name": name,
            "enabled": enabled,
            "backup": str(backup_path) if backup_path else None,
            "update": update_result,
            "time": now_iso(),
        }
    )


@app.get("/api/sources/backups")
async def api_sources_backups(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "backups": list_source_backups(),
            "time": now_iso(),
        }
    )


@app.post("/api/sources/restore")
async def api_sources_restore(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    backup_name = str(payload.get("backup", "")).strip()

    if not backup_name:
        raise HTTPException(status_code=400, detail="backup is required")

    backup_path = safe_backup_path(backup_name)
    restored_sources = validate_sources_config(
        json.loads(backup_path.read_text(encoding="utf-8"))
    )

    current_backup = backup_sources_file()
    write_json_atomic(SOURCES_FILE, restored_sources)

    return JSONResponse(
        {
            "ok": True,
            "message": "sources.json restored; routes were not updated automatically",
            "restored_from": backup_path.name,
            "current_backup": str(current_backup) if current_backup else None,
            "sources_count": len(restored_sources),
            "time": now_iso(),
        }
    )


@app.get("/api/sources")
async def api_get_sources(_: str = Depends(require_auth)) -> JSONResponse:
    ensure_sources_file()
    return JSONResponse(
        {
            "ok": True,
            "sources": read_json(SOURCES_FILE, []),
            "time": now_iso(),
        }
    )


@app.put("/api/sources")
async def api_put_sources(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    sources = validate_sources_config(payload)

    backup_path = backup_sources_file()
    write_json_atomic(SOURCES_FILE, sources)

    return JSONResponse(
        {
            "ok": True,
            "message": "sources.json saved; routes were not updated automatically",
            "backup": str(backup_path) if backup_path else None,
            "sources_count": len(sources),
            "time": now_iso(),
        }
    )


@app.get("/status")
async def status(_: str = Depends(require_auth)) -> JSONResponse:
    advertised_routes = read_lines(ADVERTISED_FILE)
    last_good_routes = read_lines(LAST_GOOD_FILE)

    return JSONResponse(
        {
            "app": APP_NAME,
            "gobgp_ready": gobgp_ready(),
            "status": read_json(STATUS_FILE, {}),
            "advertised_routes_summary": summarize_prefixes(advertised_routes),
            "last_good_routes_summary": summarize_prefixes(last_good_routes),
            "sources": read_json(SOURCES_FILE, []),
            "time": now_iso(),
        }
    )


@app.get("/status/full")
async def status_full(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        {
            "app": APP_NAME,
            "gobgp_ready": gobgp_ready(),
            "status": read_json(STATUS_FILE, {}),
            "advertised_routes": read_lines(ADVERTISED_FILE),
            "last_good_routes": read_lines(LAST_GOOD_FILE),
            "sources": read_json(SOURCES_FILE, []),
            "time": now_iso(),
        }
    )


@app.post("/update")
async def update(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    allow_large = False

    try:
        payload = await request.json()
        if isinstance(payload, dict):
            allow_large = bool(payload.get("allow_large", False))
    except Exception:
        allow_large = False

    try:
        return JSONResponse(await asyncio.to_thread(update_now, False, "manual", allow_large))
    except Exception as e:
        result = {
            "ok": False,
            "mode": "manual_update_failed",
            "error": str(e),
            "time": now_iso(),
        }
        save_status(result)
        append_update_history(result, "manual_failed")
        return JSONResponse(result, status_code=500)


@app.post("/api/update/job")
async def update_job(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    allow_large = False

    try:
        payload = await request.json()
        if isinstance(payload, dict):
            allow_large = bool(payload.get("allow_large", False))
    except Exception:
        allow_large = False

    return JSONResponse(
        start_background_job(
            kind="route_update",
            key="route_update:manual",
            title="Применение маршрутов",
            target=lambda: update_now(False, "manual", allow_large),
            payload={"allow_large": allow_large},
        )
    )


def preflight_group(group_name: str) -> dict[str, Any]:
    ensure_sources_file()

    sources = read_json(SOURCES_FILE, [])

    if not isinstance(sources, list):
        raise RuntimeError("sources.json must be a JSON array")

    group_sources = [
        source for source in sources
        if str(source.get("group", "")) == group_name
        and str(source.get("strategy", "")).lower() == "first_success"
    ]

    if not group_sources:
        raise HTTPException(status_code=404, detail=f"group not found: {group_name}")

    sorted_group_sources = sorted(
        group_sources,
        key=lambda item: int(item.get("priority", 1000)),
    )

    source_stats: list[dict[str, Any]] = []
    errors: list[str] = []
    selected_source = None
    selected_prefixes: set[ipaddress.IPv4Network] = set()

    for source in sorted_group_sources:
        if selected_source is not None:
            stat = {
                "name": source.get("name"),
                "enabled": bool(source.get("enabled", False)),
                "type": source.get("type"),
                "group": source.get("group"),
                "strategy": source.get("strategy"),
                "priority": source.get("priority"),
                "accepted": 0,
                "ignored": 0,
                "error": None,
                "selected": False,
                "skipped": True,
                "skip_reason": "previous source already selected",
            }
            source_stats.append(stat)
            continue

        test_source = dict(source)
        test_source["enabled"] = True

        prefixes, stat = collect_one_source(test_source)

        if stat["error"] is None and stat["accepted"] > 0:
            selected_source = stat["name"]
            selected_prefixes = prefixes
            stat["selected"] = True
            source_stats.append(stat)
            continue

        message = f"{stat['name']}: {stat['error'] or 'no accepted prefixes'}"
        errors.append(message)
        source_stats.append(stat)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(selected_prefixes)))
    else:
        final_networks = sorted(selected_prefixes)

    final_prefixes = [str(net) for net in final_networks]

    return {
        "ok": selected_source is not None,
        "mode": "preflight_group_first_success",
        "would_apply": False,
        "group": group_name,
        "strategy": "first_success",
        "selected_source": selected_source,
        "errors": errors,
        "source_stats": source_stats,
        "unique_before_aggregation": len(selected_prefixes),
        "final_count": len(final_prefixes),
        "aggregate": AGGREGATE_PREFIXES,
        "first_20": final_prefixes[:20],
        "last_20": final_prefixes[-20:],
        "time": now_iso(),
    }


@app.get("/preflight/group/{group_name}")
async def preflight_group_endpoint(group_name: str, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(preflight_group, group_name))
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "preflight_group_failed",
                "group": group_name,
                "error": str(e),
                "would_apply": False,
                "time": now_iso(),
            },
            status_code=500,
        )


@app.post("/apply-last-good")
async def apply_last_good_endpoint(_: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(apply_last_good, False))
    except Exception as e:
        result = {
            "ok": False,
            "mode": "apply_last_good_failed",
            "error": str(e),
            "time": now_iso(),
        }
        save_status(result)
        return JSONResponse(result, status_code=500)


@app.get("/routes", response_class=PlainTextResponse)
async def routes(_: str = Depends(require_auth)) -> str:
    prefixes = read_lines(ADVERTISED_FILE)
    return "\n".join(prefixes) + ("\n" if prefixes else "")


@app.get("/gobgp/global", response_class=PlainTextResponse)
async def gobgp_global(_: str = Depends(require_auth)) -> str:
    return gobgp_text(["gobgp", "global"])


@app.get("/gobgp/neighbor", response_class=PlainTextResponse)
async def gobgp_neighbor(_: str = Depends(require_auth)) -> str:
    return gobgp_text(["gobgp", "neighbor"])


@app.get("/gobgp/rib", response_class=PlainTextResponse)
async def gobgp_rib(_: str = Depends(require_auth)) -> str:
    return gobgp_text(["gobgp", "global", "rib", "-a", "ipv4"])


def backend_index_html() -> str:
    return """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>The333 Backend</title>
  <style>
    :root {
      --bg: #090909;
      --panel: #202020;
      --panel-2: #2a2a2a;
      --text: #f4f4f5;
      --muted: #9ca3af;
      --border: rgba(255,255,255,.11);
      --accent: #c88616;
      --ok: #3ddc84;
      --warn: #f2aa2b;
      --bad: #ff6b6b;
      --blue: #7ec7ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 78% 0%, rgba(200,134,22,.11), transparent 32%),
        linear-gradient(135deg, #0b0b0c, #151515 58%, #080808);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }
    button, a { font: inherit; }
    .page {
      width: min(1480px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 34px;
    }
    .top {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: start;
      margin-bottom: 18px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--text);
      font-size: 26px;
      font-weight: 900;
      line-height: 1;
    }
    .satellite {
      color: var(--accent);
      font-size: 26px;
      line-height: 1;
    }
    .brand small {
      color: var(--muted);
      font-size: 16px;
      font-weight: 850;
    }
    h1 {
      margin: 20px 0 8px;
      font-size: clamp(32px, 4vw, 54px);
      line-height: 1.02;
    }
    .lead {
      max-width: 760px;
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
    }
    .links {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }
    .button {
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0 13px;
      border: 1px solid var(--border);
      border-radius: 14px;
      color: var(--text);
      background: rgba(255,255,255,.065);
      text-decoration: none;
      cursor: pointer;
      font-size: 12px;
      font-weight: 820;
      transition: border-color .16s ease, background .16s ease, transform .16s ease;
    }
    .button:hover {
      border-color: rgba(200,134,22,.42);
      background: rgba(200,134,22,.13);
      transform: translateY(-1px);
    }
    .grid {
      display: grid;
      gap: 14px;
    }
    .summary {
      grid-template-columns: repeat(4, minmax(0, 1fr));
      margin: 18px 0;
    }
    .ops-strip {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
      margin: 18px 0;
    }
    .ops-cell {
      min-width: 0;
      padding: 11px 12px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: rgba(255,255,255,.045);
    }
    .ops-cell span,
    .ops-cell strong {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .ops-cell span {
      color: var(--muted);
      font-size: 10.5px;
      font-weight: 850;
    }
    .ops-cell strong {
      margin-top: 5px;
      color: var(--text);
      font-size: 13px;
      font-weight: 900;
      font-variant-numeric: tabular-nums;
    }
    .main-grid {
      grid-template-columns: minmax(0, 1fr) minmax(0, .9fr);
      align-items: start;
      margin-top: 14px;
    }
    .diagnostics-card {
      margin-bottom: 14px;
    }
    .endpoint-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 8px;
    }
    .endpoint-link {
      min-width: 0;
      display: grid;
      gap: 5px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 15px;
      color: var(--text);
      background: rgba(255,255,255,.045);
      text-decoration: none;
    }
    .endpoint-link:hover {
      border-color: rgba(200,134,22,.42);
      background: rgba(200,134,22,.11);
    }
    .endpoint-link strong,
    .endpoint-link span {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .endpoint-link strong { font-size: 12px; }
    .endpoint-link span { color: var(--muted); font-size: 11px; }
    .card {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 22px;
      background: linear-gradient(135deg, rgba(44,44,44,.94), rgba(24,24,25,.96));
      box-shadow: 0 18px 42px rgba(0,0,0,.23);
      padding: 18px;
    }
    .card.compact {
      padding: 16px;
    }
    .card h2 {
      margin: 0;
      color: var(--text);
      font-size: 18px;
      line-height: 1.2;
    }
    .card-title {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }
    .metric span,
    .kv span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 850;
      line-height: 1.2;
    }
    .metric strong {
      display: block;
      margin-top: 10px;
      overflow: hidden;
      color: var(--text);
      font-size: 30px;
      line-height: 1;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .metric small {
      display: block;
      margin-top: 12px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }
    .pill {
      min-height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0 10px;
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(255,255,255,.055);
      font-size: 12px;
      font-weight: 840;
      white-space: nowrap;
    }
    .ok { color: var(--ok) !important; }
    .warn { color: var(--warn) !important; }
    .bad { color: var(--bad) !important; }
    .blue { color: var(--blue) !important; }
    .pill.ok { border-color: rgba(61,220,132,.26); background: rgba(61,220,132,.09); }
    .pill.warn { border-color: rgba(242,170,43,.28); background: rgba(242,170,43,.10); }
    .pill.bad { border-color: rgba(255,107,107,.28); background: rgba(255,107,107,.10); }
    .kv-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .kv {
      min-width: 0;
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      padding: 11px 12px;
      border: 1px solid var(--border);
      border-radius: 15px;
      background: rgba(255,255,255,.04);
    }
    .kv strong {
      min-width: 0;
      overflow: hidden;
      color: var(--text);
      font-size: 13px;
      text-align: right;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .list {
      display: grid;
      gap: 8px;
      max-height: 360px;
      overflow-y: auto;
      padding-right: 7px;
      scrollbar-color: rgba(200,134,22,.46) rgba(255,255,255,.08);
      scrollbar-width: thin;
    }
    .row {
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 11px 12px;
      border: 1px solid var(--border);
      border-radius: 15px;
      background: rgba(255,255,255,.04);
    }
    .row strong,
    .row span {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .row strong { font-size: 13px; }
    .row span { margin-top: 3px; color: var(--muted); font-size: 11px; }
    pre {
      max-height: 420px;
      overflow: auto;
      margin: 0;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 16px;
      color: #d9f1ff;
      background: rgba(0,0,0,.22);
      font: 12px/1.45 "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
      white-space: pre-wrap;
      scrollbar-color: rgba(200,134,22,.46) rgba(255,255,255,.08);
      scrollbar-width: thin;
    }
    .raw-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
      align-content: start;
    }
    .raw-card pre {
      max-height: 300px;
    }
    .safe-actions {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }
    .safe-actions .button {
      width: 100%;
      min-height: 44px;
      text-align: center;
    }
    .status {
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
      min-height: 18px;
    }
    .footer {
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
    @media (max-width: 980px) {
      .top, .main-grid { grid-template-columns: 1fr; }
      .links { justify-content: flex-start; }
      .summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .ops-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .safe-actions { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 620px) {
      .page { width: min(100% - 24px, 1480px); padding-top: 18px; }
      .summary, .kv-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .endpoint-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .ops-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .card { border-radius: 18px; padding: 15px; }
      .metric strong { font-size: 26px; }
    }
    @media (max-width: 430px) {
      .summary,
      .kv-grid,
      .endpoint-grid,
      .ops-strip,
      .safe-actions {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main class="page">
    <section class="top">
      <div>
        <div class="brand"><span class="satellite">🛰</span><span>The333</span><small>· Backend</small></div>
        <h1>Backend API</h1>
        <p class="lead">Техническая поверхность backend-сервиса: readiness, GoBGP API, файлы данных, ресурсы, фоновые задачи и быстрые ссылки на JSON endpoints. Основная пользовательская работа остаётся в портале.</p>
      </div>
      <nav class="links">
        <a class="button" href="http://192.168.1.111:8090/">Открыть портал</a>
        <a class="button" href="/ready" target="_blank">/ready</a>
        <a class="button" href="/api/diagnostics" target="_blank">/api/diagnostics</a>
        <a class="button" href="/api/services" target="_blank">/api/services</a>
        <a class="button" href="/api/jobs" target="_blank">/api/jobs</a>
      </nav>
    </section>

    <section class="grid summary">
      <div class="card metric"><span>Backend</span><strong id="readyState">...</strong><small id="readyNote">readiness check</small></div>
      <div class="card metric"><span>GoBGP</span><strong id="gobgpState">...</strong><small>gRPC API</small></div>
      <div class="card metric"><span>Опубликовано</span><strong id="advertisedCount">...</strong><small>advertised prefixes</small></div>
      <div class="card metric"><span>RIB</span><strong id="ribCount">...</strong><small>global rib count</small></div>
    </section>

    <section class="ops-strip" aria-label="Системная информация backend">
      <div class="ops-cell"><span>ASN</span><strong id="stripAsn">...</strong></div>
      <div class="ops-cell"><span>Community</span><strong id="stripCommunity">...</strong></div>
      <div class="ops-cell"><span>Автообновление</span><strong id="stripAutoUpdate">...</strong></div>
      <div class="ops-cell"><span>Модули</span><strong id="stripServices">...</strong></div>
      <div class="ops-cell"><span>CPU / RAM</span><strong id="stripCpuRam">...</strong></div>
      <div class="ops-cell"><span>Disk</span><strong id="stripDisk">...</strong></div>
    </section>

    <section class="card compact diagnostics-card">
      <div class="card-title"><h2>Диагностические ручки</h2><span class="pill">7 read-only</span></div>
      <div class="endpoint-grid">
        <a class="endpoint-link" href="/status/full" target="_blank"><strong>/status/full</strong><span>полный статус update pipeline</span></a>
        <a class="endpoint-link" href="/api/diagnostics" target="_blank"><strong>/api/diagnostics</strong><span>файлы, env, GoBGP output</span></a>
        <a class="endpoint-link" href="/api/server-resources" target="_blank"><strong>/api/server-resources</strong><span>CPU, RAM, Disk</span></a>
        <a class="endpoint-link" href="/api/update-history" target="_blank"><strong>/api/update-history</strong><span>история обновлений</span></a>
        <a class="endpoint-link" href="/gobgp/neighbor" target="_blank"><strong>/gobgp/neighbor</strong><span>BGP peer raw output</span></a>
        <a class="endpoint-link" href="/gobgp/rib" target="_blank"><strong>/gobgp/rib</strong><span>GoBGP global RIB</span></a>
        <a class="endpoint-link" href="/api/services/source-refresh" target="_blank"><strong>/api/services/source-refresh</strong><span>статус geosite/geoip</span></a>
      </div>
    </section>

    <section class="grid main-grid">
      <div class="grid">
        <div class="card compact">
          <div class="card-title"><h2>Безопасные действия</h2><span class="pill">no apply</span></div>
          <div class="safe-actions">
            <button class="button" type="button" onclick="loadBackend()">Обновить</button>
            <a class="button" href="/preflight/group/main-blocked-routes" target="_blank">Preflight main</a>
            <a class="button" href="/status/full" target="_blank">Status full</a>
          </div>
          <div class="status">Здесь только read-only проверки. Применение маршрутов выполняется в основном портале.</div>
        </div>

        <div class="card">
          <div class="card-title"><h2>Runtime</h2><span id="runtimePill" class="pill">загрузка</span></div>
          <div class="kv-grid">
            <div class="kv"><span>ASN сервиса</span><strong id="localAs">...</strong></div>
            <div class="kv"><span>Community</span><strong id="community">...</strong></div>
            <div class="kv"><span>Источников</span><strong id="sourcesCount">...</strong></div>
            <div class="kv"><span>Модулей</span><strong id="servicesCount">...</strong></div>
            <div class="kv"><span>CPU / RAM</span><strong id="cpuRam">...</strong></div>
            <div class="kv"><span>Disk</span><strong id="diskUsage">...</strong></div>
          </div>
        </div>

        <div class="card">
          <div class="card-title"><h2>Фоновые задачи</h2><span id="jobsPill" class="pill">...</span></div>
          <div id="jobsList" class="list"></div>
        </div>

        <div class="card">
          <div class="card-title"><h2>Файлы данных</h2><span class="pill">read-only</span></div>
          <div id="filesList" class="list"></div>
        </div>
      </div>

      <div class="raw-grid">
        <div class="card raw-card">
          <div class="card-title"><h2>Сырой ready</h2><button class="button" type="button" onclick="copyBlock('rawReady')">Копировать</button></div>
          <pre id="rawReady">Загрузка...</pre>
        </div>

        <div class="card raw-card">
          <div class="card-title"><h2>Сырой diagnostics</h2><button class="button" type="button" onclick="copyBlock('rawDiagnostics')">Копировать</button></div>
          <pre id="rawDiagnostics">Загрузка...</pre>
        </div>
        <div id="statusLine" class="status"></div>
      </div>
    </section>

    <div class="footer">Прямой backend URL нужен для технической диагностики. Для настройки источников, модулей, маршрутов и community используй основной портал.</div>
  </main>

  <script>
    const text = (value, fallback = "—") => value === undefined || value === null || value === "" ? fallback : String(value);
    const fmt = (value) => typeof value === "number" ? value.toLocaleString("ru-RU") : text(value);
    const percent = (value) => typeof value === "number" ? `${value}%` : "—";
    const cls = (ok) => ok ? "ok" : "bad";
    function set(id, value, className) {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text(value);
      if (className) el.className = className;
    }
    function row(title, subtitle, pill, tone = "") {
      return `<div class="row"><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(subtitle)}</span></div><span class="pill ${tone}">${escapeHtml(pill)}</span></div>`;
    }
    async function copyBlock(id) {
      const textValue = document.getElementById(id)?.textContent || "";
      try {
        await navigator.clipboard.writeText(textValue);
        document.getElementById("statusLine").textContent = `Скопировано: ${id}`;
      } catch {
        document.getElementById("statusLine").textContent = `Не удалось скопировать: ${id}`;
      }
    }
    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }
    async function getJson(path) {
      const response = await fetch(path, { credentials: "same-origin" });
      const textValue = await response.text();
      try {
        const payload = JSON.parse(textValue);
        return { ok: response.ok, status: response.status, payload };
      } catch {
        return { ok: response.ok, status: response.status, payload: { raw: textValue } };
      }
    }
    async function loadBackend() {
      const status = document.getElementById("statusLine");
      status.textContent = "Обновляю данные backend...";
      try {
        const [ready, diagnostics, resources, services, jobs] = await Promise.all([
          getJson("/ready"),
          getJson("/api/diagnostics"),
          getJson("/api/server-resources"),
          getJson("/api/services"),
          getJson("/api/jobs")
        ]);

        const readyPayload = ready.payload || {};
        const diagnosticsPayload = diagnostics.payload || {};
        const env = diagnosticsPayload.safe_env || {};
        const resourcesPayload = resources.payload || {};
        const servicesPayload = services.payload || {};
        const jobsPayload = jobs.payload || {};

        set("readyState", readyPayload.ready ? "работает" : "ошибка", `metric-value ${cls(readyPayload.ready)}`);
        set("readyNote", readyPayload.errors?.length ? readyPayload.errors.join("; ") : "ready=true");
        set("gobgpState", readyPayload.gobgp_ready ? "онлайн" : "офлайн", `metric-value ${cls(readyPayload.gobgp_ready)}`);
        set("advertisedCount", fmt(readyPayload.advertised_count), "metric-value blue");
        set("ribCount", fmt(readyPayload.rib_count), `metric-value ${readyPayload.rib_count === readyPayload.advertised_count ? "ok" : "warn"}`);
        set("runtimePill", readyPayload.ready ? "штатно" : "требует внимания", `pill ${readyPayload.ready ? "ok" : "bad"}`);
        set("localAs", env.LOCAL_AS);
        set("community", env.BGP_COMMUNITY);
        set("sourcesCount", diagnosticsPayload.sources_count);
        set("servicesCount", `${servicesPayload.cache?.enabled_count ?? 0}/${servicesPayload.catalog?.length ?? "—"}`);
        set("cpuRam", `${percent(resourcesPayload.cpu?.used_percent)} / ${percent(resourcesPayload.ram?.used_percent)}`);
        set("diskUsage", percent(resourcesPayload.disk?.used_percent));
        set("stripAsn", env.LOCAL_AS);
        set("stripCommunity", env.BGP_COMMUNITY);
        set("stripAutoUpdate", env.AUTO_UPDATE ? `${Math.round(Number(env.UPDATE_INTERVAL_SECONDS || 0) / 3600)} ч` : "выключено");
        set("stripServices", `${servicesPayload.cache?.enabled_count ?? 0}/${servicesPayload.catalog?.length ?? "—"}`);
        set("stripCpuRam", `${percent(resourcesPayload.cpu?.used_percent)} / ${percent(resourcesPayload.ram?.used_percent)}`);
        set("stripDisk", percent(resourcesPayload.disk?.used_percent));

        document.getElementById("rawReady").textContent = JSON.stringify(readyPayload, null, 2);
        document.getElementById("rawDiagnostics").textContent = JSON.stringify({
          ok: diagnosticsPayload.ok,
          gobgp_ready: diagnosticsPayload.gobgp_ready,
          gobgp_rib_count: diagnosticsPayload.gobgp_rib_count,
          sources_count: diagnosticsPayload.sources_count,
          advertised_routes_summary: diagnosticsPayload.advertised_routes_summary,
          last_good_routes_summary: diagnosticsPayload.last_good_routes_summary,
          safe_env: diagnosticsPayload.safe_env,
          files: diagnosticsPayload.files,
          time: diagnosticsPayload.time
        }, null, 2);

        const files = diagnosticsPayload.files || [];
        document.getElementById("filesList").innerHTML = files.length
          ? files.map((file) => row(file.name || file.path, file.exists ? `${fmt(file.size)} bytes · ${text(file.mtime)}` : text(file.path), file.exists ? "есть" : "нет", file.exists ? "ok" : "bad")).join("")
          : row("Файлы не получены", "endpoint /api/diagnostics не вернул список файлов", "нет данных", "warn");

        const jobItems = jobsPayload.jobs || [];
        document.getElementById("jobsPill").textContent = `${jobItems.length} записей`;
        document.getElementById("jobsList").innerHTML = jobItems.length
          ? jobItems.slice(0, 12).map((job) => row(job.title || job.kind || job.id, `${job.status || "unknown"} · ${job.stage || "—"}`, `${Math.round(Number(job.progress_percent || 0))}%`, job.status === "failed" ? "bad" : job.status === "running" || job.status === "queued" ? "warn" : "ok")).join("")
          : row("Нет задач", "Фоновые операции сейчас не выполняются", "ok", "ok");

        status.textContent = `Обновлено: ${text(readyPayload.time)}`;
      } catch (error) {
        status.textContent = `Ошибка обновления backend dashboard: ${error}`;
        status.className = "status bad";
      }
    }
    loadBackend();
    setInterval(loadBackend, 30000);
  </script>
</body>
</html>
""".replace("The333 Backend", f"{APP_NAME} Backend")


@app.get("/", response_class=HTMLResponse)
async def index(_: str = Depends(require_auth)) -> str:
    return backend_index_html()





def read_server_resources():
    import os
    import shutil
    import time as _time

    def read_cpu_totals():
        with open("/proc/stat", "r", encoding="utf-8") as file:
            line = file.readline().strip()

        fields = [int(value) for value in line.split()[1:]]
        idle = fields[3] + fields[4]
        total = sum(fields)
        return idle, total

    def read_meminfo():
        values = {}

        with open("/proc/meminfo", "r", encoding="utf-8") as file:
            for line in file:
                key, raw_value = line.split(":", 1)
                number = int(raw_value.strip().split()[0])
                values[key] = number * 1024

        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        used = max(total - available, 0)
        percent = round((used / total * 100), 1) if total else None

        return {
            "total_bytes": total,
            "available_bytes": available,
            "used_bytes": used,
            "used_percent": percent,
        }

    def read_disk_usage():
        target = "/data" if os.path.exists("/data") else "/"
        usage = shutil.disk_usage(target)
        percent = round((usage.used / usage.total * 100), 1) if usage.total else None

        return {
            "path": target,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": percent,
        }

    cpu_percent = None

    try:
        idle_1, total_1 = read_cpu_totals()
        _time.sleep(0.15)
        idle_2, total_2 = read_cpu_totals()

        idle_delta = idle_2 - idle_1
        total_delta = total_2 - total_1

        if total_delta > 0:
            cpu_percent = round((1 - idle_delta / total_delta) * 100, 1)
    except Exception:
        cpu_percent = None

    return {
        "ok": True,
        "cpu": {
            "used_percent": cpu_percent,
            "cores": os.cpu_count(),
        },
        "ram": read_meminfo(),
        "disk": read_disk_usage(),
        "time": now_iso(),
    }


@app.get("/api/server-resources")
async def api_server_resources(_: str = Depends(require_auth)):
    return JSONResponse(read_server_resources())


SERVICE_CATALOG_FILE = CONFIG_DIR / "service_catalog.json"
SERVICE_STATE_FILE = DATA_DIR / "service_state.json"
SERVICE_CACHE_FILE = DATA_DIR / "service_cache.json"
SERVICE_DNS_CACHE_FILE = DATA_DIR / "service_dns_cache.json"
SERVICE_ROUTES_FILE = DATA_DIR / "service_routes.txt"
SERVICE_LAST_GOOD_ROUTES_FILE = DATA_DIR / "service_last_good_routes.txt"
SERVICE_SOURCE_REFRESH_FILE = DATA_DIR / "service_source_refresh.json"
SERVICE_CANDIDATES_FILE = DATA_DIR / "service_candidates.json"
SERVICE_CANDIDATES_SEED_FILE = CONFIG_DIR / "service_candidates.seed.json"
SERVICE_CATALOG_BACKUP_DIR = DATA_DIR / "service_catalog_backups"
SERVICE_REMOVED_CATALOG_FILE = DATA_DIR / "service_removed_catalog.json"
SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS = int(os.getenv("SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS", "86400"))
SERVICE_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS = float(os.getenv("SERVICE_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS", "25"))

V2FLY_DLC_DATA_API_URL = os.getenv(
    "V2FLY_DLC_DATA_API_URL",
    "https://api.github.com/repos/v2fly/domain-list-community/contents/data?ref=master",
)

V2FLY_DLC_TREE_API_URL = os.getenv(
    "V2FLY_DLC_TREE_API_URL",
    "https://api.github.com/repos/v2fly/domain-list-community/git/trees/master?recursive=1",
)

V2FLY_DLC_RAW_BASE_URL = os.getenv(
    "V2FLY_DLC_RAW_BASE_URL",
    "https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/",
)

SERVICE_SOURCE_KINDS: dict[str, dict[str, Any]] = {
    "geosite": {
        "label": "Geosite",
        "description": "Доменные списки сервисов, которые затем резолвятся в IPv4-маршруты.",
        "provider_types": ["geosite_plain"],
    },
    "geoip": {
        "label": "GeoIP / IP ranges",
        "description": "Готовые IPv4 CIDR и официальные JSON-диапазоны провайдеров.",
        "provider_types": ["geoip_plain", "ipranges_json"],
    },
}

SERVICE_CANDIDATE_TITLE_OVERRIDES: dict[str, str] = {
    "adobe": "Adobe",
    "amazon": "Amazon",
    "anthropic": "Anthropic / Claude",
    "apple": "Apple",
    "aws": "Amazon Web Services",
    "bbc": "BBC",
    "bilibili": "Bilibili",
    "bing": "Bing",
    "claude": "Claude",
    "cloudflare": "Cloudflare",
    "cloudfront": "Amazon CloudFront",
    "discord": "Discord",
    "docker": "Docker",
    "dropbox": "Dropbox",
    "facebook": "Facebook",
    "fastly": "Fastly",
    "github": "GitHub",
    "gitlab": "GitLab",
    "google": "Google",
    "instagram": "Instagram",
    "jetbrains": "JetBrains",
    "linkedin": "LinkedIn",
    "matrix": "Element / Matrix",
    "microsoft": "Microsoft",
    "netflix": "Netflix",
    "notion": "Notion",
    "openai": "OpenAI / ChatGPT",
    "perplexity": "Perplexity",
    "pornhub": "Pornhub",
    "reddit": "Reddit",
    "signal": "Signal",
    "slack": "Slack",
    "spotify": "Spotify",
    "steam": "Steam",
    "telegram": "Telegram",
    "tiktok": "TikTok",
    "twitch": "Twitch",
    "twitter": "X / Twitter",
    "vimeo": "Vimeo",
    "whatsapp": "WhatsApp",
    "youtube": "YouTube",
    "zoom": "Zoom",
}

SERVICE_CANDIDATE_ID_OVERRIDES: dict[str, str] = {
    "matrix": "element-matrix",
}

SERVICE_CANDIDATE_EXISTING_ID_ALIASES: dict[str, set[str]] = {
    "chatgpt": {"openai"},
    "claude": {"anthropic"},
    "element": {"element-matrix", "matrix"},
    "element-matrix": {"matrix"},
    "facebook": {"meta-facebook"},
    "googlevideo": {"youtube-googlevideo", "youtube"},
    "instagram": {"meta-facebook"},
    "matrix": {"element-matrix"},
    "twitter": {"x-twitter"},
    "x": {"x-twitter", "twitter"},
    "youtube": {"youtube-googlevideo"},
}

SERVICE_CANDIDATE_PRIORITY: dict[str, int] = {
    "openai": 1000,
    "anthropic": 995,
    "google": 980,
    "youtube": 970,
    "discord": 955,
    "reddit": 950,
    "telegram": 945,
    "twitter": 940,
    "instagram": 935,
    "facebook": 930,
    "tiktok": 925,
    "whatsapp": 920,
    "signal": 915,
    "matrix": 912,
    "github": 900,
    "gitlab": 895,
    "docker": 890,
    "spotify": 875,
    "netflix": 870,
    "twitch": 865,
    "steam": 850,
    "pornhub": 830,
    "cloudflare": 700,
    "fastly": 695,
    "aws": 690,
    "azure": 685,
    "microsoft": 680,
    "apple": 675,
}

SERVICE_CANDIDATE_FALLBACK_CODES = [
    "openai",
    "anthropic",
    "google",
    "youtube",
    "discord",
    "reddit",
    "telegram",
    "twitter",
    "instagram",
    "facebook",
    "tiktok",
    "whatsapp",
    "signal",
    "github",
    "gitlab",
    "docker",
    "spotify",
    "netflix",
    "twitch",
    "steam",
    "pornhub",
    "cloudflare",
    "fastly",
    "aws",
    "azure",
    "microsoft",
    "apple",
]

SERVICE_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("adult", ("porn", "hentai", "xvideos", "xhamster", "onlyfans", "redtube", "youjizz")),
    ("ai", ("openai", "anthropic", "claude", "gemini", "perplexity", "copilot", "huggingface", "midjourney")),
    ("messenger", ("telegram", "whatsapp", "signal", "viber", "line", "messenger", "wechat", "matrix", "element")),
    ("social", ("twitter", "x-twitter", "facebook", "instagram", "reddit", "linkedin", "pinterest", "threads", "mastodon", "discord")),
    ("video", ("youtube", "netflix", "twitch", "tiktok", "vimeo", "hulu", "disney", "primevideo", "crunchyroll", "peacock")),
    ("gaming", ("steam", "epicgames", "playstation", "xbox", "nintendo", "battle", "riot", "rockstar", "ubisoft", "ea")),
    ("dev", ("github", "gitlab", "docker", "npm", "pypi", "jetbrains", "stackoverflow", "sourceforge", "developer")),
    ("cloud", ("aws", "azure", "googlecloud", "gcp", "oracle", "digitalocean", "heroku", "vercel", "cloud")),
    ("cdn", ("cloudflare", "cloudfront", "fastly", "akamai", "cdn")),
    ("finance", ("paypal", "stripe", "binance", "coinbase", "wise", "revolut", "bank")),
    ("productivity", ("slack", "notion", "atlassian", "trello", "zoom", "dropbox", "office", "onedrive")),
    ("media", ("spotify", "bbc", "medium", "news", "nytimes", "bloomberg")),
]

SERVICE_CATEGORY_TOKEN_ONLY_KEYWORDS = {
    "aws",
    "bbc",
    "ea",
    "element",
    "gcp",
    "line",
    "matrix",
    "npm",
    "pypi",
}

ROUTE_SET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "advertised": {
        "label": "Опубликованные",
        "description": "Текущий набор маршрутов, который сервис отдаёт в GoBGP.",
        "path": ADVERTISED_FILE,
    },
    "last_good": {
        "label": "Последний удачный",
        "description": "Последний успешно сохранённый набор маршрутов.",
        "path": LAST_GOOD_FILE,
    },
    "service": {
        "label": "Маршруты модулей",
        "description": "Текущие маршруты, собранные из включённых модулей.",
        "path": SERVICE_ROUTES_FILE,
    },
    "service_last_good": {
        "label": "Модули: последний удачный",
        "description": "Последний успешно сохранённый набор маршрутов модулей.",
        "path": SERVICE_LAST_GOOD_ROUTES_FILE,
    },
}

ROUTE_DIFF_SECTION_LABELS = {
    "added": "Добавлено",
    "removed": "Удалено",
    "unchanged": "Совпадает",
}


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def route_set_definition(kind: str) -> dict[str, Any]:
    definition = ROUTE_SET_DEFINITIONS.get(kind)

    if definition is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown route set: {kind}",
        )

    return definition


def filter_routes_by_query(routes: list[str], query: str) -> list[str]:
    query_lower = query.lower()

    if not query_lower:
        return routes

    return [
        route
        for route in routes
        if query_lower in route.lower()
    ]


def route_set_meta() -> list[dict[str, Any]]:
    return [
        {
            "kind": kind,
            "label": definition["label"],
            "description": definition["description"],
            "file": safe_file_info(definition["path"]),
        }
        for kind, definition in ROUTE_SET_DEFINITIONS.items()
    ]


@app.get("/api/routes")
async def api_routes(
    kind: str = "advertised",
    q: str = "",
    limit: int = 500,
    offset: int = 0,
    _: str = Depends(require_auth),
) -> JSONResponse:
    definition = route_set_definition(kind)

    safe_limit = clamp_int(limit, 1, 2000)
    safe_offset = max(0, offset)
    query = q.strip()[:160]

    routes = read_lines(definition["path"])
    filtered_routes = filter_routes_by_query(routes, query)
    page_routes = filtered_routes[safe_offset:safe_offset + safe_limit]

    return JSONResponse(
        {
            "ok": True,
            "kind": kind,
            "label": definition["label"],
            "description": definition["description"],
            "query": query,
            "limit": safe_limit,
            "offset": safe_offset,
            "total_count": len(routes),
            "filtered_count": len(filtered_routes),
            "routes": page_routes,
            "first_20": routes[:20],
            "last_20": routes[-20:],
            "file": safe_file_info(definition["path"]),
            "available_sets": route_set_meta(),
            "time": now_iso(),
        }
    )


@app.get("/api/routes/diff")
async def api_routes_diff(
    base: str = "last_good",
    target: str = "advertised",
    section: str = "added",
    q: str = "",
    limit: int = 500,
    offset: int = 0,
    _: str = Depends(require_auth),
) -> JSONResponse:
    base_definition = route_set_definition(base)
    target_definition = route_set_definition(target)

    if section not in ROUTE_DIFF_SECTION_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown diff section: {section}",
        )

    safe_limit = clamp_int(limit, 1, 2000)
    safe_offset = max(0, offset)
    query = q.strip()[:160]

    base_routes = read_lines(base_definition["path"])
    target_routes = read_lines(target_definition["path"])
    base_set = set(base_routes)
    target_set = set(target_routes)

    added_routes = [
        route
        for route in target_routes
        if route not in base_set
    ]
    removed_routes = [
        route
        for route in base_routes
        if route not in target_set
    ]
    unchanged_routes = [
        route
        for route in target_routes
        if route in base_set
    ]

    section_routes = {
        "added": added_routes,
        "removed": removed_routes,
        "unchanged": unchanged_routes,
    }
    filtered_routes = {
        key: filter_routes_by_query(routes, query)
        for key, routes in section_routes.items()
    }
    selected_routes = filtered_routes[section]
    page_routes = selected_routes[safe_offset:safe_offset + safe_limit]

    return JSONResponse(
        {
            "ok": True,
            "base": {
                "kind": base,
                "label": base_definition["label"],
                "description": base_definition["description"],
                "file": safe_file_info(base_definition["path"]),
            },
            "target": {
                "kind": target,
                "label": target_definition["label"],
                "description": target_definition["description"],
                "file": safe_file_info(target_definition["path"]),
            },
            "section": section,
            "section_label": ROUTE_DIFF_SECTION_LABELS[section],
            "query": query,
            "limit": safe_limit,
            "offset": safe_offset,
            "counts": {
                "base": len(base_routes),
                "target": len(target_routes),
                "added": len(added_routes),
                "removed": len(removed_routes),
                "unchanged": len(unchanged_routes),
            },
            "filtered_counts": {
                "added": len(filtered_routes["added"]),
                "removed": len(filtered_routes["removed"]),
                "unchanged": len(filtered_routes["unchanged"]),
            },
            "routes": page_routes,
            "available_sets": route_set_meta(),
            "time": now_iso(),
        }
    )


def ensure_service_state_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if SERVICE_STATE_FILE.exists():
        return

    catalog = read_service_catalog()
    state = {
        "version": 1,
        "services": {
            item["id"]: {"enabled": False}
            for item in catalog
            if isinstance(item, dict) and item.get("id")
        },
    }
    write_json_atomic(SERVICE_STATE_FILE, state)


def read_service_catalog() -> list[dict[str, Any]]:
    catalog = read_json(SERVICE_CATALOG_FILE, [])

    if not isinstance(catalog, list):
        raise RuntimeError("service_catalog.json must be a JSON array")

    return catalog


def backup_service_catalog_file() -> Path | None:
    if not SERVICE_CATALOG_FILE.exists():
        return None

    SERVICE_CATALOG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = SERVICE_CATALOG_BACKUP_DIR / f"service_catalog.{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    backup_path.write_text(SERVICE_CATALOG_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def write_service_catalog(catalog: list[dict[str, Any]]) -> None:
    if not isinstance(catalog, list):
        raise RuntimeError("service catalog must be a list")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []

    for item in catalog:
        if not isinstance(item, dict):
            continue

        service_id = normalize_service_id(str(item.get("id", "")))
        if not service_id or service_id in seen:
            continue

        seen.add(service_id)
        next_item = dict(item)
        next_item["id"] = service_id
        normalized.append(next_item)

    backup_service_catalog_file()
    write_json_atomic(SERVICE_CATALOG_FILE, normalized)


def normalize_service_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized[:80]


def candidate_service_id(code_or_id: str) -> str:
    normalized = normalize_service_id(code_or_id)
    return normalize_service_id(SERVICE_CANDIDATE_ID_OVERRIDES.get(normalized, normalized))


def read_removed_service_catalog() -> dict[str, Any]:
    payload = read_json(SERVICE_REMOVED_CATALOG_FILE, None)

    if not isinstance(payload, dict):
        payload = {}

    services = payload.get("services")
    if not isinstance(services, dict):
        services = {}

    normalized_services: dict[str, dict[str, Any]] = {}
    for raw_id, raw_entry in services.items():
        entry = raw_entry if isinstance(raw_entry, dict) else {"service": raw_entry}
        service = entry.get("service")

        if not isinstance(service, dict):
            continue

        service_id = normalize_service_id(str(service.get("id") or raw_id))
        if not service_id:
            continue

        next_entry = copy.deepcopy(entry)
        next_service = copy.deepcopy(service)
        next_service["id"] = service_id
        next_entry["service"] = next_service
        normalized_services[service_id] = next_entry

    return {
        "version": 1,
        "services": normalized_services,
        "updated_at": payload.get("updated_at"),
    }


def write_removed_service_catalog(payload: dict[str, Any]) -> None:
    if not isinstance(payload.get("services"), dict):
        payload["services"] = {}

    payload["version"] = 1
    payload["updated_at"] = now_iso()
    write_json_atomic(SERVICE_REMOVED_CATALOG_FILE, payload)


def service_source_code_from_providers(service: dict[str, Any]) -> str | None:
    discovery = service.get("discovery")
    if isinstance(discovery, dict):
        source_code = str(discovery.get("source_code", "")).strip().lower()
        if source_code:
            return source_code

    providers = service.get("providers", [])
    if not isinstance(providers, list):
        return None

    for provider in providers:
        if not isinstance(provider, dict):
            continue

        url = str(provider.get("url", "")).strip()
        if not url:
            continue

        parsed_path = urlparse(url).path.rstrip("/")
        if "/data/" in parsed_path:
            source_code = parsed_path.rsplit("/", 1)[-1].strip().lower()
            if source_code:
                return source_code

    return None


def service_source_url_from_providers(service: dict[str, Any]) -> str | None:
    discovery = service.get("discovery")
    if isinstance(discovery, dict):
        source_url = str(discovery.get("source_url", "")).strip()
        if source_url:
            return source_url

    providers = service.get("providers", [])
    if not isinstance(providers, list):
        return None

    for provider in providers:
        if not isinstance(provider, dict):
            continue

        url = str(provider.get("url", "")).strip()
        if url:
            return url

    return None


def service_catalog_item_candidate(
    service: dict[str, Any],
    entry: dict[str, Any] | None = None,
    *,
    restorable: bool = True,
) -> dict[str, Any] | None:
    entry = entry or {}
    service_id = normalize_service_id(str(service.get("id", "")))
    if not service_id:
        return None

    category = str(service.get("category") or "service")
    source_code = service_source_code_from_providers(service) or service_id
    source_url = service_source_url_from_providers(service) or ""
    discovery = service.get("discovery")
    discovery_risk = discovery.get("risk") if isinstance(discovery, dict) else None
    risk = discovery_risk if isinstance(discovery_risk, dict) else service_candidate_risk(source_code, category)
    providers = service.get("providers", [])
    provider_count = len(providers) if isinstance(providers, list) else 0
    first_provider = copy.deepcopy(providers[0]) if provider_count and isinstance(providers[0], dict) else None
    base_score = service_candidate_score(source_code, category, str(risk.get("level", "targeted")))

    return {
        "id": service_id,
        "title": service.get("title") or candidate_title_from_code(source_code),
        "description": service.get("description") or (
            "Исключённый сервисный модуль. Можно вернуть в каталог без потери провайдеров."
            if restorable
            else "Сервисный модуль уже находится в каталоге."
        ),
        "category": category,
        "source_kind": "restorable" if restorable else "catalog",
        "source_name": "исключённые модули" if restorable else "каталог сервисных модулей",
        "source_code": source_code,
        "source_url": source_url,
        "provider": first_provider,
        "providers_count": provider_count,
        "risk": risk,
        "score": base_score + (2000 if restorable else 0),
        "existing": not restorable,
        "existing_aliases": [],
        "importable": restorable,
        "restorable": restorable,
        "removed_at": entry.get("removed_at"),
        "enabled_was": bool(entry.get("enabled_was", False)),
    }


def restorable_service_candidate(service: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
    return service_catalog_item_candidate(service, entry, restorable=True)


def candidate_title_from_code(code: str) -> str:
    if code in SERVICE_CANDIDATE_TITLE_OVERRIDES:
        return SERVICE_CANDIDATE_TITLE_OVERRIDES[code]

    words = [part for part in re.split(r"[-_]+", code) if part]
    if not words:
        return code

    return " ".join(word.upper() if len(word) <= 3 else word.capitalize() for word in words)


def service_candidate_known_id_aliases(service_id: str, code: str) -> set[str]:
    aliases = {normalize_service_id(service_id), normalize_service_id(code)}

    for key in tuple(aliases):
        aliases.update(SERVICE_CANDIDATE_EXISTING_ID_ALIASES.get(key, set()))

    return {normalized for item in aliases if (normalized := normalize_service_id(item))}


def service_candidate_keyword_matches(value: str, keyword: str, tokens: set[str]) -> bool:
    if keyword in SERVICE_CATEGORY_TOKEN_ONLY_KEYWORDS:
        return keyword in tokens or value == keyword

    if keyword in tokens or value == keyword:
        return True

    return len(keyword) >= 4 and keyword in value


def infer_service_candidate_category(code: str) -> str:
    value = code.lower()
    tokens = {part for part in re.split(r"[^a-z0-9]+", value) if part}

    for category, keywords in SERVICE_CATEGORY_KEYWORDS:
        if any(service_candidate_keyword_matches(value, keyword, tokens) for keyword in keywords):
            return category

    if value.startswith("category-"):
        return "platform"

    return "service"


def service_candidate_risk(code: str, category: str) -> dict[str, str]:
    value = code.lower()

    if category in {"cloud", "cdn"} or value in {"google", "microsoft", "apple", "aws", "azure", "cloudflare", "fastly", "cloudfront"}:
        return {
            "level": "infrastructure",
            "label": "инфраструктура",
            "tone": "warn",
            "reason": "Может затронуть CDN, облака или крупную платформу, а не один конкретный сайт.",
        }

    if value.startswith("category-") or value.startswith("geolocation-") or "ads" in value:
        return {
            "level": "large",
            "label": "широкий",
            "tone": "warn",
            "reason": "Категорийный список может включать много несвязанных доменов.",
        }

    if category == "adult":
        return {
            "level": "sensitive",
            "label": "18+",
            "tone": "warn",
            "reason": "Чувствительная категория. По умолчанию безопаснее включать только явно нужные сервисы.",
        }

    return {
        "level": "targeted",
        "label": "точный",
        "tone": "ok",
        "reason": "Похоже на конкретный сервис или бренд из Geosite.",
    }


def service_candidate_score(code: str, category: str, risk_level: str) -> int:
    score = SERVICE_CANDIDATE_PRIORITY.get(code, 100)

    if category in {"ai", "dev", "messenger", "social", "video"}:
        score += 40

    if risk_level == "targeted":
        score += 20
    elif risk_level == "infrastructure":
        score -= 20
    elif risk_level == "large":
        score -= 60

    return score


def service_candidate_max_domains(risk_level: str, category: str) -> int:
    if risk_level == "large":
        return 60
    if risk_level == "infrastructure":
        return 100
    if category == "adult":
        return 80
    return 100


def fallback_service_candidate_entries() -> list[dict[str, Any]]:
    return [
        {
            "code": code,
            "path": code,
            "source_url": urljoin(V2FLY_DLC_RAW_BASE_URL, code),
            "source": "fallback",
        }
        for code in SERVICE_CANDIDATE_FALLBACK_CODES
    ]


def fetch_v2fly_service_candidate_entries() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = {
        "name": "v2fly/domain-list-community",
        "api_url": V2FLY_DLC_TREE_API_URL,
        "fallback_api_url": V2FLY_DLC_DATA_API_URL,
        "ok": False,
        "error": None,
        "items_count": 0,
    }

    try:
        with httpx.Client(
            timeout=SERVICE_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "The333-BGP-service-catalog"},
        ) as client:
            response = client.get(V2FLY_DLC_TREE_API_URL)
            response.raise_for_status()
            payload = response.json()

        entries: list[dict[str, Any]] = []

        if isinstance(payload, dict) and isinstance(payload.get("tree"), list):
            for item in payload["tree"]:
                if not isinstance(item, dict):
                    continue

                path = str(item.get("path", "")).strip()
                if item.get("type") != "blob" or not path.startswith("data/"):
                    continue

                code = path.removeprefix("data/").strip().lower()
                if not code or "/" in code:
                    continue

                service_id = candidate_service_id(code)
                if not service_id:
                    continue

                entries.append(
                    {
                        "code": code,
                        "path": path,
                        "source_url": urljoin(V2FLY_DLC_RAW_BASE_URL, code),
                        "sha": item.get("sha"),
                        "source": "github-tree-api",
                    }
                )

        if not entries:
            with httpx.Client(
                timeout=SERVICE_CANDIDATE_DISCOVERY_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers={"User-Agent": "The333-BGP-service-catalog"},
            ) as client:
                response = client.get(V2FLY_DLC_DATA_API_URL)
                response.raise_for_status()
                payload = response.json()

            if not isinstance(payload, list):
                raise RuntimeError("GitHub contents API returned non-list payload")

            for item in payload:
                if not isinstance(item, dict):
                    continue

                if item.get("type") != "file":
                    continue

                code = str(item.get("name", "")).strip().lower()
                if not code:
                    continue

                service_id = candidate_service_id(code)
                if not service_id:
                    continue

                entries.append(
                    {
                        "code": code,
                        "path": str(item.get("path") or f"data/{code}"),
                        "source_url": str(item.get("download_url") or urljoin(V2FLY_DLC_RAW_BASE_URL, code)),
                        "sha": item.get("sha"),
                        "source": "github-contents-api",
                    }
                )

        meta["ok"] = True
        meta["items_count"] = len(entries)
        return entries, meta

    except Exception as e:
        meta["error"] = str(e)
        entries = fallback_service_candidate_entries()
        meta["items_count"] = len(entries)
        return entries, meta


def read_service_candidates_cache() -> dict[str, Any]:
    cache = read_json(SERVICE_CANDIDATES_FILE, None)

    if not isinstance(cache, dict):
        seed = read_json(SERVICE_CANDIDATES_SEED_FILE, None)
        if isinstance(seed, dict):
            cache = seed
        else:
            cache = {
                "version": 1,
                "auto": True,
                "auto_interval_seconds": SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS,
                "last_refresh": None,
                "candidates": [],
                "sources": [],
            }

    cache.setdefault("version", 1)
    cache.setdefault("auto", True)
    cache.setdefault("auto_interval_seconds", SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS)
    cache.setdefault("last_refresh", None)
    cache.setdefault("candidates", [])
    cache.setdefault("sources", [])

    if not isinstance(cache["candidates"], list):
        cache["candidates"] = []

    return cache


def write_service_candidates_cache(cache: dict[str, Any]) -> None:
    cache["version"] = 1
    cache["updated_at"] = now_iso()
    write_json_atomic(SERVICE_CANDIDATES_FILE, cache)


def service_catalog_source_fingerprints(catalog: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    source_codes: set[str] = set()
    source_urls: set[str] = set()

    for service in catalog:
        if not isinstance(service, dict):
            continue

        discovery = service.get("discovery")
        if isinstance(discovery, dict):
            source_code = str(discovery.get("source_code", "")).strip().lower()
            if source_code:
                source_codes.add(source_code)

            source_url = str(discovery.get("source_url", "")).strip()
            if source_url:
                source_urls.add(source_url)

        providers = service.get("providers", [])
        if not isinstance(providers, list):
            continue

        for provider in providers:
            if not isinstance(provider, dict):
                continue

            url = str(provider.get("url", "")).strip()
            if url:
                source_urls.add(url)
                parsed_path = urlparse(url).path.rstrip("/")
                if "/data/" in parsed_path:
                    source_code = parsed_path.rsplit("/", 1)[-1].strip().lower()
                    if source_code:
                        source_codes.add(source_code)

    return source_codes, source_urls


def build_service_candidate_item(
    entry: dict[str, Any],
    known_ids: set[str],
    known_source_codes: set[str],
    known_source_urls: set[str],
) -> dict[str, Any] | None:
    code = str(entry.get("code", "")).strip().lower()
    source_url = str(entry.get("source_url", "")).strip()
    service_id = candidate_service_id(code)

    if not code or not source_url or not service_id:
        return None

    category = infer_service_candidate_category(code)
    risk = service_candidate_risk(code, category)
    max_domains = service_candidate_max_domains(risk["level"], category)
    title = candidate_title_from_code(code)
    known_id_aliases = service_candidate_known_id_aliases(service_id, code)
    existing = bool(known_id_aliases & known_ids) or code in known_source_codes or source_url in known_source_urls

    return {
        "id": service_id,
        "title": title,
        "description": f"Автоматически найдено в V2Fly Geosite: geosite:{code}.",
        "category": category,
        "source_kind": "geosite",
        "source_name": "v2fly/domain-list-community",
        "source_code": code,
        "source_url": source_url,
        "provider": {
            "type": "geosite_plain",
            "name": f"v2fly-geosite-{service_id}",
            "url": source_url,
            "max_domains": max_domains,
        },
        "risk": risk,
        "score": service_candidate_score(code, category, risk["level"]),
        "existing": existing,
        "existing_aliases": sorted(known_id_aliases & known_ids),
        "importable": not existing,
        "sha": entry.get("sha"),
    }


def refresh_service_candidates(trigger: str = "manual") -> dict[str, Any]:
    started = time.time()
    catalog = read_service_catalog()
    known_ids = {normalize_service_id(str(item.get("id", ""))) for item in catalog if isinstance(item, dict)}
    known_source_codes, known_source_urls = service_catalog_source_fingerprints(catalog)
    entries, source_meta = fetch_v2fly_service_candidate_entries()
    candidates: list[dict[str, Any]] = []

    for entry in entries:
        candidate = build_service_candidate_item(entry, known_ids, known_source_codes, known_source_urls)
        if candidate is None:
            continue
        candidates.append(candidate)

    removed_catalog = read_removed_service_catalog()
    archived_candidates: list[dict[str, Any]] = []

    for entry in removed_catalog.get("services", {}).values():
        if not isinstance(entry, dict):
            continue

        service = entry.get("service")
        if not isinstance(service, dict):
            continue

        service_id = normalize_service_id(str(service.get("id", "")))
        if not service_id or service_id in known_ids:
            continue

        candidate = restorable_service_candidate(service, entry)
        if candidate is not None:
            archived_candidates.append(candidate)

    for archived in archived_candidates:
        archived_id = normalize_service_id(str(archived.get("id", "")))
        archived_code = str(archived.get("source_code", "")).strip().lower()
        archived_aliases = service_candidate_known_id_aliases(archived_id, archived_code)

        candidates = [
            item
            for item in candidates
            if normalize_service_id(str(item.get("id", ""))) not in archived_aliases
            and str(item.get("source_code", "")).strip().lower() != archived_code
        ]
        candidates.append(archived)

    for service in catalog:
        if not isinstance(service, dict):
            continue

        service_id = normalize_service_id(str(service.get("id", "")))
        if not service_id:
            continue

        source_code = service_source_code_from_providers(service) or service_id
        service_aliases = service_candidate_known_id_aliases(service_id, source_code)
        already_represented = any(
            normalize_service_id(str(item.get("id", ""))) in service_aliases
            or str(item.get("source_code", "")).strip().lower() == source_code
            for item in candidates
        )

        if already_represented:
            continue

        catalog_candidate = service_catalog_item_candidate(service, restorable=False)
        if catalog_candidate is not None:
            candidates.append(catalog_candidate)

    candidates.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("title", item.get("id", ""))).lower()))

    cache = read_service_candidates_cache()
    cache.update(
        {
            "ok": bool(source_meta.get("ok", False)),
            "trigger": trigger,
            "last_refresh": now_iso(),
            "auto_interval_seconds": SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS,
            "sources": [source_meta],
            "candidates": candidates,
            "total_count": len(candidates),
            "importable_count": sum(1 for item in candidates if item.get("importable")),
            "existing_count": sum(1 for item in candidates if item.get("existing")),
            "duration_seconds": round(time.time() - started, 3),
            "time": now_iso(),
        }
    )
    write_service_candidates_cache(cache)
    return cache


def service_candidates_response(refresh: bool = False) -> dict[str, Any]:
    cache = read_service_candidates_cache()

    if refresh or not cache.get("candidates"):
        cache = refresh_service_candidates("manual" if refresh else "cold_start")

    return {
        "ok": bool(cache.get("ok", True)),
        "auto": bool(cache.get("auto", True)),
        "auto_interval_seconds": cache.get("auto_interval_seconds", SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS),
        "last_refresh": cache.get("last_refresh"),
        "sources": cache.get("sources", []),
        "candidates": cache.get("candidates", []),
        "total_count": cache.get("total_count", len(cache.get("candidates", []))),
        "importable_count": cache.get("importable_count"),
        "existing_count": cache.get("existing_count"),
        "duration_seconds": cache.get("duration_seconds"),
        "updated_at": cache.get("updated_at"),
        "time": now_iso(),
    }


def refresh_service_candidates_if_due() -> dict[str, Any] | None:
    cache = read_service_candidates_cache()

    if not bool(cache.get("auto", True)):
        return None

    last_refresh = parse_iso_ts(cache.get("last_refresh"))
    interval = int(cache.get("auto_interval_seconds") or SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS)

    if last_refresh is not None and time.time() - last_refresh < max(3600, interval):
        return None

    return refresh_service_candidates("auto")


def import_service_candidates(candidate_ids: list[str], enabled: bool = True) -> dict[str, Any]:
    if not candidate_ids:
        raise HTTPException(status_code=400, detail="ids must not be empty")

    catalog = read_service_catalog()
    known_ids = {normalize_service_id(str(item.get("id", ""))) for item in catalog if isinstance(item, dict)}
    removed_catalog = read_removed_service_catalog()
    removed_services = removed_catalog.setdefault("services", {})
    candidates_payload = service_candidates_response(False)
    candidates_by_id = {
        str(item.get("id", "")): item
        for item in candidates_payload.get("candidates", [])
        if isinstance(item, dict)
    }

    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    restored_ids: set[str] = set()

    for raw_id in candidate_ids:
        candidate_id = candidate_service_id(str(raw_id))

        if not candidate_id:
            skipped.append({"id": raw_id, "reason": "empty id"})
            continue

        archived_entry = removed_services.get(candidate_id)
        if isinstance(archived_entry, dict) and isinstance(archived_entry.get("service"), dict):
            if candidate_id in known_ids:
                skipped.append({"id": candidate_id, "reason": "already exists"})
                continue

            service_item = copy.deepcopy(archived_entry["service"])
            service_item["id"] = candidate_id
            catalog.append(service_item)
            known_ids.add(candidate_id)
            restored_ids.add(candidate_id)
            imported.append(
                {
                    "id": candidate_id,
                    "title": service_item.get("title") or candidate_id,
                    "category": service_item.get("category") or "service",
                    "enabled": enabled,
                    "restored": True,
                }
            )
            continue

        candidate = candidates_by_id.get(candidate_id)
        if not candidate:
            skipped.append({"id": candidate_id, "reason": "candidate not found"})
            continue

        known_id_aliases = service_candidate_known_id_aliases(candidate_id, str(candidate.get("source_code", "")))
        if candidate_id in known_ids or known_id_aliases & known_ids or candidate.get("existing"):
            skipped.append({"id": candidate_id, "reason": "already exists"})
            continue

        provider = candidate.get("provider")
        if not isinstance(provider, dict):
            skipped.append({"id": candidate_id, "reason": "candidate provider is invalid"})
            continue

        service_item = {
            "id": candidate_id,
            "title": candidate.get("title") or candidate_id,
            "description": candidate.get("description") or f"Автоматически добавлено из {candidate.get('source_name')}.",
            "category": candidate.get("category") or "service",
            "auto_discovered": True,
            "discovery": {
                "source_name": candidate.get("source_name"),
                "source_kind": candidate.get("source_kind"),
                "source_code": candidate.get("source_code"),
                "source_url": candidate.get("source_url"),
                "risk": candidate.get("risk"),
                "imported_at": now_iso(),
            },
            "providers": [provider],
        }

        catalog.append(service_item)
        known_ids.add(candidate_id)
        imported.append(
            {
                "id": candidate_id,
                "title": service_item["title"],
                "category": service_item["category"],
                "enabled": enabled,
            }
        )

    if imported:
        write_service_catalog(catalog)

        if restored_ids:
            for service_id in restored_ids:
                removed_services.pop(service_id, None)
            write_removed_service_catalog(removed_catalog)

        state = read_service_state()
        state.setdefault("services", {})

        for item in imported:
            service_id = str(item["id"])
            state["services"].setdefault(service_id, {})
            state["services"][service_id]["enabled"] = enabled

        state["updated_at"] = now_iso()
        write_service_state(state)
        refresh_service_candidates("after_import")

    return {
        "ok": True,
        "imported": imported,
        "imported_count": len(imported),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "enabled": enabled,
        "catalog_count": len(read_service_catalog()),
        "time": now_iso(),
    }


def remove_services_from_community_profiles(service_ids: set[str]) -> int:
    if not service_ids:
        return 0

    config = read_community_profiles()
    changed_profiles = 0
    next_profiles: list[dict[str, Any]] = []

    for profile in config.get("profiles", []):
        if not isinstance(profile, dict):
            continue

        current_services = profile.get("services", [])
        if not isinstance(current_services, list):
            current_services = []

        next_services = [
            item
            for item in current_services
            if normalize_service_id(str(item)) not in service_ids
        ]

        if len(next_services) != len(current_services):
            changed_profiles += 1

        next_profile = dict(profile)
        next_profile["services"] = next_services
        next_profiles.append(next_profile)

    if changed_profiles:
        next_config = dict(config)
        next_config["profiles"] = next_profiles
        write_community_profiles(next_config)

    return changed_profiles


def remove_service_catalog_items(service_ids: list[str], auto_discovered_only: bool = False) -> dict[str, Any]:
    if not service_ids:
        raise HTTPException(status_code=400, detail="ids must not be empty")

    requested_ids = [normalize_service_id(str(item)) for item in service_ids]
    requested = {item for item in requested_ids if item}

    if not requested:
        raise HTTPException(status_code=400, detail="ids must contain at least one valid service id")

    catalog = read_service_catalog()
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    removed_service_items: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for service in catalog:
        if not isinstance(service, dict):
            continue

        service_id = normalize_service_id(str(service.get("id", "")))

        if service_id not in requested:
            kept.append(service)
            continue

        seen_ids.add(service_id)

        if auto_discovered_only and not bool(service.get("auto_discovered", False)):
            kept.append(service)
            skipped.append(
                {
                    "id": service_id,
                    "reason": "not auto-discovered; built-in curated modules are protected",
                }
            )
            continue

        removed.append(
            {
                "id": service_id,
                "title": service.get("title") or service_id,
                "enabled_was": False,
            }
        )
        removed_service_items[service_id] = copy.deepcopy(service)

    for service_id in sorted(requested - seen_ids):
        skipped.append({"id": service_id, "reason": "not found"})

    if removed:
        removed_ids = {str(item["id"]) for item in removed}
        state = read_service_state()
        service_state = state.setdefault("services", {})
        removed_catalog = read_removed_service_catalog()
        removed_archive = removed_catalog.setdefault("services", {})

        for item in removed:
            service_id = str(item["id"])
            item["enabled_was"] = bool(service_state.get(service_id, {}).get("enabled", False))
            service_state.pop(service_id, None)
            service_item = removed_service_items.get(service_id)

            if service_item is not None:
                service_item["id"] = service_id
                removed_archive[service_id] = {
                    "service": service_item,
                    "removed_at": now_iso(),
                    "enabled_was": item["enabled_was"],
                }

        state["updated_at"] = now_iso()
        write_service_catalog(kept)
        write_service_state(state)
        write_removed_service_catalog(removed_catalog)
        changed_profiles = remove_services_from_community_profiles(removed_ids)
        refresh_service_candidates("after_remove")
    else:
        changed_profiles = 0

    return {
        "ok": True,
        "removed": removed,
        "removed_count": len(removed),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "auto_discovered_only": auto_discovered_only,
        "community_profiles_changed": changed_profiles,
        "catalog_count": len(read_service_catalog()),
        "time": now_iso(),
    }


def read_service_state() -> dict[str, Any]:
    ensure_service_state_file()
    state = read_json(SERVICE_STATE_FILE, {"version": 1, "services": {}})

    if not isinstance(state, dict):
        raise RuntimeError("service_state.json must be a JSON object")

    if not isinstance(state.get("services"), dict):
        state["services"] = {}

    return state


def write_service_state(state: dict[str, Any]) -> None:
    if "version" not in state:
        state["version"] = 1

    if not isinstance(state.get("services"), dict):
        state["services"] = {}

    write_json_atomic(SERVICE_STATE_FILE, state)


def read_service_dns_cache() -> dict[str, Any]:
    cache = read_json(SERVICE_DNS_CACHE_FILE, {"version": 1, "domains": {}})

    if not isinstance(cache, dict):
        cache = {"version": 1, "domains": {}}

    if not isinstance(cache.get("domains"), dict):
        cache["domains"] = {}

    return cache


def write_service_dns_cache(cache: dict[str, Any]) -> None:
    cache["version"] = 1
    cache["updated_at"] = now_iso()
    write_json_atomic(SERVICE_DNS_CACHE_FILE, cache)


def default_service_source_refresh_state() -> dict[str, Any]:
    return {
        "version": 1,
        "sources": {
            kind: {
                "auto": True,
                "last_refresh": None,
                "last_status": None,
            }
            for kind in SERVICE_SOURCE_KINDS
        },
        "updated_at": now_iso(),
    }


def read_service_source_refresh_state() -> dict[str, Any]:
    state = read_json(SERVICE_SOURCE_REFRESH_FILE, None)

    if not isinstance(state, dict):
        state = default_service_source_refresh_state()

    sources = state.setdefault("sources", {})

    if not isinstance(sources, dict):
        sources = {}
        state["sources"] = sources

    for kind in SERVICE_SOURCE_KINDS:
        item = sources.setdefault(kind, {})

        if not isinstance(item, dict):
            item = {}
            sources[kind] = item

        item.setdefault("auto", True)
        item.setdefault("last_refresh", None)
        item.setdefault("last_status", None)

    state["version"] = 1
    return state


def write_service_source_refresh_state(state: dict[str, Any]) -> None:
    state["version"] = 1
    state["updated_at"] = now_iso()
    write_json_atomic(SERVICE_SOURCE_REFRESH_FILE, state)


def service_source_refresh_summary() -> dict[str, Any]:
    state = read_service_source_refresh_state()
    sources = state.get("sources", {})
    result_sources: dict[str, Any] = {}

    for kind, meta in SERVICE_SOURCE_KINDS.items():
        item = sources.get(kind, {})
        if not isinstance(item, dict):
            item = {}

        result_sources[kind] = {
            "kind": kind,
            "label": meta["label"],
            "description": meta["description"],
            "provider_types": meta["provider_types"],
            "auto": bool(item.get("auto", True)),
            "last_refresh": item.get("last_refresh"),
            "last_status": item.get("last_status"),
        }

    return {
        "ok": True,
        "sources": result_sources,
        "updated_at": state.get("updated_at"),
        "time": now_iso(),
    }


def service_provider_matches_refresh_kind(provider: dict[str, Any], kind: str) -> bool:
    meta = SERVICE_SOURCE_KINDS.get(kind)
    if not meta:
        return False

    provider_type = str(provider.get("type", "")).strip().lower()
    return provider_type in set(meta["provider_types"])


def refresh_service_source_kind(kind: str, enabled_only: bool = True, trigger: str = "manual") -> dict[str, Any]:
    if kind not in SERVICE_SOURCE_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown service source kind: {kind}")

    started = time.time()
    catalog = read_service_catalog()
    service_state = read_service_state().get("services", {})
    source_meta = SERVICE_SOURCE_KINDS[kind]
    provider_stats: list[dict[str, Any]] = []
    services_checked = 0
    providers_checked = 0
    accepted = 0
    ignored = 0
    errors: list[str] = []
    warnings_count = 0
    total_bytes = 0
    source_versions: list[dict[str, Any]] = []

    for service in catalog:
        if not isinstance(service, dict):
            continue

        service_id = str(service.get("id", "")).strip()
        enabled = bool(service_state.get(service_id, {}).get("enabled", False))

        if enabled_only and not enabled:
            continue

        providers = service.get("providers", [])
        if not isinstance(providers, list):
            continue

        matched_providers = [
            provider
            for provider in providers
            if isinstance(provider, dict) and service_provider_matches_refresh_kind(provider, kind)
        ]

        if not matched_providers:
            continue

        services_checked += 1

        for provider in matched_providers:
            providers_checked += 1
            _, provider_stat = collect_service_provider(provider)
            provider_stat["service_id"] = service_id
            provider_stat["service_title"] = service.get("title", service_id)
            provider_stats.append(provider_stat)

            accepted += int(provider_stat.get("accepted", 0) or 0)
            ignored += int(provider_stat.get("ignored", 0) or 0)
            warnings_count += int(provider_stat.get("warnings_count", 0) or 0)
            source_data = provider_stat.get("source", {})

            if isinstance(source_data, dict):
                source_bytes = int(source_data.get("bytes", 0) or 0)
                total_bytes += source_bytes
                source_versions.append(
                    {
                        "service_id": service_id,
                        "provider": provider_stat.get("name"),
                        "type": provider_stat.get("type"),
                        "source_url": source_data.get("source_url"),
                        "source_path": source_data.get("source_path"),
                        "bytes": source_bytes,
                        "sync_token": provider_stat.get("sync_token"),
                        "creation_time": provider_stat.get("creation_time"),
                        "line_count": provider_stat.get("line_count"),
                        "parsed_prefix_count": provider_stat.get("parsed_prefix_count"),
                        "parsed_domain_count": provider_stat.get("parsed_domain_count"),
                        "error": source_data.get("error") or provider_stat.get("error"),
                    }
                )

            if provider_stat.get("error"):
                errors.append(f"{service_id}/{provider_stat.get('name')}: {provider_stat.get('error')}")

    ok = len(errors) == 0
    result = {
        "ok": ok,
        "kind": kind,
        "label": source_meta["label"],
        "description": source_meta["description"],
        "trigger": trigger,
        "would_apply": False,
        "enabled_only": enabled_only,
        "services_checked": services_checked,
        "providers_checked": providers_checked,
        "accepted": accepted,
        "ignored": ignored,
        "errors": errors[:20],
        "errors_count": len(errors),
        "warnings_count": warnings_count,
        "total_bytes": total_bytes,
        "source_versions_count": len(source_versions),
        "source_versions": source_versions[:50],
        "providers": provider_stats,
        "duration_seconds": round(time.time() - started, 3),
        "time": now_iso(),
    }

    state = read_service_source_refresh_state()
    state.setdefault("sources", {}).setdefault(kind, {})
    state["sources"][kind]["last_refresh"] = result["time"]
    state["sources"][kind]["last_status"] = {
        key: value
        for key, value in result.items()
        if key not in ("providers",)
    }
    write_service_source_refresh_state(state)

    return result


def refresh_auto_service_sources() -> dict[str, Any]:
    state = read_service_source_refresh_state()
    results: dict[str, Any] = {}

    for kind in SERVICE_SOURCE_KINDS:
        item = state.get("sources", {}).get(kind, {})
        if isinstance(item, dict) and not bool(item.get("auto", True)):
            continue

        results[kind] = refresh_service_source_kind(kind, enabled_only=True, trigger="auto")

    return {
        "ok": all(bool(item.get("ok", False)) for item in results.values()) if results else True,
        "results": results,
        "time": now_iso(),
    }


def parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def dns_resolve_ipv4(domain: str) -> tuple[set[ipaddress.IPv4Network], dict[str, Any]]:
    import socket

    domain = domain.strip().lower()
    now_ts = time.time()
    now_value = now_iso()

    stat = {
        "domain": domain,
        "ips": [],
        "current_ips": [],
        "stale_ips": [],
        "accepted": 0,
        "ignored": 0,
        "cache_hit": False,
        "cache_grace_seconds": SERVICE_DNS_CACHE_GRACE_SECONDS,
        "resolve_retries": SERVICE_DNS_RESOLVE_RETRIES,
        "resolve_attempts": 0,
        "resolve_errors": [],
        "error": None,
        "warning": None,
    }

    cache = read_service_dns_cache()
    domain_cache = cache["domains"].get(domain, {})

    if not isinstance(domain_cache, dict):
        domain_cache = {}

    cached_ips = domain_cache.get("ips", {})
    if not isinstance(cached_ips, dict):
        cached_ips = {}

    current_ips: set[str] = set()
    resolve_ok = False

    max_attempts = max(1, SERVICE_DNS_RESOLVE_RETRIES)

    for attempt in range(1, max_attempts + 1):
        stat["resolve_attempts"] = attempt

        try:
            results = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
            current_ips = {item[4][0] for item in results}
            resolve_ok = True
            stat["current_ips"] = sorted(current_ips)
            break

        except Exception as e:
            error_text = str(e)
            stat["resolve_errors"].append(error_text)

            if attempt < max_attempts:
                time.sleep(SERVICE_DNS_RESOLVE_RETRY_DELAY_SECONDS)
                continue

            stat["warning"] = error_text

    # Update current DNS answers immediately.
    if resolve_ok:
        for ip in current_ips:
            item = cached_ips.get(ip, {})
            if not isinstance(item, dict):
                item = {}

            if not item.get("first_seen"):
                item["first_seen"] = now_value

            item["last_seen"] = now_value
            item["last_status"] = "current"
            cached_ips[ip] = item

        # Mark disappeared IPs as stale but keep them during grace period.
        for ip, item in list(cached_ips.items()):
            if ip in current_ips:
                continue

            if not isinstance(item, dict):
                item = {}

            if item.get("last_status") != "stale":
                item["stale_since"] = now_value

            item["last_status"] = "stale"
            cached_ips[ip] = item

    active_ips: set[str] = set()
    stale_ips: set[str] = set()

    for ip, item in list(cached_ips.items()):
        if not isinstance(item, dict):
            cached_ips.pop(ip, None)
            continue

        last_seen_ts = parse_iso_ts(item.get("last_seen"))
        stale_since_ts = parse_iso_ts(item.get("stale_since"))

        is_current = ip in current_ips
        within_grace = False

        if not is_current and stale_since_ts is not None:
            within_grace = (now_ts - stale_since_ts) <= SERVICE_DNS_CACHE_GRACE_SECONDS

        if is_current or within_grace:
            active_ips.add(ip)

            if not is_current:
                stale_ips.add(ip)
        else:
            cached_ips.pop(ip, None)

    domain_cache["ips"] = cached_ips
    domain_cache["last_resolve_at"] = now_value
    domain_cache["last_resolve_ok"] = resolve_ok
    domain_cache["last_error"] = stat["error"]
    domain_cache["last_warning"] = stat["warning"]
    cache["domains"][domain] = domain_cache
    write_service_dns_cache(cache)

    networks: set[ipaddress.IPv4Network] = set()

    for ip in sorted(active_ips):
        net = parse_prefix(f"{ip}/32")
        if net is None:
            stat["ignored"] += 1
            continue

        networks.add(net)

    stat["ips"] = sorted(active_ips)
    stat["stale_ips"] = sorted(stale_ips)
    stat["accepted"] = len(networks)
    stat["cache_hit"] = len(active_ips) > len(current_ips) or bool(active_ips and not resolve_ok)

    return networks, stat


def normalize_geosite_domain(value: str) -> str | None:
    value = (value or "").strip().lower()

    if not value:
        return None

    # Remove inline comments and attributes.
    value = value.split("#", 1)[0].strip()
    if not value:
        return None

    token = value.split()[0].strip()
    if not token:
        return None

    ignored_prefixes = (
        "regexp:",
        "include:",
        "keyword:",
        "ext:",
        "geosite:",
        "geoip:",
    )

    if token.startswith(ignored_prefixes):
        return None

    if token.startswith("full:"):
        token = token.removeprefix("full:").strip()
    elif token.startswith("domain:"):
        token = token.removeprefix("domain:").strip()
    elif ":" in token:
        return None

    token = token.strip(".")
    token = token.removeprefix("*.")

    if not token:
        return None

    if "/" in token or "\\" in token or "*" in token:
        return None

    labels = token.split(".")

    if len(labels) < 2:
        return None

    for label in labels:
        if not label:
            return None

        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-")
        if any(ch not in allowed for ch in label):
            return None

        if label.startswith("-") or label.endswith("-"):
            return None

    return token


def read_geosite_plain_text(provider: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    import httpx

    url = str(provider.get("url", "")).strip()
    local_path = str(provider.get("path", "")).strip()

    meta = {
        "source_url": url or None,
        "source_path": local_path or None,
        "bytes": 0,
        "error": None,
    }

    try:
        if url:
            with httpx.Client(
                timeout=SERVICE_GEOSITE_HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                text_value = response.text

            meta["bytes"] = len(text_value.encode("utf-8", errors="ignore"))
            return text_value, meta

        if local_path:
            path = Path(local_path)

            if not path.is_absolute():
                path = CONFIG_DIR / local_path

            text_value = path.read_text(encoding="utf-8")
            meta["bytes"] = len(text_value.encode("utf-8", errors="ignore"))
            return text_value, meta

        meta["error"] = "url or path is required"
        return "", meta

    except Exception as e:
        meta["error"] = str(e)
        return "", meta


def parse_geosite_plain_domains(text_value: str) -> dict[str, Any]:
    lines = text_value.splitlines()
    domains: list[str] = []
    includes: list[str] = []
    ignored_lines = 0
    ignored_samples: list[str] = []

    seen: set[str] = set()
    seen_includes: set[str] = set()

    for raw_line in lines:
        stripped = raw_line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        without_comment = stripped.split("#", 1)[0].strip()
        token = without_comment.split()[0].strip().lower() if without_comment else ""

        if token.startswith("include:"):
            include_name = token.removeprefix("include:").strip().strip("/")
            if include_name and include_name not in seen_includes and "/" not in include_name and "\\" not in include_name:
                seen_includes.add(include_name)
                includes.append(include_name)
            else:
                ignored_lines += 1
                if len(ignored_samples) < 20:
                    ignored_samples.append(stripped)
            continue

        domain = normalize_geosite_domain(stripped)

        if domain is None:
            ignored_lines += 1
            if len(ignored_samples) < 20:
                ignored_samples.append(stripped)
            continue

        if domain in seen:
            continue

        seen.add(domain)
        domains.append(domain)

    return {
        "line_count": len(lines),
        "domain_count": len(domains),
        "include_count": len(includes),
        "ignored_lines": ignored_lines,
        "ignored_samples": ignored_samples,
        "domains": domains,
        "includes": includes,
    }


def geosite_include_url(base_url: str, include_name: str) -> str:
    if not base_url:
        return ""

    normalized_name = include_name.strip().strip("/")
    if not normalized_name:
        return ""

    return urljoin(base_url.rsplit("/", 1)[0] + "/", normalized_name)


def collect_geosite_plain_domains(
    text_value: str,
    source_url: str,
    provider: dict[str, Any],
) -> dict[str, Any]:
    import httpx

    visited_urls: set[str] = set()
    domains: list[str] = []
    seen_domains: set[str] = set()
    include_sources: list[dict[str, Any]] = []
    ignored_lines = 0
    ignored_samples: list[str] = []
    line_count = 0
    include_count = 0
    max_depth = max(0, SERVICE_GEOSITE_INCLUDE_MAX_DEPTH)
    max_includes = max(0, SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER)

    def add_domains(items: list[str]) -> None:
        for domain in items:
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            domains.append(domain)

    def visit(current_text: str, current_url: str, depth: int) -> None:
        nonlocal ignored_lines, line_count, include_count

        parsed = parse_geosite_plain_domains(current_text)
        line_count += int(parsed["line_count"])
        ignored_lines += int(parsed["ignored_lines"])
        ignored_samples.extend(str(item) for item in parsed["ignored_samples"] if len(ignored_samples) < 20)
        add_domains(parsed["domains"])

        if depth >= max_depth or include_count >= max_includes:
            return

        for include_name in parsed["includes"]:
            if include_count >= max_includes:
                return

            include_url = geosite_include_url(current_url, include_name)
            if not include_url or include_url in visited_urls:
                continue

            visited_urls.add(include_url)
            include_count += 1

            include_meta = {
                "name": include_name,
                "url": include_url,
                "bytes": 0,
                "error": None,
            }

            try:
                with httpx.Client(
                    timeout=SERVICE_GEOSITE_HTTP_TIMEOUT_SECONDS,
                    follow_redirects=True,
                ) as client:
                    response = client.get(include_url)
                    response.raise_for_status()
                    include_text = response.text

                include_meta["bytes"] = len(include_text.encode("utf-8", errors="ignore"))
                include_sources.append(include_meta)
                visit(include_text, include_url, depth + 1)

            except Exception as e:
                include_meta["error"] = str(e)
                include_sources.append(include_meta)

    visit(text_value, source_url, 0)

    return {
        "line_count": line_count,
        "domain_count": len(domains),
        "ignored_lines": ignored_lines,
        "ignored_samples": ignored_samples[:20],
        "domains": domains,
        "include_count": include_count,
        "include_sources": include_sources,
        "include_max_depth": max_depth,
        "include_max_count": max_includes,
    }


def collect_geosite_plain_provider(provider: dict[str, Any]) -> tuple[set[ipaddress.IPv4Network], dict[str, Any]]:
    provider_name = str(provider.get("name", "geosite_plain"))
    requested_max_domains = int(provider.get("max_domains", SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER))
    max_domains = max(0, min(requested_max_domains, SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER))

    stat: dict[str, Any] = {
        "name": provider_name,
        "type": "geosite_plain",
        "accepted": 0,
        "ignored": 0,
        "error": None,
        "source": {},
        "line_count": 0,
        "parsed_domain_count": 0,
        "include_count": 0,
        "include_sources": [],
        "include_max_depth": SERVICE_GEOSITE_INCLUDE_MAX_DEPTH,
        "include_max_count": SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER,
        "ignored_lines": 0,
        "ignored_samples": [],
        "max_domains": max_domains,
        "resolve_delay_seconds": SERVICE_DNS_RESOLVE_DELAY_SECONDS,
        "domain_stats": [],
    }

    text_value, source_meta = read_geosite_plain_text(provider)
    stat["source"] = source_meta

    if source_meta.get("error"):
        stat["error"] = source_meta["error"]
        return set(), stat

    parsed = collect_geosite_plain_domains(
        text_value,
        str(source_meta.get("source_url") or ""),
        provider,
    )

    domains = parsed["domains"]
    selected_domains = domains[:max_domains]

    stat["line_count"] = parsed["line_count"]
    stat["parsed_domain_count"] = parsed["domain_count"]
    stat["include_count"] = parsed["include_count"]
    stat["include_sources"] = parsed["include_sources"]
    stat["include_max_depth"] = parsed["include_max_depth"]
    stat["include_max_count"] = parsed["include_max_count"]
    stat["ignored_lines"] = parsed["ignored_lines"]
    stat["ignored_samples"] = parsed["ignored_samples"]
    stat["selected_domain_count"] = len(selected_domains)
    stat["truncated"] = len(domains) > len(selected_domains)

    collected: set[ipaddress.IPv4Network] = set()

    for domain in selected_domains:
        if SERVICE_DNS_RESOLVE_DELAY_SECONDS > 0:
            time.sleep(SERVICE_DNS_RESOLVE_DELAY_SECONDS)

        domain_prefixes, domain_stat = dns_resolve_ipv4(domain)
        collected.update(domain_prefixes)
        stat["domain_stats"].append(domain_stat)

    stat["accepted"] = len(collected)
    stat["ignored"] = int(stat["ignored_lines"])

    warnings = [
        f"{item.get('domain')}: {item.get('warning') or item.get('error')}"
        for item in stat["domain_stats"]
        if item.get("warning") or item.get("error")
    ]
    warnings.extend(
        f"include {item.get('name')}: {item.get('error')}"
        for item in stat["include_sources"]
        if item.get("error")
    )

    stat["warnings"] = warnings[:20]
    stat["warnings_count"] = len(warnings)

    return collected, stat


def resolve_service_by_id(service_id: str) -> dict[str, Any]:
    catalog = read_service_catalog()

    selected = None
    for service in catalog:
        if str(service.get("id", "")).strip() == service_id:
            selected = service
            break

    if selected is None:
        raise HTTPException(status_code=404, detail=f"service not found: {service_id}")

    providers = selected.get("providers", [])

    if not isinstance(providers, list):
        raise RuntimeError("providers must be a list")

    collected: set[ipaddress.IPv4Network] = set()
    provider_stats: list[dict[str, Any]] = []
    provider_errors: list[str] = []

    for provider in providers:
        if not isinstance(provider, dict):
            provider_errors.append("provider must be an object")
            continue

        provider_prefixes, provider_stat = collect_service_provider(provider)
        collected.update(provider_prefixes)
        provider_stats.append(provider_stat)

        if provider_stat.get("error"):
            provider_errors.append(f"{provider_stat.get('name')}: {provider_stat.get('error')}")

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(collected)))
    else:
        final_networks = sorted(collected)

    prefixes = [str(item) for item in final_networks]

    return {
        "ok": True,
        "mode": "single_service_resolve_preview",
        "would_apply": False,
        "id": service_id,
        "title": selected.get("title", service_id),
        "enabled": read_service_state().get("services", {}).get(service_id, {}).get("enabled", False),
        "provider_count": len(providers),
        "final_count": len(prefixes),
        "prefixes": prefixes,
        "first_50": prefixes[:50],
        "last_50": prefixes[-50:],
        "providers": provider_stats,
        "errors": provider_errors,
        "time": now_iso(),
    }


def normalize_geoip_prefix_line(value: str) -> ipaddress.IPv4Network | None:
    value = (value or "").strip()

    if not value:
        return None

    value = value.split("#", 1)[0].strip()
    if not value:
        return None

    token = value.split()[0].strip().strip(",")

    # Common rule-list prefixes.
    for prefix in ("IP-CIDR,", "IP-CIDR6,", "IP-ASN,", "ip-cidr,", "ip-cidr6,", "ip-asn,"):
        if token.startswith(prefix):
            token = token.split(",", 1)[1].strip()
            break

    # Reject IPv6 and ASN for now.
    if ":" in token:
        return None

    if token.upper().startswith("AS"):
        return None

    try:
        if "/" not in token:
            token = f"{token}/32"

        network = ipaddress.ip_network(token, strict=False)

        if not isinstance(network, ipaddress.IPv4Network):
            return None

        return network

    except Exception:
        return None


def read_geoip_plain_text(provider: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    import httpx

    url = str(provider.get("url", "")).strip()
    local_path = str(provider.get("path", "")).strip()

    meta = {
        "source_url": url or None,
        "source_path": local_path or None,
        "bytes": 0,
        "error": None,
    }

    try:
        if url:
            with httpx.Client(
                timeout=SERVICE_GEOIP_HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                text_value = response.text

            meta["bytes"] = len(text_value.encode("utf-8", errors="ignore"))
            return text_value, meta

        if local_path:
            path = Path(local_path)

            if not path.is_absolute():
                path = CONFIG_DIR / local_path

            text_value = path.read_text(encoding="utf-8")
            meta["bytes"] = len(text_value.encode("utf-8", errors="ignore"))
            return text_value, meta

        meta["error"] = "url or path is required"
        return "", meta

    except Exception as e:
        meta["error"] = str(e)
        return "", meta


def parse_geoip_plain_prefixes(text_value: str) -> dict[str, Any]:
    lines = text_value.splitlines()
    networks: list[ipaddress.IPv4Network] = []
    ignored_lines = 0
    ignored_samples: list[str] = []

    seen: set[str] = set()

    for raw_line in lines:
        stripped = raw_line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        network = normalize_geoip_prefix_line(stripped)

        if network is None:
            ignored_lines += 1
            if len(ignored_samples) < 20:
                ignored_samples.append(stripped)
            continue

        key = str(network)

        if key in seen:
            continue

        seen.add(key)
        networks.append(network)

    return {
        "line_count": len(lines),
        "prefix_count": len(networks),
        "ignored_lines": ignored_lines,
        "ignored_samples": ignored_samples,
        "networks": networks,
    }


def collect_geoip_plain_provider(provider: dict[str, Any]) -> tuple[set[ipaddress.IPv4Network], dict[str, Any]]:
    provider_name = str(provider.get("name", "geoip_plain"))
    requested_max_prefixes = int(provider.get("max_prefixes", SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER))
    max_prefixes = max(0, min(requested_max_prefixes, SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER))

    stat: dict[str, Any] = {
        "name": provider_name,
        "type": "geoip_plain",
        "accepted": 0,
        "ignored": 0,
        "error": None,
        "warnings": [],
        "warnings_count": 0,
        "source": {},
        "line_count": 0,
        "parsed_prefix_count": 0,
        "selected_prefix_count": 0,
        "ignored_lines": 0,
        "ignored_samples": [],
        "max_prefixes": max_prefixes,
        "truncated": False,
        "first_20": [],
        "last_20": [],
    }

    text_value, source_meta = read_geoip_plain_text(provider)
    stat["source"] = source_meta

    if source_meta.get("error"):
        stat["error"] = source_meta["error"]
        return set(), stat

    parsed = parse_geoip_plain_prefixes(text_value)
    networks = parsed["networks"]
    selected_networks = networks[:max_prefixes]

    stat["line_count"] = parsed["line_count"]
    stat["parsed_prefix_count"] = parsed["prefix_count"]
    stat["selected_prefix_count"] = len(selected_networks)
    stat["ignored_lines"] = parsed["ignored_lines"]
    stat["ignored_samples"] = parsed["ignored_samples"]
    stat["truncated"] = len(networks) > len(selected_networks)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(selected_networks)))
    else:
        final_networks = sorted(selected_networks)

    collected = set(final_networks)

    prefixes = [str(item) for item in sorted(collected)]
    stat["accepted"] = len(collected)
    stat["ignored"] = int(stat["ignored_lines"])
    stat["first_20"] = prefixes[:20]
    stat["last_20"] = prefixes[-20:]

    if stat["truncated"]:
        stat["warnings"].append(
            f"prefix list truncated: selected {len(selected_networks)} of {len(networks)}"
        )

    stat["warnings_count"] = len(stat["warnings"])

    return collected, stat


def collect_ipranges_json_provider(provider: dict[str, Any]) -> tuple[set[ipaddress.IPv4Network], dict[str, Any]]:
    provider_name = str(provider.get("name", "ipranges_json"))
    requested_max_prefixes = int(provider.get("max_prefixes", SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER))
    max_prefixes = max(0, min(requested_max_prefixes, SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER))
    url = str(provider.get("url", "")).strip()

    stat: dict[str, Any] = {
        "name": provider_name,
        "type": "ipranges_json",
        "accepted": 0,
        "ignored": 0,
        "error": None,
        "warnings": [],
        "warnings_count": 0,
        "source": {
            "source_url": url or None,
            "bytes": 0,
            "error": None,
        },
        "line_count": 0,
        "parsed_prefix_count": 0,
        "selected_prefix_count": 0,
        "ignored_lines": 0,
        "ignored_samples": [],
        "max_prefixes": max_prefixes,
        "truncated": False,
        "sync_token": None,
        "creation_time": None,
        "first_20": [],
        "last_20": [],
    }

    if not url:
        stat["error"] = "url is required"
        stat["source"]["error"] = stat["error"]
        return set(), stat

    try:
        with httpx.Client(
            timeout=SERVICE_GEOIP_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            raw_body = response.text

        stat["source"]["bytes"] = len(raw_body.encode("utf-8", errors="ignore"))
        payload = response.json()
    except Exception as e:
        stat["error"] = str(e)
        stat["source"]["error"] = stat["error"]
        return set(), stat

    prefixes = payload.get("prefixes") if isinstance(payload, dict) else None
    if not isinstance(prefixes, list):
        stat["error"] = "JSON payload must contain prefixes array"
        return set(), stat

    stat["sync_token"] = payload.get("syncToken")
    stat["creation_time"] = payload.get("creationTime")
    stat["line_count"] = len(prefixes)

    networks: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()

    for item in prefixes:
        if not isinstance(item, dict):
            stat["ignored_lines"] += 1
            if len(stat["ignored_samples"]) < 20:
                stat["ignored_samples"].append(str(item))
            continue

        value = str(item.get("ipv4Prefix", "")).strip()
        if not value:
            stat["ignored_lines"] += 1
            continue

        try:
            network = ipaddress.ip_network(value, strict=False)
        except Exception:
            stat["ignored_lines"] += 1
            if len(stat["ignored_samples"]) < 20:
                stat["ignored_samples"].append(value)
            continue

        if not isinstance(network, ipaddress.IPv4Network):
            stat["ignored_lines"] += 1
            continue

        key = str(network)
        if key in seen:
            continue

        seen.add(key)
        networks.append(network)

    selected_networks = networks[:max_prefixes]

    stat["parsed_prefix_count"] = len(networks)
    stat["selected_prefix_count"] = len(selected_networks)
    stat["truncated"] = len(networks) > len(selected_networks)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(selected_networks)))
    else:
        final_networks = sorted(selected_networks)

    collected = set(final_networks)
    prefix_strings = [str(item) for item in sorted(collected)]

    stat["accepted"] = len(collected)
    stat["ignored"] = int(stat["ignored_lines"])
    stat["first_20"] = prefix_strings[:20]
    stat["last_20"] = prefix_strings[-20:]

    if stat["truncated"]:
        stat["warnings"].append(
            f"prefix list truncated: selected {len(selected_networks)} of {len(networks)}"
        )

    stat["warnings_count"] = len(stat["warnings"])

    return collected, stat


def collect_service_provider(provider: dict[str, Any]) -> tuple[set[ipaddress.IPv4Network], dict[str, Any]]:
    provider_type = str(provider.get("type", "")).strip().lower()
    provider_name = str(provider.get("name", provider_type or "unnamed"))

    stat: dict[str, Any] = {
        "name": provider_name,
        "type": provider_type,
        "accepted": 0,
        "ignored": 0,
        "error": None,
        "domain_stats": [],
    }

    collected: set[ipaddress.IPv4Network] = set()

    if provider_type == "static_prefixes":
        prefixes = provider.get("prefixes", [])

        if not isinstance(prefixes, list):
            stat["error"] = "prefixes must be a list"
            return collected, stat

        for item in prefixes:
            net = parse_prefix(str(item))
            if net is None:
                stat["ignored"] += 1
                continue

            collected.add(net)
            stat["accepted"] += 1

        return collected, stat

    if provider_type == "dns_domains":
        domains = provider.get("domains", [])

        if not isinstance(domains, list):
            stat["error"] = "domains must be a list"
            return collected, stat

        for item in domains:
            domain = str(item).strip().lower()
            if not domain:
                stat["ignored"] += 1
                continue

            domain_prefixes, domain_stat = dns_resolve_ipv4(domain)
            collected.update(domain_prefixes)
            stat["domain_stats"].append(domain_stat)

        stat["accepted"] = len(collected)
        stat["ignored"] += sum(int(item.get("ignored", 0)) for item in stat["domain_stats"])

        warnings = [
            f"{item.get('domain')}: {item.get('warning') or item.get('error')}"
            for item in stat["domain_stats"]
            if item.get("warning") or item.get("error")
        ]

        stat["warnings"] = warnings[:20]
        stat["warnings_count"] = len(warnings)

        return collected, stat

    if provider_type == "geosite_plain":
        return collect_geosite_plain_provider(provider)

    if provider_type == "geoip_plain":
        return collect_geoip_plain_provider(provider)

    if provider_type == "ipranges_json":
        return collect_ipranges_json_provider(provider)

    stat["error"] = f"unsupported provider type: {provider_type}"
    return collected, stat


def build_service_routes(enabled_only: bool = True) -> dict[str, Any]:
    catalog = read_service_catalog()
    state = read_service_state()

    service_state = state.get("services", {})
    collected: set[ipaddress.IPv4Network] = set()
    service_stats: list[dict[str, Any]] = []

    for service in catalog:
        service_id = str(service.get("id", "")).strip()
        enabled = bool(service_state.get(service_id, {}).get("enabled", False))

        service_stat: dict[str, Any] = {
            "id": service_id,
            "title": service.get("title", service_id),
            "category": service.get("category"),
            "enabled": enabled,
            "providers": [],
            "accepted": 0,
            "ignored": 0,
            "error": None,
            "selected": False,
        }

        if enabled_only and not enabled:
            service_stat["skipped"] = True
            service_stats.append(service_stat)
            continue

        service_prefixes: set[ipaddress.IPv4Network] = set()
        provider_errors: list[str] = []

        providers = service.get("providers", [])

        if not isinstance(providers, list):
            service_stat["error"] = "providers must be a list"
            service_stats.append(service_stat)
            continue

        for provider in providers:
            if not isinstance(provider, dict):
                provider_errors.append("provider must be an object")
                continue

            provider_prefixes, provider_stat = collect_service_provider(provider)
            service_prefixes.update(provider_prefixes)
            service_stat["providers"].append(provider_stat)

            if provider_stat.get("error"):
                provider_errors.append(f"{provider_stat.get('name')}: {provider_stat.get('error')}")

        service_stat["accepted"] = len(service_prefixes)
        service_stat["ignored"] = sum(int(item.get("ignored", 0)) for item in service_stat["providers"])
        service_stat["selected"] = enabled and len(service_prefixes) > 0

        if provider_errors:
            service_stat["error"] = "; ".join(provider_errors[:5])

        collected.update(service_prefixes)
        service_stats.append(service_stat)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(collected)))
    else:
        final_networks = sorted(collected)

    prefixes = [str(net) for net in final_networks]

    result = {
        "ok": True,
        "mode": "service_routes_dry_run",
        "would_apply": False,
        "enabled_only": enabled_only,
        "services_count": len(catalog),
        "enabled_count": sum(1 for item in service_stats if item.get("enabled")),
        "unique_before_aggregation": len(collected),
        "final_count": len(prefixes),
        "aggregate": AGGREGATE_PREFIXES,
        "prefixes": prefixes,
        "first_20": prefixes[:20],
        "last_20": prefixes[-20:],
        "service_stats": service_stats,
        "time": now_iso(),
    }

    write_json_atomic(SERVICE_CACHE_FILE, result)
    write_prefixes_file(SERVICE_ROUTES_FILE, prefixes)

    if prefixes:
        write_prefixes_file(SERVICE_LAST_GOOD_ROUTES_FILE, prefixes)

    return result


@app.get("/api/services")
async def api_services(_: str = Depends(require_auth)) -> JSONResponse:
    catalog = read_service_catalog()
    state = read_service_state()
    cache = read_json(SERVICE_CACHE_FILE, None)
    candidates_cache = read_service_candidates_cache()

    return JSONResponse(
        {
            "ok": True,
            "catalog": catalog,
            "state": state,
            "cache": cache,
            "source_refresh": service_source_refresh_summary(),
            "candidates_summary": {
                "auto": bool(candidates_cache.get("auto", True)),
                "last_refresh": candidates_cache.get("last_refresh"),
                "total_count": candidates_cache.get("total_count", len(candidates_cache.get("candidates", []))),
                "importable_count": candidates_cache.get("importable_count"),
                "existing_count": candidates_cache.get("existing_count"),
            },
            "time": now_iso(),
        }
    )


@app.get("/api/services/candidates")
async def api_services_candidates(refresh: bool = False, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(service_candidates_response, refresh))
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "service_candidates_failed",
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.post("/api/services/candidates/refresh/job")
async def api_services_candidates_refresh_job(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(
        start_background_job(
            kind="service_candidates_refresh",
            key="service_candidates_refresh:v2fly",
            title="Обновление каталога найденных сервисов",
            target=lambda: refresh_service_candidates("manual_job"),
            payload={"source": "v2fly/domain-list-community"},
        )
    )


@app.post("/api/services/candidates/auto")
async def api_services_candidates_set_auto(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    enabled = payload.get("enabled")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    cache = read_service_candidates_cache()
    cache["auto"] = enabled
    cache["auto_interval_seconds"] = SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS
    write_service_candidates_cache(cache)

    return JSONResponse(
        {
            "ok": True,
            "auto": enabled,
            "auto_interval_seconds": SERVICE_CANDIDATE_AUTO_INTERVAL_SECONDS,
            "time": now_iso(),
        }
    )


@app.post("/api/services/candidates/import")
async def api_services_candidates_import(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    ids = payload.get("ids")

    if ids is None and payload.get("id") is not None:
        ids = [payload.get("id")]

    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise HTTPException(status_code=400, detail="ids must be a list of strings")

    enabled = payload.get("enabled", True)

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    try:
        return JSONResponse(await asyncio.to_thread(import_service_candidates, ids, enabled))
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "service_candidate_import_failed",
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.post("/api/services/remove")
async def api_services_remove(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    ids = payload.get("ids")

    if ids is None and payload.get("id") is not None:
        ids = [payload.get("id")]

    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise HTTPException(status_code=400, detail="ids must be a list of strings")

    auto_discovered_only = payload.get("auto_discovered_only", False)

    if not isinstance(auto_discovered_only, bool):
        raise HTTPException(status_code=400, detail="auto_discovered_only must be boolean")

    try:
        return JSONResponse(await asyncio.to_thread(remove_service_catalog_items, ids, auto_discovered_only))
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "service_remove_failed",
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


def community_catalog_summary() -> dict[str, Any]:
    ensure_sources_file()
    sources = read_json(SOURCES_FILE, [])
    services = read_service_catalog()

    return {
        "sources": [
            {
                "name": str(source.get("name", "")),
                "description": source.get("description"),
                "type": source.get("type"),
                "enabled": bool(source.get("enabled", False)),
            }
            for source in sources
            if isinstance(source, dict) and str(source.get("name", "")).strip()
        ],
        "services": [
            {
                "id": str(service.get("id", "")),
                "title": service.get("title", service.get("id")),
                "category": service.get("category"),
            }
            for service in services
            if isinstance(service, dict) and str(service.get("id", "")).strip()
        ],
    }


def build_community_api_response(include_plan: bool = True) -> dict[str, Any]:
    profiles = read_community_profiles()
    response: dict[str, Any] = {
        "ok": True,
        "config": profiles,
        "default_community": BGP_COMMUNITY,
        "catalog": community_catalog_summary(),
        "time": now_iso(),
    }

    if include_plan:
        base_prefixes, _ = collect_static_prefixes()
        service_prefixes, _ = collect_service_prefixes_for_update()
        plan = build_community_route_plan(base_prefixes, service_prefixes)
        response["plan"] = {
            key: value
            for key, value in plan.items()
            if key not in {"prefixes", "route_communities"}
        }
    else:
        route_attributes = read_route_attributes()
        response["plan"] = {
            "default_community": BGP_COMMUNITY,
            "profiles": [
                {
                    "id": profile.get("id"),
                    "title": profile.get("title"),
                    "community": profile.get("community"),
                    "enabled": profile.get("enabled"),
                    "sources": profile.get("sources", []),
                    "services": profile.get("services", []),
                    "unique_before_aggregation": None,
                    "errors": [],
                }
                for profile in profiles.get("profiles", [])
            ],
            "profiles_count": len(profiles.get("profiles", [])),
            "enabled_count": sum(1 for profile in profiles.get("profiles", []) if profile.get("enabled")),
            "unique_before_aggregation": None,
            "final_count": len(read_lines(ADVERTISED_FILE)),
            "tagged_count": sum(1 for communities in route_attributes.values() if communities),
            "aggregate": AGGREGATE_PREFIXES,
            "time": now_iso(),
        }

    return response


@app.get("/api/communities")
async def api_communities(_: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(build_community_api_response, False))
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "community_profiles_failed",
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.put("/api/communities")
async def api_put_communities(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    config = payload.get("config", payload)
    validated = validate_community_profiles_config(config)
    write_community_profiles(validated)
    return JSONResponse(await asyncio.to_thread(build_community_api_response, False))


@app.post("/api/communities/set-enabled")
async def api_communities_set_enabled(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()
    profile_id = str(payload.get("id", "")).strip().lower()
    enabled = payload.get("enabled")

    if not profile_id:
        raise HTTPException(status_code=400, detail="id is required")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    config = read_community_profiles()
    found = False

    for profile in config.get("profiles", []):
        if profile.get("id") == profile_id:
            profile["enabled"] = enabled
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"community profile not found: {profile_id}")

    write_community_profiles(config)
    return JSONResponse(await asyncio.to_thread(build_community_api_response, False))


@app.post("/api/communities/preview")
async def api_communities_preview(_: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(build_community_api_response, True))
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "community_preview_failed",
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.get("/api/services/source-refresh")
async def api_services_source_refresh(_: str = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(service_source_refresh_summary())


@app.post("/api/services/source-refresh/{kind}")
async def api_services_refresh_source(kind: str, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(refresh_service_source_kind, kind, True, "manual"))
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "kind": kind,
                "mode": "service_source_refresh_failed",
                "would_apply": False,
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.post("/api/services/source-refresh/{kind}/job")
async def api_services_refresh_source_job(kind: str, _: str = Depends(require_auth)) -> JSONResponse:
    if kind not in SERVICE_SOURCE_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown service source kind: {kind}")

    source_meta = SERVICE_SOURCE_KINDS[kind]

    return JSONResponse(
        start_background_job(
            kind="service_source_refresh",
            key=f"service_source_refresh:{kind}",
            title=f"Обновление {source_meta['label']}",
            target=lambda: refresh_service_source_kind(kind, True, "manual_job"),
            payload={"kind": kind, "enabled_only": True},
        )
    )


@app.post("/api/services/source-refresh/{kind}/auto")
async def api_services_set_source_auto(kind: str, request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    if kind not in SERVICE_SOURCE_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown service source kind: {kind}")

    payload = await request.json()
    enabled = payload.get("enabled")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    state = read_service_source_refresh_state()
    state.setdefault("sources", {}).setdefault(kind, {})
    state["sources"][kind]["auto"] = enabled
    write_service_source_refresh_state(state)

    return JSONResponse(
        {
            "ok": True,
            "kind": kind,
            "auto": enabled,
            "source_refresh": service_source_refresh_summary(),
            "time": now_iso(),
        }
    )


@app.post("/api/services/set-enabled")
async def api_services_set_enabled(request: Request, _: str = Depends(require_auth)) -> JSONResponse:
    payload = await request.json()

    service_id = str(payload.get("id", "")).strip()
    enabled = payload.get("enabled")

    if not service_id:
        raise HTTPException(status_code=400, detail="id is required")

    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be boolean")

    catalog = read_service_catalog()
    known_ids = {str(item.get("id")) for item in catalog if isinstance(item, dict)}

    if service_id not in known_ids:
        raise HTTPException(status_code=404, detail=f"service not found: {service_id}")

    state = read_service_state()
    state.setdefault("services", {})
    state["services"].setdefault(service_id, {})
    state["services"][service_id]["enabled"] = enabled
    state["updated_at"] = now_iso()

    write_service_state(state)

    return JSONResponse(
        {
            "ok": True,
            "id": service_id,
            "enabled": enabled,
            "message": "service state saved; routes were not applied",
            "time": now_iso(),
        }
    )


def collect_service_prefixes_for_update() -> tuple[list[str], dict[str, Any]]:
    if not SERVICE_ROUTES_ENABLED:
        return [], {
            "enabled": False,
            "reason": "SERVICE_ROUTES_ENABLED=false",
            "final_count": 0,
        }

    result = build_service_routes(enabled_only=True)

    prefixes = result.get("prefixes", [])

    if not isinstance(prefixes, list):
        raise RuntimeError("service routes result prefixes must be a list")

    return [str(item) for item in prefixes], {
        "enabled": True,
        "final_count": len(prefixes),
        "services_count": result.get("services_count"),
        "enabled_count": result.get("enabled_count"),
        "service_stats": result.get("service_stats", []),
        "time": result.get("time"),
    }


@app.post("/api/services/resolve")
async def api_services_resolve(_: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(build_service_routes, True))
    except Exception as e:
        result = {
            "ok": False,
            "mode": "service_routes_dry_run_failed",
            "would_apply": False,
            "error": str(e),
            "time": now_iso(),
        }
        write_json_atomic(SERVICE_CACHE_FILE, result)
        return JSONResponse(result, status_code=500)


@app.get("/api/services/routes", response_class=PlainTextResponse)
async def api_services_routes(_: str = Depends(require_auth)) -> str:
    prefixes = read_lines(SERVICE_ROUTES_FILE)
    return "\n".join(prefixes) + ("\n" if prefixes else "")


def network_is_covered_by_any(
    network: ipaddress.IPv4Network,
    candidates: list[ipaddress.IPv4Network],
) -> bool:
    for candidate in candidates:
        if network.subnet_of(candidate):
            return True
    return False


def build_service_apply_preview() -> dict[str, Any]:
    base_prefixes, base_meta = collect_static_prefixes()
    service_result = build_service_routes(enabled_only=True)

    service_prefixes = service_result.get("prefixes", [])

    if not isinstance(service_prefixes, list):
        raise RuntimeError("service routes result prefixes must be a list")

    base_networks = [
        ipaddress.ip_network(item, strict=False)
        for item in base_prefixes
    ]

    service_networks = [
        ipaddress.ip_network(str(item), strict=False)
        for item in service_prefixes
    ]

    current_advertised_networks = [
        ipaddress.ip_network(item, strict=False)
        for item in read_lines(ADVERTISED_FILE)
    ]

    covered_by_base: list[str] = []
    not_covered_by_base: list[str] = []

    for network in sorted(service_networks):
        if network_is_covered_by_any(network, base_networks):
            covered_by_base.append(str(network))
        else:
            not_covered_by_base.append(str(network))

    merged_networks = set(base_networks) | set(service_networks)

    if AGGREGATE_PREFIXES:
        final_networks = list(ipaddress.collapse_addresses(sorted(merged_networks)))
    else:
        final_networks = sorted(merged_networks)

    final_prefixes = [str(item) for item in final_networks]
    final_set = set(final_prefixes)
    current_set = {str(item) for item in current_advertised_networks}

    would_add = sort_prefixes(final_set - current_set)
    would_delete = sort_prefixes(current_set - final_set)
    unchanged = len(final_set & current_set)

    return {
        "ok": True,
        "mode": "service_apply_preview",
        "would_apply": False,
        "service_routes_enabled": SERVICE_ROUTES_ENABLED,
        "base": {
            "count": len(base_prefixes),
            "meta": base_meta,
        },
        "services": {
            "enabled_count": service_result.get("enabled_count"),
            "services_count": service_result.get("services_count"),
            "route_count": len(service_prefixes),
            "covered_by_base_count": len(covered_by_base),
            "not_covered_by_base_count": len(not_covered_by_base),
            "covered_by_base_first_20": covered_by_base[:20],
            "not_covered_by_base_first_20": not_covered_by_base[:20],
            "stats": service_result.get("service_stats", []),
        },
        "current_advertised": {
            "count": len(current_set),
        },
        "final": {
            "count": len(final_prefixes),
            "first_20": final_prefixes[:20],
            "last_20": final_prefixes[-20:],
        },
        "diff_vs_current_advertised": {
            "add_count": len(would_add),
            "delete_count": len(would_delete),
            "unchanged": unchanged,
            "add_first_50": would_add[:50],
            "delete_first_50": would_delete[:50],
        },
        "time": now_iso(),
    }


@app.get("/api/services/apply-preview")
async def api_services_apply_preview(_: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(build_service_apply_preview))
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "service_apply_preview_failed",
                "would_apply": False,
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )


@app.post("/api/services/resolve/{service_id}")
async def api_services_resolve_one(service_id: str, _: str = Depends(require_auth)) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(resolve_service_by_id, service_id))
    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "mode": "single_service_resolve_preview_failed",
                "would_apply": False,
                "id": service_id,
                "error": str(e),
                "time": now_iso(),
            },
            status_code=500,
        )
