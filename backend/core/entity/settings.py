"""实体化流水线：路径、端口与运行环境。

所有路径都相对本仓库根目录解析，除 FreeCAD / 主解释器外不依赖外部绝对路径。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Giant-Unified 根目录（backend/core/entity/settings.py → 上四级）
ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = ROOT / "backend"
ENTITY_DIR = Path(__file__).resolve().parent
VENDOR_DIR = ENTITY_DIR / "vendor"

#: 原始管件目录（流水线唯一入口）
SRC_PARTS_DIR = Path(os.environ.get("ENTITY_SRC", str(ROOT / "gnt管件")))
#: 默认输出根
OUT_ROOT = Path(os.environ.get("ENTITY_OUT", str(ROOT / "entity_out")))

#: FreeCAD 端插件
ADDON_DIR = ROOT / "freecad_addon"
ADDON_PACKAGE = ADDON_DIR / "PipeSolidBridge"

#: 端口（与主程序 5880/5050/3000/18765 全部错开）
WEB_PORT = int(os.environ.get("APP_PORT", "5890"))
DEV_PORT = int(os.environ.get("ENTITY_DEV_PORT", "5060"))
BRIDGE_PORT = int(os.environ.get("PIPESOLID_BRIDGE_PORT", "18766"))
BRIDGE_HOST = os.environ.get("PIPESOLID_BRIDGE_HOST", "127.0.0.1")

def _default_freecad_home() -> Path:
    """FreeCAD 安装目录。

    交付包里优先用同级的 ``freecad``（便携版），其次相邻目录，最后退回开发机的
    ``D:\\Freecad``；显式设置 ``FREECAD_HOME`` 时以环境变量为准。
    """
    for candidate in (ROOT / "freecad", ROOT.parent / "freecad", Path(r"D:\Freecad")):
        if (candidate / "bin" / "freecadcmd.exe").is_file() or (
            candidate / "bin" / "FreeCADCmd.exe"
        ).is_file():
            return candidate
    return Path(r"D:\Freecad")


#: 主解释器（OCP 7.7.2 + numpy 1.26.4 + Flask）；交付包里就是 python\python.exe
PY_OCC = Path(os.environ.get("ENTITY_PY_OCC") or sys.executable)
FREECAD_HOME = Path(os.environ.get("FREECAD_HOME") or _default_freecad_home())

#: c1 路沿用 c2 已验证参数
HALF_SAMPLES = int(os.environ.get("ENTITY_HALF_SAMPLES", "65"))
SMOOTH_PASSES = int(os.environ.get("ENTITY_SMOOTH_PASSES", "2"))
STATION_STEP_MM = 2.0

#: pipe 路默认参数
PIPE_OPTIONS = {
    "direction": "auto",
    "half_points": 96,
    "max_sections": "auto",
    "export_stl": False,
    "open_in_freecad": False,
}


def freecad_exe() -> Path | None:
    for name in ("freecad.exe", "FreeCAD.exe"):
        p = FREECAD_HOME / "bin" / name
        if p.is_file():
            return p
    return None


def freecad_cmd() -> Path | None:
    for name in ("freecadcmd.exe", "FreeCADCmd.exe"):
        p = FREECAD_HOME / "bin" / name
        if p.is_file():
            return p
    return None


def ensure_out(out_root: Path | str | None = None) -> dict:
    """建好输出目录骨架，返回关键路径字典。"""
    out = Path(out_root or OUT_ROOT)
    paths = {
        "root": out,
        "cache": out / "_cache",
        # FreeCADCmd 的任务/结果 json（vendor/pss/config.jobs_dir() 同一条路径）
        "tmp": out / "_scratch" / "pipe_jobs",
        "report": out / "_report",
        "logs": out / "_logs",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def part_paths(out_root: Path | str, part: str) -> dict:
    base = Path(out_root) / part
    paths = {
        "base": base,
        "models": base / "00_models",
        "c1": base / "10_c1",
        "pipe": base / "20_pipe",
        "logs": base / "logs",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def measurement_signature() -> str:
    """测量参数签名（``GTAPP_STATION_MM`` / ``GTAPP_STATION_GRID``）。

    默认 1 mm 实测 + 1 mm 归档时返回空串，历史缓存文件名保持不变；
    其它组合加 ``s<步距>g<归档步距>`` 后缀。缓存文件名必须带上它，
    否则把 ``GTAPP_STATION_MM=3`` 打开后会直接命中 1 mm 的旧缓存，
    页面显示 0.0 s 且拿到的仍是 1 mm 读数。
    """
    def _value(name: str) -> float:
        try:
            raw = float(os.environ.get(name, "") or 0)
        except (TypeError, ValueError):
            return 0.0
        return raw if raw > 0 else 0.0

    station = _value("GTAPP_STATION_MM") or 1.0
    grid = _value("GTAPP_STATION_GRID") or 1.0
    if abs(station - 1.0) < 1e-9 and abs(grid - 1.0) < 1e-9:
        return ""
    return f"s{station:g}g{grid:g}"


def measured_path(part: str, end_treatment: str, out_root: Path | str | None = None) -> Path:
    out = Path(out_root or OUT_ROOT)
    suffix = "" if end_treatment == "c1" else f"_{end_treatment}"
    signature = measurement_signature()
    if signature:
        suffix += f"_{signature}"
    return out / "_cache" / f"{part}_measured{suffix}.json"
