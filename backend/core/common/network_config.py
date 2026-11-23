"""运行时网络访问配置（可在 Web 管理界面调整，无需改 .env 并重启容器）。

优先级：network_config.json 中的字段 > 环境变量 GTAPP_* 默认值。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

from config import ALLOWED_CIDRS, IP_ALLOWLIST_ENABLED, DATA_DIR

_CONFIG_PATH = os.path.join(DATA_DIR, 'network_config.json')
_lock = threading.Lock()
_cached: Optional[dict] = None
_cached_mtime: Optional[float] = None


def _read_file() -> dict:
    global _cached, _cached_mtime
    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        _cached = {}
        _cached_mtime = None
        return {}
    if _cached is not None and mtime == _cached_mtime:
        return _cached
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    _cached = data
    _cached_mtime = mtime
    return data


def get_allowed_cidrs() -> str:
    data = _read_file()
    val = data.get('allowed_cidrs')
    if val is not None and str(val).strip():
        return str(val).strip()
    return ALLOWED_CIDRS


def is_ip_allowlist_enabled() -> bool:
    data = _read_file()
    if 'ip_allowlist_enabled' in data:
        return bool(data['ip_allowlist_enabled'])
    return IP_ALLOWLIST_ENABLED


def save_network_config(*, allowed_cidrs: str, ip_allowlist_enabled: bool) -> None:
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    payload = {
        'allowed_cidrs': allowed_cidrs.strip(),
        'ip_allowlist_enabled': bool(ip_allowlist_enabled),
    }
    with _lock:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        global _cached, _cached_mtime
        _cached = payload
        try:
            _cached_mtime = os.path.getmtime(_CONFIG_PATH)
        except OSError:
            _cached_mtime = None


def get_public_info() -> dict:
    data = _read_file()
    return {
        'allowed_cidrs': get_allowed_cidrs(),
        'ip_allowlist_enabled': is_ip_allowlist_enabled(),
        'config_overridden': bool(data),
    }
