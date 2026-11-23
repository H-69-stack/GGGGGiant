"""「硬模成型」页面 → 实体化流水线 的桥接。

四个业务页（成型 / 扩口 / 压扁 / 压弯）调用 ``/api/rfm/generate*`` 生成模型后，
本模块把刚生成的 OCC shape 直接送进实体化流水线，返回 4 面实体（1 Solid /
4 faces / 2 条共享分型边 / 0 自由边），页面用它替换预览、体积与下载。

路线按 ``contracts.default_route``：
  * 成型_外径形腔 → ``c1``（C1 外推：会话里的 sections_full 当作 measured 数据）
  * 扩口/压扁/压弯 → ``pipe``（FreeCADCmd 重蒙皮）
失败或退化时回退到 ``selfref``（STEP 保形重建），保证页面永远有 4 面件可用。

本模块只在 API 进程里跑：c1 用进程内 OCP，pipe 起 FreeCADCmd 子进程，
两者都是同步调用，调用方负责超时与错误兜底。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from typing import Any, Optional

from . import checks, contracts, settings

#: 工序 → 默认模型键（扩口的芯轴单独指定）
MODEL_BY_OP = {
    "shaping": "成型_外径形腔",
    "belling": "扩口_扩口腔体",
    "flattening": "压扁_压扁模型",
    "bending": "压弯_压弯模型",
}
MANDREL_KEY = "扩口_扩口芯轴"


def model_key(operation: str, kind: str = "") -> str:
    """RFM 工序（+ cage 变体）→ 实体化模型键。"""
    if operation == "belling":
        # 扩口页的「主产物」是芯轴（型腔走 generate-cavity 单独生成）。
        return MANDREL_KEY if str(kind or "") in ("", "mandrel") else MODEL_BY_OP["belling"]
    try:
        return MODEL_BY_OP[operation]
    except KeyError:
        raise ValueError(f"未知 RFM 工序：{operation}（无法匹配实体化模型）") from None


def _write_measured(sections: list, path: str) -> None:
    """把会话里的 sections_full 落成 measured.json 结构，供 c1 加载器复用。"""
    payload = {"sections_full": sections, "sections": sections}
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False)


def _read_shape(step_path: str):
    from core.pp.tube.brep_sections import read_shape

    return read_shape(step_path)


def _sections_payload_from_step(step_path: str) -> list:
    """把工序实体的 STEP 截面转成 c1 加载器认识的 measured 结构。

    成型以外的工序（扩口/压扁/压弯）不能拿原始管件截面跑 c1，否则会把
    芯轴/型腔重建成原管外形；这里从刚生成的外形上提截面，再做 c1 外推。
    """
    from .routes import selfref

    rings = selfref.sections_from_step(step_path)
    payload = []
    for ring in rings:
        payload.append({
            "position": ring["centroid"].tolist(),
            "outline_3d": ring["points"].tolist(),
            "normal": ring["normal"].tolist(),
            "basis_u": ring["side"].tolist(),
            "basis_v": ring["up"].tolist(),
            "perimeter": float(ring.get("perimeter") or 0.0),
            "cumulative_length": float(ring.get("t") or 0.0),
            "measurement_status": "measured",
            "measurement_source": "shape_extract",
        })
    return payload


def _build_c1(sections: list, key: str, work_dir: str, log) -> dict:
    """C1 外推：会话截面 → 4 面实体（进程内 OCP）。"""
    from .routes import c1 as c1_route

    measured = os.path.join(work_dir, "measured.json")
    _write_measured(sections, measured)
    spec = contracts.ModelSpec(
        part="RFM",
        key=key,
        step="",
        end_treatment="c1",
        out_dir=work_dir,
        measured_path=measured,
    )
    ctx = contracts.PipelineContext(
        src_dir=work_dir,
        out_root=work_dir,
        parts=["RFM"],
        routes=("c1",),
        models=(key,),
        options={"half_samples": settings.HALF_SAMPLES, "smooth_passes": settings.SMOOTH_PASSES},
        log=log or (lambda _m: None),
    )
    result = c1_route.run(spec, ctx)
    if result.status != "ok" or not result.step:
        raise RuntimeError(result.error or "c1 路线未产出 STEP")
    return {"result": result, "step": result.step}


def _build_pipe(step_path: str, key: str, work_dir: str, log) -> dict:
    """pipe 路：FreeCADCmd 重蒙皮（子进程，方向由 directions 在主进程算好）。"""
    from .routes import pipe as pipe_route

    spec = contracts.ModelSpec(
        part="RFM",
        key=key,
        step=step_path,
        end_treatment="c1",
        out_dir=work_dir,
    )
    ctx = contracts.PipelineContext(
        src_dir=work_dir,
        out_root=work_dir,
        parts=["RFM"],
        routes=("pipe",),
        models=(key,),
        options={"direction": "auto", "export_stl": False, "open_in_freecad": False},
        log=log or (lambda _m: None),
    )
    result = pipe_route.run(spec, ctx)
    if result.status != "ok" or not result.step:
        raise RuntimeError(result.error or "pipe 路线未产出 STEP")
    return {"result": result, "step": result.step}


def _build_selfref(step_path: str, out_step: str, key: str, op: str, log) -> dict:
    from .routes import selfref

    # 页面里的 121 与流水线一样按「类别规则」自动定分型方向（平面弯 → 弯曲
    # 平面法向），否则 build_from_step 会退回截面自带的三轴对称评分，弯管就会
    # 切在偏了 20~30° 的 ±X 上。
    direction = None
    try:
        rings = selfref.sections_from_step(step_path)
        resolved = selfref._direction_from_rules(
            rings, (op, key), log or (lambda _m: None), "[RFM]"
        )
        direction = resolved.get("vector")
        if log:
            log(f"[RFM] 121 保形分型方向 {resolved.get('label')}（rule={resolved.get('rule')}）")
    except Exception as exc:  # noqa: BLE001 - 方向失败仍可退回三轴评分
        if log:
            log(f"[RFM] 121 保形分型方向自动解析失败（{exc}），退回截面自带对称轴")

    report = selfref.build_from_step(
        step_path,
        out_step,
        part="RFM",
        key=key,
        op=op,
        direction=direction,
        log=log,
    )
    info = {
        "route": "selfref",
        "faces": report.get("faces"),
        "edges": report.get("edges"),
        "solids": report.get("solids"),
        "free_edges": report.get("free_edges"),
        "parting_edges": report.get("parting_edges"),
        "valid": report.get("valid"),
        "volume_after": report.get("volume_mm3"),
        "direction": report.get("direction"),
        "sections_used": report.get("sections_used"),
        "sections_total": report.get("sections_total"),
    }
    return {"info": info, "step": report.get("step") or out_step}


def convert_shape(
    shape,
    operation: str,
    *,
    sections: Optional[list] = None,
    kind: str = "",
    keep_dir: Optional[str] = None,
    log=None,
    entity_route: Optional[str] = None,
) -> dict:
    """把刚生成的 shape 转成 4 面实体。

    Parameters
    ----------
    shape:
        RFM 生成器产出的 OCC shape / solid（尚未导出 STEP）。
    operation:
        ``shaping`` / ``belling`` / ``flattening`` / ``bending``。
    sections:
        RFM 会话里的 ``sections_full``（成型用得上；其余工序可省略）。
    kind:
        ``cavity`` / ``mandrel`` / ``""``。
    keep_dir:
        调试用：保留中间文件到该目录（默认用临时目录并在结束时删除）。

    Returns
    -------
    dict
        ``{"shape": 新 shape, "info": {...}, "step": 转换后 STEP 路径}``。
        转不出来时抛异常，由调用方决定是否退回原模型。
    """
    started = time.perf_counter()
    key = model_key(operation, kind)
    default_route = contracts.default_route(key)
    choice = str(entity_route or "").strip().lower()
    explicit = ""
    if choice in ("121", "selfref", "reference", "reference_121", "保形"):
        explicit = "121"
    elif choice in ("c1", "extrapolate", "外推"):
        explicit = "c1"
    elif choice in ("pipe", "freecad", "重蒙皮"):
        explicit = "pipe"
    volume_before = None
    try:
        from core.common.solid_geometry import volume as shape_volume

        volume_before = round(float(shape_volume(shape)), 3)
    except Exception:  # noqa: BLE001 - 体积只用于报告
        volume_before = None

    work_root = keep_dir or os.path.join(settings.OUT_ROOT, "_scratch", "rfm_bridge")
    work_dir = os.path.join(work_root, uuid.uuid4().hex[:10])
    os.makedirs(work_dir, exist_ok=True)
    src_step = os.path.join(work_dir, "source.step")
    fallback_reason = None
    used = None
    try:
        def _ensure_source_step() -> str:
            """只在需要喂 FreeCAD / 保形兜底时才导出源 STEP（c1 全程用内存截面）。"""
            if not os.path.isfile(src_step):
                from core.common.solid_geometry import step_bytes

                with open(src_step, "wb") as stream:
                    stream.write(step_bytes(shape))
            return src_step

        built = None
        if explicit == "121":
            try:
                out_step = os.path.join(work_dir, "selfref.step")
                built = _build_selfref(_ensure_source_step(), out_step, key, operation, log)
                used = "121"
            except Exception as exc:  # noqa: BLE001 - 保形失败退回 pipe
                fallback_reason = f"121 保形失败：{exc}"
        elif explicit == "c1":
            try:
                source_sections = sections if (operation == "shaping" and sections) else _sections_payload_from_step(_ensure_source_step())
                built = _build_c1(source_sections, key, work_dir, log)
                used = "c1"
            except Exception as exc:  # noqa: BLE001 - 退化到 pipe / 保形
                fallback_reason = f"c1 失败：{exc}"
        elif explicit == "pipe":
            pass
        elif default_route == "c1" and sections:
            try:
                built = _build_c1(sections, key, work_dir, log)
                used = "c1"
            except Exception as exc:  # noqa: BLE001 - 退化到 pipe / 保形
                fallback_reason = f"c1 失败：{exc}"
        elif not sections:
            fallback_reason = "会话没有 sections_full，跳过 c1"

        if built is None:
            try:
                built = _build_pipe(_ensure_source_step(), key, work_dir, log)
                used = "pipe"
            except Exception as exc:  # noqa: BLE001 - 退化到保形
                fallback_reason = (fallback_reason + "；" if fallback_reason else "") + f"pipe 失败：{exc}"
                out_step = os.path.join(work_dir, "selfref.step")
                built = _build_selfref(_ensure_source_step(), out_step, key, operation, log)
                used = "selfref"

        step_path = built["step"]
        new_shape = _read_shape(step_path)
        result = built.get("result")
        if result is not None:
            info = {
                "route": used,
                "faces": result.faces,
                "edges": result.edges,
                "solids": result.solids,
                "free_edges": result.free_edges,
                "parting_edges": result.parting_edges,
                "valid": result.valid,
                "volume_after": result.volume_mm3,
                "direction": result.direction_label or result.direction,
                "sections_used": result.sections_used,
                "sections_total": result.sections_total,
                "problems": list(result.problems or []),
            }
        else:
            info = dict(built.get("info") or {})
        info.update(
            {
                "applied": True,
                "route": "121" if used == "121" else used,
                "model_key": key,
                "default_route": default_route,
                "requested_route": explicit or "auto",
                "volume_before": volume_before,
                "fallback": fallback_reason,
                "seconds": round(time.perf_counter() - started, 3),
            }
        )
        return {"shape": new_shape, "info": info, "step": step_path}
    finally:
        if not keep_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def describe(info: Optional[dict]) -> str:
    """给日志/界面用的一句话摘要。"""
    if not info:
        return "未实体化"
    if not info.get("applied"):
        return f"未实体化：{info.get('error') or info.get('reason') or '未知原因'}"
    bits = [
        f"路线 {info.get('route')}",
        f"{info.get('faces')} 面",
        f"{info.get('solids')} 实体",
        f"分型边 {info.get('parting_edges')}",
    ]
    if info.get("fallback"):
        bits.append(f"回退：{info['fallback']}")
    return " / ".join(str(b) for b in bits)
