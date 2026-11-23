"""路线偏好：外径形腔的 c1 / 121 实现与分型方向（持久化 + 解析）。

持久化文件由 :data:`settings.OUT_ROOT` 派生::

    <OUT_ROOT>/_config/route_prefs.json

结构（``version`` 固定 1，``parts`` 按件号，``models`` 按模型键）::

    {
      "version": 1,
      "parts": {
        "H0A3Z2-DT": {
          "direction": "auto",
          "models": {
            "成型_外径形腔": {"impl": "121", "direction": "auto"}
          }
        }
      }
    }

约定：

* 读写一律**容错**：文件不存在 / JSON 损坏 / 字段类型不对都回落到默认值，
  不抛异常（:func:`load_prefs` / :func:`get_part_pref` / :func:`get_model_pref`）。
* ``save_prefs`` 用「临时文件 + ``os.replace``」原子写，并自动建目录。
* 只有显式写了 ``impl`` 才覆盖默认：``resolve_impl`` 缺省回落到
  :func:`core.entity.contracts.uses_reference_route`（2052 的外径形腔 = 121）。
* ``impl`` 只认 ``default`` / ``c1`` / ``121``；``direction`` 只认
  ``auto`` / ``X`` / ``Y`` / ``Z``（大小写归一）。

本模块只依赖标准库与 ``contracts`` / ``settings``，可在 ``--dry-run`` 里安全导入。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

PREF_FILENAME = "route_prefs.json"

#: ``impl`` 的合法取值；``default`` = 交给 :mod:`contracts` 的默认判定
DEFAULT_IMPL = "default"
IMPLS = (DEFAULT_IMPL, "c1", "121")

#: 分型方向的合法取值（``auto`` = 按规则自动算）
DEFAULT_DIRECTION = "auto"
DIRECTIONS = (DEFAULT_DIRECTION, "X", "Y", "Z")

#: 手动角度写法：``angle:-27`` / ``angle=-27`` / ``angle_z:-27`` / ``angle:-27°``
_ANGLE_RE = re.compile(
    r"^angle(?:\s*[_ -]?\s*z)?\s*[:=]\s*([-+]?\d+(?:\.\d+)?)\s*°?$",
    re.IGNORECASE,
)

VERSION = 1
PARTS_KEY = "parts"
CONFIG_SUBDIR = "_config"


# ------------------------------------------------------------------ 路径

def prefs_path(out_root=None) -> Path:
    """偏好文件路径：``<out_root>/_config/route_prefs.json``（默认 ``settings.OUT_ROOT``）。"""
    from . import settings

    root = Path(out_root) if out_root else Path(settings.OUT_ROOT)
    root = root.expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    return root / CONFIG_SUBDIR / PREF_FILENAME


# ------------------------------------------------------------------ 归一化

def _default_prefs() -> dict:
    return {"version": VERSION, PARTS_KEY: {}}


def _normalize_direction(value: Any, default: str = DEFAULT_DIRECTION) -> str:
    """``auto`` / ``X`` / ``Y`` / ``Z`` / ``angle:<度>``，大小写与空白归一。

    ``angle`` 是绕 +Z 轴、从 +X 逆时针为正的角度（例如 ``angle:-27``），落盘统一
    成 ``angle:<float>``。非法值回落 ``default``。
    """
    if not isinstance(value, str):
        return default
    text = value.strip()
    if text.upper() in ("", "AUTO", "自动"):
        return DEFAULT_DIRECTION
    if text.upper() in ("X", "Y", "Z"):
        return text.upper()
    match = _ANGLE_RE.match(text)
    if match:
        return "angle:%g" % float(match.group(1))
    return default


def _normalize_impl(value: Any, default: str = DEFAULT_IMPL) -> str:
    """``default`` / ``c1`` / ``121``；``121保形`` 之类的别名也认，非法值回落 ``default``。"""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("", "default", "默认"):
        return DEFAULT_IMPL
    if text in ("c1", "c1外推"):
        return "c1"
    if text in ("121", "121保形", "参考保形"):
        return "121"
    return default


def _normalize_prefs(prefs: Any) -> dict:
    """把任意对象收敛成合法结构（不抛异常）。"""
    if not isinstance(prefs, dict):
        return _default_prefs()
    raw_parts = prefs.get(PARTS_KEY)
    parts: dict[str, dict] = {}
    if isinstance(raw_parts, dict):
        for part, entry in raw_parts.items():
            if not isinstance(part, str) or not part.strip():
                continue
            part = part.strip()
            entry = entry if isinstance(entry, dict) else {}
            models: dict[str, dict] = {}
            raw_models = entry.get("models")
            if isinstance(raw_models, dict):
                for key, model in raw_models.items():
                    if not isinstance(key, str) or not key.strip():
                        continue
                    model = model if isinstance(model, dict) else {}
                    item: dict[str, str] = {}
                    if "impl" in model:
                        impl = _normalize_impl(model.get("impl"))
                        if impl != DEFAULT_IMPL:
                            item["impl"] = impl
                    if "direction" in model:
                        direction = _normalize_direction(model.get("direction"))
                        if direction != DEFAULT_DIRECTION:
                            item["direction"] = direction
                    if item:  # 全默认的条目不留（保持文件干净）
                        models[key.strip()] = item
            part_entry: dict[str, Any] = {"direction": _normalize_direction(entry.get("direction"))}
            if models:
                part_entry["models"] = models
            if models or part_entry["direction"] != DEFAULT_DIRECTION:
                parts[part] = part_entry  # 全默认的件也不留
    return {"version": VERSION, PARTS_KEY: parts}


# ------------------------------------------------------------------ 读写

def load_prefs(path=None) -> dict:
    """读偏好；文件缺失 / 损坏 / 字段类型错 → ``{"version":1,"parts":{}}``。"""
    target = Path(path) if path is not None else prefs_path()
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return _default_prefs()
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return _default_prefs()
    return _normalize_prefs(raw)


def save_prefs(prefs: dict, path=None) -> dict:
    """原子写偏好（临时文件 + ``os.replace``），返回落盘用的规整结构。

    目录不存在会自动创建。写盘失败会清理临时文件并把异常抛给调用方。
    """
    data = _normalize_prefs(prefs)
    target = Path(path) if path is not None else prefs_path()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return data


# ------------------------------------------------------------------ 查询 / 修改

def get_part_pref(prefs, part) -> dict:
    """该件的偏好：``{"direction": "auto", "models": {...}}``（永远是新的 dict）。"""
    data = _normalize_prefs(prefs)
    entry = data[PARTS_KEY].get(str(part).strip(), {}) if part is not None else {}
    models = entry.get("models") if isinstance(entry, dict) else None
    return {
        "direction": _normalize_direction((entry or {}).get("direction")),
        "models": dict(models) if isinstance(models, dict) else {},
    }


def get_model_pref(prefs, part, key) -> dict:
    """该「件 + 模型」的显式偏好：``{"impl": "default"|"c1"|"121", "direction": "auto"|"X"|"Y"|"Z"}``。"""
    models = get_part_pref(prefs, part)["models"]
    entry = models.get(str(key)) if key is not None else None
    entry = entry if isinstance(entry, dict) else {}
    return {
        "impl": _normalize_impl(entry.get("impl")),
        "direction": _normalize_direction(entry.get("direction")),
    }


def set_model_pref(prefs, part, key, *, impl=None, direction=None) -> dict:
    """写入该「件 + 模型」的偏好，返回**新的**偏好 dict（不落盘，调用方决定 save）。

    ``impl`` / ``direction`` 传 ``None`` 表示该项不变；非法值按默认处理。
    ``impl=default`` 且 ``direction=auto`` 时删除该条目。
    """
    data = _normalize_prefs(prefs)
    part = str(part).strip() if part is not None else ""
    key = str(key).strip() if key is not None else ""
    if not part or not key:
        return data

    parts = data[PARTS_KEY]
    entry = parts.setdefault(part, {"direction": DEFAULT_DIRECTION})
    models = entry.setdefault("models", {})
    item = dict(models.get(key) or {})

    if impl is not None:
        normalized = _normalize_impl(impl)
        if normalized == DEFAULT_IMPL:
            item.pop("impl", None)
        else:
            item["impl"] = normalized
    if direction is not None:
        normalized = _normalize_direction(direction)
        if normalized == DEFAULT_DIRECTION:
            item.pop("direction", None)
        else:
            item["direction"] = normalized

    if item:
        models[key] = item
    else:
        models.pop(key, None)
    if not models:
        entry.pop("models", None)
    return data


# ------------------------------------------------------------------ 解析生效值

def resolve_impl(prefs, part, key) -> str:
    """该「件 + 模型」实际用 ``c1`` 还是 ``121``。

    显式配置优先（``c1`` / ``121``）；``default`` 时回落到
    :func:`contracts.uses_reference_route`（2052 的「成型_外径形腔」→ ``121``）。
    """
    impl = get_model_pref(prefs, part, key)["impl"]
    if impl in ("c1", "121"):
        return impl
    from . import contracts

    return "121" if contracts.uses_reference_route(part, key) else "c1"


def resolve_direction(prefs, part, key) -> str:
    """该「件 + 模型」实际用哪个分型方向：模型显式 → 件级 → ``auto``。"""
    model = get_model_pref(prefs, part, key)["direction"]
    if model != DEFAULT_DIRECTION:
        return model
    return get_part_pref(prefs, part)["direction"]


__all__ = [
    "PREF_FILENAME",
    "DEFAULT_IMPL",
    "IMPLS",
    "DEFAULT_DIRECTION",
    "DIRECTIONS",
    "VERSION",
    "prefs_path",
    "load_prefs",
    "save_prefs",
    "get_part_pref",
    "get_model_pref",
    "set_model_pref",
    "resolve_impl",
    "resolve_direction",
]
