"""C2 公共配置：路径、管件清单、可信参考清单、路线/复查状态。

C2 只有两种情况（来自 c2.docx 里的对话结论）：

  情况 121  已有正确外形的多段参考模型（形腔 STEP，面数 89~149 都算）
            → 保形转成 4 面 Solid（2 侧面 + 2 平面端盖）
            → 只在人工登记 preferred_route=reference 时走。

  情况二    raw C1 外推截面（<管件>_measured.json）
            → 同一个 4 面 Solid 规范
            → **默认路线**；新件直接出这一版（2026-11-03 起与复查解耦）。
              （历史：2026-11-02 及以前是“人工判定 121 不行之后才走 C1”。）

两条硬约束：

1. 不按“几何看起来像不像”猜路线。路线只认 state.json 的 preferred_route
   （人写，默认 c1）；references.json 里的可信清单
   + 质量门禁（sha256 / 面数 / valid / 体积）只负责确认 121 参考本身可信。
2. references.json 里每个管件都登记了 121 参考与 measured.json。
   缺参考时写错误记录；只要 measured.json 在，就能继续用 C1 出件。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 目录与清单

# 路径一律取 core.entity.settings（本文件是流水线内的 vendor 副本，不再自带
# D:\AAAGNT-1 这种绝对路径）。只有被当成独立脚本模块 import 时才按文件位置回退。
try:  # 包内用法：core.entity.vendor.c2.c2_config
    from ... import settings as _settings
except Exception:  # noqa: BLE001 - 独立脚本 import c2_config 时没有父包
    _settings = None

if _settings is not None:
    ROOT = Path(_settings.ROOT)                     # Giant-Unified 根
    PIPE_SMOOTHER = Path(_settings.VENDOR_DIR) / "pipe_smoother"
    BACKEND_DIR = Path(_settings.BACKEND_DIR)
else:  # backend/core/entity/vendor/c2/c2_config.py -> Giant-Unified
    ROOT = Path(__file__).resolve().parents[5]
    PIPE_SMOOTHER = Path(__file__).resolve().parents[1] / "pipe_smoother"
    BACKEND_DIR = ROOT / "backend"

# C2 的全部产物（references/state/report/output/zip）都挂在 C2_DIR 下。默认就是
# <Giant-Unified>/c2；设了环境变量 C2_DIR 就整套搬过去，用于“从头跑一次、产物
# 单独放一个文件夹”的场景，代码仍然只有这一份。
C2_DIR = Path(os.environ.get("C2_DIR") or (ROOT / "c2"))
OUTPUT_DIR = C2_DIR / "output"
REF_DIR = OUTPUT_DIR / "ref"          # 121 参考 → 4 面的重建件
C1_DIR = OUTPUT_DIR / "c1"            # 情况二（C1 外推）的候选件
REVIEW_DIR = OUTPUT_DIR / "review"    # 人工复查图

REFERENCES_FILE = C2_DIR / "references.json"
STATE_FILE = C2_DIR / "state.json"
HISTORY_FILE = C2_DIR / "history.jsonl"
REPORT_JSON = C2_DIR / "c2_report.json"
REPORT_MD = C2_DIR / "c2_report.md"
MANIFEST_JSON = C2_DIR / "manifest.json"
BUNDLE_ZIP = C2_DIR / "c2_all.zip"

CASES = [
    "24TK_1324-1",
    "24TK_1324-2",
    "24TK_1324-31",
    "24TK_1442-1",
    "24TK_2052-2",
    "H0A2Z3-DT",
]

# 每个截面半圈采样点数（prepare_sections 的 half_samples）。65 是 Giant 管件的
# 既有标定值：闭口环共 129 点，B 样条周期插值后 u=0/0.5 正好落在左右极点上。
HALF_SAMPLES = 65

# 4 面路线（与人工复查解耦）：
#   c1        = 情况二：从 <管件>_measured.json 外推重建 4 面（默认）
#   reference = 情况121：读 121 形腔保形重建 4 面（必须人工在 state.json 里指定）
DEFAULT_ROUTE = "c1"
ROUTE_CHOICES = ("c1", "reference")

DEFAULT_GATE = {
    "require_sha256": True,
    "require_valid": True,
    "require_single_solid": True,
    "face_tolerance": 0,          # 参考模型面数必须与清单一致
    "volume_tolerance_pct": 0.5,  # 参考实体体积允许漂移（%）
    "free_edge_max": 0,
}

DIRS = (OUTPUT_DIR, REF_DIR, C1_DIR, REVIEW_DIR)


def ensure_dirs() -> None:
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)


def utf8_stdout() -> None:
    """让控制台按 UTF-8 输出，避免中文路径/日志被代码页打断。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 老环境没有 reconfigure
            pass


