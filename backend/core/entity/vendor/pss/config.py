# -*- coding: utf-8 -*-
"""路径 / 端口 / 运行环境（vendor 副本，随 Giant-Unified 实体化流水线改造）。

改造点相对原版 ``AAAGNT-2/PipeSolidStudio/backend/app/config.py``：

* 不再读写 ``data/config.json``，一律走环境变量 + :mod:`core.entity.settings`，
  避免与「同一份路径两处定义」的坑。
* 桥端口默认 **18766**（原 18765 已被原程序占用）；web 端口 **5890**（原 8787）。
* FreeCADCmd 任务 json 落在 ``<out_root>/_scratch/pipe_jobs``（默认
  ``entity_out/_scratch/pipe_jobs``），文件名 ``job_<uuid8>.json``。
* 只被主进程（headless / bridge_client）import；**禁止**在 FreeCAD 进程里 import，
  FreeCAD 端只认 ``PIPESOLID_JOB`` 环境变量（见 ``freecad_runner.py``）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

#: 仓库根（backend/core/entity/vendor/pss/config.py → 上五级）
ROOT = Path(__file__).resolve().parents[5]
ADDON = ROOT / "freecad_addon" / "PipeSolidBridge"
RUNNER = Path(__file__).resolve().parent / "freecad_runner.py"

_DEFAULTS = {
    "freecad_home": os.environ.get("FREECAD_HOME", r"D:\Freecad"),
    "bridge_host": os.environ.get("PIPESOLID_BRIDGE_HOST", "127.0.0.1"),
    "bridge_port": int(os.environ.get("PIPESOLID_BRIDGE_PORT", "18766")),
    "web_port": int(os.environ.get("APP_PORT", "5890")),
}

#: 优先复用实体化流水线 settings（同一份端口/路径定义，避免两处漂移）。
try:  # pragma: no cover - 仅在独立运行本目录脚本时退化
    from ... import settings as _entity_settings  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    _entity_settings = None

if _entity_settings is not None:
    ROOT = Path(getattr(_entity_settings, "ROOT", ROOT)).resolve()
    ADDON = Path(getattr(_entity_settings, "ADDON_PACKAGE", ADDON))
    _DEFAULTS.update(
        freecad_home=str(getattr(_entity_settings, "FREECAD_HOME", _DEFAULTS["freecad_home"])),
        bridge_host=str(getattr(_entity_settings, "BRIDGE_HOST", _DEFAULTS["bridge_host"])),
        bridge_port=int(getattr(_entity_settings, "BRIDGE_PORT", _DEFAULTS["bridge_port"])),
        web_port=int(getattr(_entity_settings, "WEB_PORT", _DEFAULTS["web_port"])),
    )
    _OUT_ROOT = Path(getattr(_entity_settings, "OUT_ROOT", ROOT / "entity_out"))
else:
    _OUT_ROOT = Path(os.environ.get("ENTITY_OUT", str(ROOT / "entity_out")))

CONFIG = dict(_DEFAULTS)

#: FreeCADCmd 任务 json 目录（默认 entity_out/_scratch/pipe_jobs）
TMP = Path(os.environ.get("PIPESOLID_JOB_DIR", str(_OUT_ROOT / "_scratch" / "pipe_jobs")))
#: 兼容原版字段名（原版此处为 data/tmp）
DATA = TMP.parent


def jobs_dir(out_root=None) -> Path:
    """本次运行的 job/result json 目录；``out_root`` 优先于环境变量。"""
    env = os.environ.get("PIPESOLID_JOB_DIR")
    if env:
        p = Path(env)
    elif out_root:
        p = Path(out_root) / "_scratch" / "pipe_jobs"
    else:
        p = TMP
    p.mkdir(parents=True, exist_ok=True)
    return p


def freecad_home() -> Path:
    return Path(CONFIG["freecad_home"])


def freecad_exe():
    """GUI 版 freecad.exe（仅用于「在 FreeCAD 打开」，流水线不用）。"""
    exe = os.environ.get("FREECAD_EXE")
    if exe and Path(exe).is_file():
        return str(exe)
    for name in ("freecad.exe", "FreeCAD.exe"):
        p = freecad_home() / "bin" / name
        if p.is_file():
            return str(p)
    return shutil.which("FreeCAD") or shutil.which("freecad")


def freecad_cmd():
    """无头版 freecadcmd.exe（pipe 路唯一执行入口）。"""
    exe = os.environ.get("FREECAD_CMD")
    if exe and Path(exe).is_file():
        return str(exe)
    for name in ("freecadcmd.exe", "FreeCADCmd.exe"):
        p = freecad_home() / "bin" / name
        if p.is_file():
            return str(p)
    return shutil.which("FreeCADCmd") or shutil.which("freecadcmd")


def addon_parent() -> Path:
    """FreeCAD 进程里 ``sys.path.insert(0, addon_parent)`` 的那一级目录。"""
    return ADDON.parent


def user_mod_dir():
    appdata = os.environ.get("APPDATA", "")
    for p in (Path(appdata) / "FreeCAD" / "v1-1" / "Mod",
              Path(appdata) / "FreeCAD" / "Mod"):
        if p.parent.exists():
            return p
    return Path(appdata) / "FreeCAD" / "Mod"


def addon_installed() -> bool:
    return (user_mod_dir() / "PipeSolidBridge" / "InitGui.py").is_file()


__all__ = [
    "ROOT", "ADDON", "RUNNER", "CONFIG", "TMP", "DATA", "jobs_dir",
    "freecad_home", "freecad_exe", "freecad_cmd", "addon_parent",
    "user_mod_dir", "addon_installed",
]