# ---------------------------------------------------------------- 小工具


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def sha256_file(path: os.PathLike | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: os.PathLike | str, default=None):
    p = Path(path)
    if not p.is_file():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(path: os.PathLike | str, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rel(path: os.PathLike | str | None) -> str:
    """统一成以 c2 目录为基准的相对路径（报告里更好读）。"""
    if not path:
        return ""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(C2_DIR.resolve())).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return str(p)


def append_history(event: str, **fields) -> None:
    """追加一行审计日志，方便回看“哪件什么时候按哪条路线出的”。"""
    record = {"at": now_iso(), "event": event}
    record.update({k: v for k, v in fields.items() if v is not None})
    with open(HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 参考清单


def load_references() -> dict:
    refs = read_json(REFERENCES_FILE)
    if not refs:
        raise SystemExit(
            f"缺少参考清单 {REFERENCES_FILE}；C2 不按几何相似度猜路线，必须先建清单。"
        )
    refs.setdefault("cases", {})
    refs.setdefault("gate", dict(DEFAULT_GATE))
    return refs


def save_references(refs: dict) -> None:
    refs["updated_at"] = now_iso()
    write_json(REFERENCES_FILE, refs)


# ---------------------------------------------------------------- 复查状态


def default_case_state() -> dict:
    return {
        "review": {"status": "pending", "at": None, "by": "auto", "note": ""},
        # 人工指定的 4 面路线（c1 / reference）。c1 是默认；reference 必须人写。
        "preferred_route": DEFAULT_ROUTE,
        "preferred_route_note": "",
        "route": None,          # 上一次实际走的路线（由程序写）
        "route_reason": "",
        "adopted": None,
        "reference": None,
        "c1": None,
        "last_run": None,
        "errors": [],
    }


def load_state() -> dict:
    state = read_json(STATE_FILE) or {}
    state.setdefault("schema", "c2-state/1")
    state.setdefault("cases", {})
    return state


def save_state(state: dict) -> None:
    state["updated_at"] = now_iso()
    write_json(STATE_FILE, state)


def case_state(state: dict, case: str) -> dict:
    """取某件的状态（缺项补默认值，已有内容原样保留）。"""
    st = state["cases"].setdefault(case, {})
    base = default_case_state()
    for key, value in base.items():
        if key not in st or st[key] is None:
            st[key] = json.loads(json.dumps(value))
    st["review"].setdefault("status", "pending")
    return st


def review_status(state: dict, case: str) -> str:
    status = case_state(state, case)["review"].get("status") or "pending"
    return status if status in ("pending", "approved", "rejected") else "pending"


def preferred_route(state: dict, case: str) -> str:
    """人工指定的 4 面路线，默认 c1；非法值按 c1 处理。"""
    route = case_state(state, case).get("preferred_route") or DEFAULT_ROUTE
    return route if route in ROUTE_CHOICES else DEFAULT_ROUTE


def set_preferred_route(state: dict, case: str, route: str, note: str = "", by: str = "user") -> dict:
    if route not in ROUTE_CHOICES:
        raise SystemExit(f"路线只能是 {ROUTE_CHOICES}，收到 {route!r}")
    st = case_state(state, case)
    st["preferred_route"] = route
    st["preferred_route_note"] = note
    append_history("route", case=case, route=route, note=note, by=by)
    return st


def set_review(state: dict, case: str, status: str, note: str = "", by: str = "user") -> dict:
    if status not in ("pending", "approved", "rejected"):
        raise SystemExit(f"复查状态只能是 pending/approved/rejected，收到 {status!r}")
    st = case_state(state, case)
    st["review"] = {"status": status, "at": now_iso(), "by": by, "note": note}
    append_history("review", case=case, status=status, note=note, by=by)
    return st
