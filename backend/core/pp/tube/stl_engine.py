"""STL tube analysis engine — implements algorithms A and B in one place.

The only behavioural difference between A and B is whether we PCA-rotate
the mesh so the long axis aligns with +Z before the first slicing pass.
Everything downstream (centerline build, secondary cut, end-trimming) is
identical.
"""

from __future__ import annotations

import logging
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable, List, Optional, Tuple

import numpy as np

from .geometry import (
    centerline_tangents,
    cumulative_lengths,
    hull_and_inner,
    pca_long_axis,
    principal_extents_2d,
    rotation_between_vectors,
    smooth_centerline_savgol,
    sparse_indices_by_arc_length,
    trim_and_extrapolate_centerline,
    trim_and_extrapolate_series,
)
from .workers import stl_secondary_cut_chunk, stl_slice_z_chunk

logger = logging.getLogger(__name__)

try:
    import trimesh
except ImportError:  # pragma: no cover — handled at runtime
    trimesh = None  # type: ignore[assignment]


_SLICE_STEP_MM = 1.0

# 分析并行的硬性护栏：即便 worker 死锁也不能把整个分析线程拖死。
# 单 chunk 超时后跳过；wall-clock 超时后放弃并行，回退串行；
# executor 在超时/异常时都以 wait=False 关闭，避免 __exit__ 里 join 卡死。
_POOL_CHUNK_TIMEOUT_S = 120.0
_POOL_WALL_TIMEOUT_S = 300.0


def _worker_stdio_init() -> None:  # pragma: no cover — runs in subprocess
    """Worker 子进程初始化：把 stdout/stderr 指到 devnull。

    pythonw 起的子进程默认 sys.stdout=None，任何依赖库里潜在的 print() 会
    抛 AttributeError 直接干掉整个 worker，进而让主进程 as_completed()
    死等，出现「一直没结果」的死锁。这里直接兜住。
    """
    import os as _os
    import sys as _sys
    try:
        devnull = open(_os.devnull, 'w')
        _sys.stdout = devnull
        _sys.stderr = devnull
    except Exception:
        pass


def _run_pool_with_timeout(
    fn,
    chunks: List,
    *,
    max_workers: int,
    chunk_timeout: float = _POOL_CHUNK_TIMEOUT_S,
    wall_timeout: float = _POOL_WALL_TIMEOUT_S,
    stage: str = "pool",
    progress_cb: Optional[Callable[[int, str], None]] = None,
    progress_lo: int = 0,
    progress_hi: int = 100,
) -> Tuple[List, bool]:
    """Run ``fn`` over ``chunks`` in a process pool with hard timeouts.

    Returns ``(results, timed_out)``. On timeout the executor is shut down
    with ``wait=False, cancel_futures=True`` so we never block the caller.
    """
    results: List = []
    timed_out = False
    start = time.monotonic()
    futures: List = []

    executor = ProcessPoolExecutor(max_workers=max_workers, initializer=_worker_stdio_init)
    try:
        futures = [executor.submit(fn, ch) for ch in chunks]
        completed = 0
        n = len(futures)
        try:
            for fut in as_completed(futures, timeout=wall_timeout):
                elapsed_left = wall_timeout - (time.monotonic() - start)
                if elapsed_left <= 0:
                    raise FuturesTimeoutError
                try:
                    res = fut.result(timeout=min(chunk_timeout, max(1.0, elapsed_left)))
                    results.append(res)
                except FuturesTimeoutError:
                    logger.warning("[%s] 单批超时 %.0fs，跳过该 chunk", stage, chunk_timeout)
                except Exception as exc:
                    logger.warning("[%s] 子进程失败: %s", stage, exc)
                completed += 1
                if progress_cb and n > 0:
                    pct = progress_lo + int((completed / n) * (progress_hi - progress_lo))
                    _progress(progress_cb, pct, f"{stage} {completed}/{n} 批")
        except FuturesTimeoutError:
            logger.warning(
                "[%s] wall-clock 超时 %.0fs，取消剩余 futures，将回退串行",
                stage, wall_timeout,
            )
            timed_out = True
    except Exception as exc:
        logger.warning("[%s] 并行调度异常: %s", stage, exc)
        timed_out = True
    finally:
        for f in futures:
            try:
                f.cancel()
            except Exception:
                pass
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    return results, timed_out


def _moving_average_smooth(arr: np.ndarray, window: int = 9) -> np.ndarray:
    """Simple edge-preserving smoother used before 1mm re-sampling."""
    if arr.size <= 2:
        return arr.copy()
    w = int(max(3, window))
    if w % 2 == 0:
        w += 1
    if arr.size < w:
        return arr.copy()
    pad = w // 2
    padded = np.pad(arr, (pad, pad), mode="edge")
    kernel = np.ones(w, dtype=float) / float(w)
    return np.convolve(padded, kernel, mode="valid")


def _resample_to_integer_mm(
    *,
    centerline_world: np.ndarray,
    cum: np.ndarray,
    perim_arr: np.ndarray,
    wt_vals: np.ndarray,
    outer_2d_per_idx: List[Optional[List[List[float]]]],
    inner_2d_per_idx: List[Optional[List[List[float]]]],
    outline_3d_per_idx: List[Optional[List[List[float]]]],
) -> Tuple[
    np.ndarray, np.ndarray, np.ndarray, np.ndarray,
    List[Optional[List[List[float]]]],
    List[Optional[List[List[float]]]],
    List[Optional[List[List[float]]]],
]:
    """Resample output to 1mm integer arc-length grid and smooth curves.

    Target arc-length axis is: 0, 1, 2, ..., floor(total_length).
    Perimeter / wall-thickness are first smoothed then interpolated.
    Geometry payloads (outer/inner/outline) are mapped by nearest source index.
    """
    if len(cum) < 2:
        return (
            centerline_world, cum, perim_arr, wt_vals,
            outer_2d_per_idx, inner_2d_per_idx, outline_3d_per_idx,
        )

    # Ensure arc-length is strictly increasing for interpolation.
    keep = np.ones(len(cum), dtype=bool)
    keep[1:] = np.diff(cum) > 1e-9
    if not np.all(keep):
        centerline_world = centerline_world[keep]
        perim_arr = perim_arr[keep]
        wt_vals = wt_vals[keep]
        cum = cum[keep]
        outer_2d_per_idx = [v for v, k in zip(outer_2d_per_idx, keep) if k]
        inner_2d_per_idx = [v for v, k in zip(inner_2d_per_idx, keep) if k]
        outline_3d_per_idx = [v for v, k in zip(outline_3d_per_idx, keep) if k]

    if len(cum) < 2:
        return (
            centerline_world, cum, perim_arr, wt_vals,
            outer_2d_per_idx, inner_2d_per_idx, outline_3d_per_idx,
        )

    total_len = float(cum[-1])
    if total_len < 1.0:
        return (
            centerline_world, cum, perim_arr, wt_vals,
            outer_2d_per_idx, inner_2d_per_idx, outline_3d_per_idx,
        )

    # 修复要点：旧的 `floor(total_len) + 1` 会让总长刚好整数的管件被切掉最后一段，
    # 而 floor 后的 0.6 mm 尾数会被丢弃掉（例如 999.6 → 999）。改成 `total_len + 1e-9`
    # 让端点能落在离它最近的整毫米上，且不会因为浮点累加多出一段零长度段。
    target_s = np.arange(0.0, total_len + 1e-9, 1.0, dtype=float)
    if target_s.size < 2:
        target_s = np.array([0.0, total_len], dtype=float)

    # Smooth + interpolate perimeter and thickness.
    perim_sm = _moving_average_smooth(perim_arr.astype(float), window=9)
    wt_sm = _moving_average_smooth(wt_vals.astype(float), window=9)
    perim_new = np.interp(target_s, cum, perim_sm)
    wt_new = np.interp(target_s, cum, wt_sm)

    # Interpolate centerline xyz onto 1mm arc-length axis.
    xyz_new = np.column_stack([
        np.interp(target_s, cum, centerline_world[:, 0]),
        np.interp(target_s, cum, centerline_world[:, 1]),
        np.interp(target_s, cum, centerline_world[:, 2]),
    ])

    # Map shape payloads by nearest source sample.
    nearest_idx = np.searchsorted(cum, target_s, side="left")
    nearest_idx = np.clip(nearest_idx, 0, len(cum) - 1)
    for i, s in enumerate(target_s):
        j = nearest_idx[i]
        if j > 0 and abs(cum[j - 1] - s) <= abs(cum[j] - s):
            nearest_idx[i] = j - 1

    outer_new = [outer_2d_per_idx[j] for j in nearest_idx]
    inner_new = [inner_2d_per_idx[j] for j in nearest_idx]
    outline_new = [outline_3d_per_idx[j] for j in nearest_idx]

    return xyz_new, target_s, perim_new, wt_new, outer_new, inner_new, outline_new


def _progress(cb, pct: int, msg: str) -> None:
    if cb is None:
        return
    try:
        cb(int(pct), str(msg))
    except Exception:
        pass


def _load_mesh(file_path: str) -> "trimesh.Trimesh":
    mesh = trimesh.load(file_path)
    if isinstance(mesh, trimesh.Scene):
        geoms = list(getattr(mesh, "geometry", {}).values())
        if not geoms:
            raise ValueError("STL文件不含任何几何体")
        mesh = trimesh.util.concatenate(geoms)
    if mesh.is_empty:
        raise ValueError("STL文件为空")
    return mesh


def _serialize_mesh(mesh: "trimesh.Trimesh") -> bytes:
    """Export to binary STL bytes; the resulting payload pickles cheaply."""
    return mesh.export(file_type="stl")


def _parallel_slice_z(
    mesh: "trimesh.Trimesh",
    z_values: np.ndarray,
    *,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    progress_lo: int = 10,
    progress_hi: int = 55,
) -> List[dict]:
    """Slice the mesh at every Z in `z_values` using a process pool.

    Falls back to the in-process worker if multiprocessing is unavailable
    or there are very few slices.
    """
    n_total = len(z_values)
    if n_total == 0:
        return []

    cpu = max(1, (os.cpu_count() or 2) - 1)
    chunk_size = max(8, math.ceil(n_total / (cpu * 4)))

    if n_total < 16 or cpu == 1:
        mesh_bytes = _serialize_mesh(mesh)
        results = stl_slice_z_chunk((mesh_bytes, z_values.tolist()))
        results.sort(key=lambda r: r["z"])
        _progress(progress_cb, progress_hi, "一次切割完成")
        return results

    mesh_bytes = _serialize_mesh(mesh)
    chunks = [(mesh_bytes, z_values[i: i + chunk_size].tolist()) for i in range(0, n_total, chunk_size)]

    chunk_results, timed_out = _run_pool_with_timeout(
        stl_slice_z_chunk,
        chunks,
        max_workers=cpu,
        stage="一次切割",
        progress_cb=progress_cb,
        progress_lo=progress_lo,
        progress_hi=progress_hi,
    )
    results: List[dict] = []
    for chunk_res in chunk_results:
        if chunk_res:
            results.extend(chunk_res)

    # 超时或并行结果稀薄：兜底跑一次全量串行，保证一定有输出。
    if timed_out or len(results) < len(z_values) * 0.5:
        logger.warning(
            "一次切割并行结果不足（%d/%d，timed_out=%s），回退串行",
            len(results), len(z_values), timed_out,
        )
        results = stl_slice_z_chunk((mesh_bytes, z_values.tolist()))
        _progress(progress_cb, progress_hi, "一次切割（串行兜底）完成")

    results.sort(key=lambda r: r["z"])
    return results


def _parallel_secondary_cut(
    mesh: "trimesh.Trimesh",
    centerline: np.ndarray,
    tangents: np.ndarray,
    *,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    progress_lo: int = 60,
    progress_hi: int = 88,
) -> List[Optional[dict]]:
    n = len(centerline)
    if n == 0:
        return []

    items: List[Tuple[List[float], List[float], int]] = [
        (centerline[i].tolist(), tangents[i].tolist(), i) for i in range(n)
    ]

    cpu = max(1, (os.cpu_count() or 2) - 1)
    chunk_size = max(8, math.ceil(n / (cpu * 4)))

    placeholder: List[Optional[dict]] = [None] * n

    if n < 16 or cpu == 1:
        mesh_bytes = _serialize_mesh(mesh)
        results = stl_secondary_cut_chunk((mesh_bytes, items))
        for r in results:
            placeholder[r["idx"]] = r
        _progress(progress_cb, progress_hi, "二次切割完成")
        return placeholder

    mesh_bytes = _serialize_mesh(mesh)
    chunks = [(mesh_bytes, items[i: i + chunk_size]) for i in range(0, n, chunk_size)]

    chunk_results, timed_out = _run_pool_with_timeout(
        stl_secondary_cut_chunk,
        chunks,
        max_workers=cpu,
        stage="二次切割",
        progress_cb=progress_cb,
        progress_lo=progress_lo,
        progress_hi=progress_hi,
    )
    filled = 0
    for chunk_res in chunk_results:
        if not chunk_res:
            continue
        for r in chunk_res:
            placeholder[r["idx"]] = r
            filled += 1

    # 覆盖率不足或超时：串行兜底把剩下的补齐（用完整 items 全跑一遍）。
    if timed_out or filled < n * 0.5:
        logger.warning(
            "二次切割并行覆盖率不足（%d/%d，timed_out=%s），回退串行",
            filled, n, timed_out,
        )
        for r in stl_secondary_cut_chunk((mesh_bytes, items)):
            placeholder[r["idx"]] = r
        _progress(progress_cb, progress_hi, "二次切割（串行兜底）完成")

    return placeholder


def _filter_outlier_sections(raw: List[dict]) -> List[dict]:
    if len(raw) < 4:
        return raw
    perims = np.array([r["perimeter"] for r in raw], dtype=float)
    median_p = float(np.median(perims))
    if median_p <= 0:
        return raw
    keep = []
    for r, p in zip(raw, perims):
        if p <= median_p * 1.4:
            keep.append(r)
    return keep if len(keep) >= 4 else raw


def _build_centerline_from_centroids(
    raw_sections: List[dict],
) -> Tuple[np.ndarray, List[dict]]:
    """Preserve the shaped core and linearly replace only local end values.

    Endpoints cannot anchor the fit: their partial sections may have displaced
    centroids. Never flatten the first/last 20 percent onto a global core mean.
    """
    raw_sections = _filter_outlier_sections(raw_sections)
    if len(raw_sections) < 4:
        raise ValueError(f"有效切片太少: {len(raw_sections)}")
    centroids = np.array([[r['centroid_2d'][0], r['centroid_2d'][1], r['z']]
                          for r in raw_sections], dtype=float)
    n = len(centroids)
    if n > 32:
        trim = 10
        centroids[trim:-trim] = smooth_centerline_savgol(centroids[trim:-trim], window=11, poly=3)
        centroids = trim_and_extrapolate_centerline(centroids, trim=trim, n_fit=10,
                                                   stations=np.array([r['z'] for r in raw_sections]))
    return centroids, raw_sections


def _polygon_area(points) -> float:
    """Signed-shoelace polygon area (in 2D), always returns >= 0."""
    if not points or len(points) < 3:
        return 0.0
    pts = np.asarray(points, dtype=float)
    x = pts[:, 0]
    y = pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) * 0.5)


def _estimate_wall_volume(
    cum_lengths: np.ndarray,
    outer_polys: List[Optional[List[List[float]]]],
    inner_polys: List[Optional[List[List[float]]]],
    perim_arr: np.ndarray,
    wt_vals: np.ndarray,
) -> float:
    """Estimate the wall-material volume by integrating ring area along arc length.

    For each station compute outer ring area minus inner ring area; if inner
    is unavailable, fall back to ``perimeter * wall_thickness`` (thin-wall).
    Volume is the trapezoidal integral of that ring area over cumulative length.
    """
    n = len(cum_lengths)
    if n < 2:
        return 0.0
    ring_area = np.zeros(n, dtype=float)
    for i in range(n):
        outer = outer_polys[i] if i < len(outer_polys) else None
        inner = inner_polys[i] if i < len(inner_polys) else None
        a_out = _polygon_area(outer) if outer else 0.0
        a_in = _polygon_area(inner) if inner else 0.0
        if a_out > 0 and a_in > 0 and a_out > a_in:
            ring_area[i] = a_out - a_in
        else:
            per = float(perim_arr[i]) if np.isfinite(perim_arr[i]) else 0.0
            wt = float(wt_vals[i]) if np.isfinite(wt_vals[i]) else 0.0
            ring_area[i] = max(0.0, per * wt)
    avg_area = (ring_area[:-1] + ring_area[1:]) * 0.5
    dz = np.diff(cum_lengths)
    return float(np.sum(avg_area * dz))


def _format_output(
    *,
    centerline_world: np.ndarray,
    secondary: List[Optional[dict]],
    primary_fallback: List[dict],
    bounding_box_world: List[float],
    sparse_step_mm: float,
    inverse_rotation: Optional[np.ndarray] = None,
    inverse_translation: Optional[np.ndarray] = None,
    mesh_volume: Optional[float] = None,
) -> dict:
    """Build the output schema expected by api/pp.py and the front-end.

    `primary_fallback` is the list of first-pass sections (in *work* coords,
    i.e. rotated for A, original for B) and is used to backfill missing
    secondary cuts at the very ends.
    """
    n = len(centerline_world)
    if n == 0:
        return {"error": "中心线为空"}

    # Inverse transform back to world coordinates for the world-frame outline.
    def _to_world(points_3d: np.ndarray) -> np.ndarray:
        if inverse_rotation is None:
            return points_3d
        out = (inverse_rotation @ points_3d.T).T
        if inverse_translation is not None:
            out = out + inverse_translation
        return out

    # ----- assemble per-position section data (1mm step) -----
    sections_full: List[dict] = []
    perim_arr = np.zeros(n, dtype=float)
    wt_arr: List[Optional[float]] = [None] * n
    outer_2d_per_idx: List[Optional[List[List[float]]]] = [None] * n
    inner_2d_per_idx: List[Optional[List[List[float]]]] = [None] * n
    outline_3d_per_idx: List[Optional[List[List[float]]]] = [None] * n

    # Map primary fallback by index (1-to-1 with centerline because both have len N)
    for i in range(n):
        sec = secondary[i]
        if sec is not None:
            perim_arr[i] = float(sec["perimeter"])
            wt = sec.get("wall_thickness")
            wt_arr[i] = float(wt) if wt is not None else None
            outer_2d_per_idx[i] = sec.get("outer_2d")
            inner_2d_per_idx[i] = sec.get("inner_2d")
            outline_world = np.asarray(sec.get("outline_3d", []), dtype=float)
            if outline_world.size:
                if inverse_rotation is not None:
                    outline_world = _to_world(outline_world)
                outline_3d_per_idx[i] = outline_world.tolist()
        elif i < len(primary_fallback):
            pf = primary_fallback[i]
            perim_arr[i] = float(pf["perimeter"])
            wt = pf.get("wall_thickness")
            wt_arr[i] = float(wt) if wt is not None else None
            outer_2d_per_idx[i] = pf.get("outer_2d")
            inner_2d_per_idx[i] = pf.get("inner_2d")

    # ----- handle endpoint anomalies on secondary results (replace ends linearly) -----
    perim_arr = trim_and_extrapolate_series(perim_arr, trim=10, n_fit=10)
    wt_vals = np.array([w if w is not None else np.nan for w in wt_arr], dtype=float)
    if np.any(np.isnan(wt_vals)):
        # fill nans by neighbour interpolation before linear end-extrapolation
        idx = np.arange(n)
        valid = ~np.isnan(wt_vals)
        if np.any(valid):
            wt_vals = np.interp(idx, idx[valid], wt_vals[valid])
    if np.any(np.isfinite(wt_vals)):
        wt_vals = trim_and_extrapolate_series(wt_vals, trim=10, n_fit=10)

    # ----- orient the pipe so the larger-perimeter end sits at 0 mm -----
    # Compare the average perimeter over the first/last "core" window
    # (skip the trimmed/extrapolated end zones to avoid bias).
    core_window = max(5, min(20, n // 10))
    head_perim = float(np.mean(perim_arr[10: 10 + core_window])) if n > 30 else float(np.mean(perim_arr[:core_window]))
    tail_perim = float(np.mean(perim_arr[-(10 + core_window): -10])) if n > 30 else float(np.mean(perim_arr[-core_window:]))
    if tail_perim > head_perim + 1e-6:
        centerline_world = centerline_world[::-1]
        perim_arr = perim_arr[::-1]
        wt_vals = wt_vals[::-1]
        outer_2d_per_idx = list(reversed(outer_2d_per_idx))
        inner_2d_per_idx = list(reversed(inner_2d_per_idx))
        outline_3d_per_idx = list(reversed(outline_3d_per_idx))

    cum = np.asarray(cumulative_lengths(centerline_world.tolist()), dtype=float)
    # 保留原始折线长（_resample_to_integer_mm 之后 cum 会被强制对齐到 1mm 整轴，
    # 末位会被截到 floor，因此用原始 cum 计算 center_line_length 才是几何真值）。
    pre_resample_total_length = float(cum[-1]) if len(cum) > 0 else 0.0

    # ----- backfill outer_2d / inner_2d / outline_3d at trimmed ends -----
    def _first_valid_idx(arr):
        for i, val in enumerate(arr):
            if val is not None:
                return i
        return None

    def _last_valid_idx(arr):
        for i in range(len(arr) - 1, -1, -1):
            if arr[i] is not None:
                return i
        return None

    for buf in (outer_2d_per_idx, inner_2d_per_idx, outline_3d_per_idx):
        first = _first_valid_idx(buf)
        last = _last_valid_idx(buf)
        if first is None or last is None:
            continue
        for i in range(0, first):
            buf[i] = buf[first]
        for i in range(last + 1, n):
            buf[i] = buf[last]

    for i in range(n):
        entry = {
            "position": centerline_world[i].tolist(),
            "perimeter": round(float(perim_arr[i]), 2),
            "cumulative_length": round(float(cum[i]), 2),
        }
        if np.isfinite(wt_vals[i]):
            entry["wall_thickness"] = round(float(wt_vals[i]), 2)
        if outer_2d_per_idx[i] is not None:
            entry["outer_2d"] = [[round(p[0], 3), round(p[1], 3)] for p in outer_2d_per_idx[i]]
            size = principal_extents_2d(entry["outer_2d"])
            if size:
                entry["x_length"] = round(float(size[0]), 4)
                entry["y_length"] = round(float(size[1]), 4)
        if inner_2d_per_idx[i] is not None:
            entry["inner_2d"] = [[round(p[0], 3), round(p[1], 3)] for p in inner_2d_per_idx[i]]
        if outline_3d_per_idx[i] is not None:
            # Already in world coordinates. Include so the 3D viewer can
            # render an accurate highlight ring at any sampled position.
            entry["outline_3d"] = [[round(p[0], 3), round(p[1], 3), round(p[2], 3)] for p in outline_3d_per_idx[i]]
        sections_full.append(entry)

    # ----- normalize onto integer-mm axis and smooth curves -----
    # This becomes the canonical section table used downstream.
    centerline_world, cum, perim_arr, wt_vals, outer_2d_per_idx, inner_2d_per_idx, outline_3d_per_idx = _resample_to_integer_mm(
        centerline_world=centerline_world,
        cum=cum,
        perim_arr=perim_arr,
        wt_vals=wt_vals,
        outer_2d_per_idx=outer_2d_per_idx,
        inner_2d_per_idx=inner_2d_per_idx,
        outline_3d_per_idx=outline_3d_per_idx,
    )
    n = len(cum)
    sections_full = []
    for i in range(n):
        entry = {
            "position": centerline_world[i].tolist(),
            "perimeter": round(float(perim_arr[i]), 2),
            # 修复要点：之前用 `float(int(round(cum[i])))` 把 999.6 截到 999。
            # 与上面 sparse sections 保持一致，使用 round(_, 2) 保留小数位精度。
            "cumulative_length": round(float(cum[i]), 2),
        }
        if np.isfinite(wt_vals[i]):
            entry["wall_thickness"] = round(float(wt_vals[i]), 2)
        if outer_2d_per_idx[i] is not None:
            entry["outer_2d"] = [[round(p[0], 3), round(p[1], 3)] for p in outer_2d_per_idx[i]]
            size = principal_extents_2d(entry["outer_2d"])
            if size:
                entry["x_length"] = round(float(size[0]), 4)
                entry["y_length"] = round(float(size[1]), 4)
        if inner_2d_per_idx[i] is not None:
            entry["inner_2d"] = [[round(p[0], 3), round(p[1], 3)] for p in inner_2d_per_idx[i]]
        if outline_3d_per_idx[i] is not None:
            entry["outline_3d"] = [[round(p[0], 3), round(p[1], 3), round(p[2], 3)] for p in outline_3d_per_idx[i]]
        sections_full.append(entry)

    # ----- sparse sections (~every sparse_step_mm) for 3D viewport -----
    sparse_idx = sparse_indices_by_arc_length(cum, step_mm=sparse_step_mm)
    sections: List[dict] = []
    for i in sparse_idx:
        entry = {
            "position": centerline_world[i].tolist(),
            "perimeter": round(float(perim_arr[i]), 2),
            "cumulative_length": round(float(cum[i]), 2),
            "index": int(i),
        }
        if np.isfinite(wt_vals[i]):
            entry["wall_thickness"] = round(float(wt_vals[i]), 2)
        if outline_3d_per_idx[i] is not None:
            outline = outline_3d_per_idx[i] + [outline_3d_per_idx[i][0]]
            entry["outline"] = [[round(p[0], 3), round(p[1], 3), round(p[2], 3)] for p in outline]
        sections.append(entry)

    # ----- wall thickness stats over the *core* portion (exclude extrapolated ends) -----
    core_wt = [s["wall_thickness"] for s in sections_full[10: max(11, len(sections_full) - 10)] if "wall_thickness" in s]
    wt_stats = None
    if core_wt:
        arr = np.array(core_wt, dtype=float)
        wt_stats = {
            "median_mm": round(float(np.median(arr)), 2),
            "mean_mm": round(float(np.mean(arr)), 2),
            "min_mm": round(float(np.min(arr)), 2),
            "max_mm": round(float(np.max(arr)), 2),
            "count": int(len(arr)),
        }

    # ----- wall-material volume (mm^3) -----
    # Prefer the watertight-mesh volume when caller passed it in; otherwise
    # estimate by integrating ring area along the centerline.
    if mesh_volume is not None and mesh_volume > 0:
        volume_mm3 = float(mesh_volume)
    else:
        volume_mm3 = _estimate_wall_volume(
            cum_lengths=cum,
            outer_polys=outer_2d_per_idx,
            inner_polys=inner_2d_per_idx,
            perim_arr=perim_arr,
            wt_vals=wt_vals,
        )

    # center_line_length 用进入重采样前的折线末位，避免被 1mm 整轴的 floor 截短。
    center_line_length = round(float(pre_resample_total_length), 2)

    # ----- "管件长度" 的真正含义：中心线两端点的直线距离（end-to-end）-----
    # 旧的 actual_z_length 用的是 mesh 包围盒 Z 跨度：
    #   - 算法 A（PCA 主轴对齐）：旋转后 mesh 主轴方向 ≠ 端到端方向，弯管时会显著偏短；
    #   - 算法 B（不旋转）：mesh 原始 Z 跨度对水平弯管也是错的。
    # 这里把语义替换为 ||centerline[0] - centerline[-1]||，键名沿用 actual_z_length
    # 以保持前端 / DB 兼容性。
    centerline_endpoint_length = 0.0
    if centerline_world is not None and len(centerline_world) >= 2:
        try:
            p0 = np.asarray(centerline_world[0], dtype=float)
            pn = np.asarray(centerline_world[-1], dtype=float)
            if np.all(np.isfinite(p0)) and np.all(np.isfinite(pn)):
                centerline_endpoint_length = float(np.linalg.norm(pn - p0))
                if not np.isfinite(centerline_endpoint_length) or centerline_endpoint_length < 0:
                    centerline_endpoint_length = 0.0
        except Exception:
            centerline_endpoint_length = 0.0
    actual_z_length = round(centerline_endpoint_length, 2)

    # 保留 mesh 包围盒作为几何参考（前端不展示，但保留兼容）
    bounding_box_for_output = bounding_box_world if bounding_box_world else []

    return {
        "sections": sections,
        "sections_full": sections_full,
        "center_line_points": centerline_world.tolist(),
        "center_line_length": center_line_length,
        "actual_z_length": actual_z_length,
        # 新增字段：明示"中心线两端点直线距离"，避免下游误用旧语义
        "centerline_endpoint_length": actual_z_length,
        "wall_thickness_stats": wt_stats,
        "bounding_box": bounding_box_for_output,
        "volume": round(float(volume_mm3), 2),
    }


def analyze_stl_tube(
    file_path: str,
    *,
    algorithm: str = "A",
    sparse_step_mm: float = 25.0,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    if trimesh is None:
        return {"error": "trimesh 未安装，无法分析 STL"}

    t0 = time.monotonic()
    logger.info("analyze_stl_tube 开始: file=%s algorithm=%s", file_path, algorithm)
    try:
        _progress(progress_callback, 2, "加载 STL 模型...")
        mesh = _load_mesh(file_path)
        logger.info("mesh 加载完成: verts=%d faces=%d, +%.2fs",
                    len(mesh.vertices), len(mesh.faces), time.monotonic() - t0)

        if algorithm.upper() == "A":
            _progress(progress_callback, 6, "PCA 主轴对齐...")
            vertices = mesh.vertices
            center = vertices.mean(axis=0)
            long_axis = pca_long_axis(vertices)
            rotation = rotation_between_vectors(long_axis, np.array([0.0, 0.0, 1.0]))
            rotated_verts = (rotation @ (vertices - center).T).T
            work_mesh = trimesh.Trimesh(vertices=rotated_verts, faces=mesh.faces)
            inverse_rotation = rotation.T
            inverse_translation = center
        else:
            _progress(progress_callback, 6, "B 算法：跳过旋转，使用原始坐标...")
            work_mesh = mesh
            inverse_rotation = None
            inverse_translation = None

        bounds = work_mesh.bounds
        z_min = float(bounds[0][2]) + 0.2
        z_max = float(bounds[1][2]) - 0.2
        if z_max - z_min < 5.0:
            return {"error": f"模型 Z 方向范围太小: {z_max - z_min:.1f}mm"}

        z_values = np.arange(z_min, z_max + 1e-9, _SLICE_STEP_MM)
        _progress(progress_callback, 10, f"开始一次切割 ({len(z_values)} 个截面)...")
        logger.info("开始一次切割: n=%d z_range=[%.2f, %.2f] +%.2fs",
                    len(z_values), z_min, z_max, time.monotonic() - t0)

        primary = _parallel_slice_z(
            work_mesh,
            z_values,
            progress_cb=progress_callback,
            progress_lo=10,
            progress_hi=55,
        )
        logger.info("一次切割完成: primary=%d 有效, +%.2fs", len(primary), time.monotonic() - t0)
        if len(primary) < 4:
            return {"error": f"有效一次切割截面太少: {len(primary)}"}

        _progress(progress_callback, 56, "构建中心线...")
        centerline_work, primary_filtered = _build_centerline_from_centroids(primary)
        tangents = centerline_tangents(centerline_work)
        logger.info("中心线构建完成: n=%d, +%.2fs", len(centerline_work), time.monotonic() - t0)

        _progress(progress_callback, 60, "二次切割（垂直于中心线）...")
        secondary = _parallel_secondary_cut(
            work_mesh,
            centerline_work,
            tangents,
            progress_cb=progress_callback,
            progress_lo=60,
            progress_hi=88,
        )
        logger.info("二次切割完成: +%.2fs", time.monotonic() - t0)

        _progress(progress_callback, 90, "构建输出...")
        # Translate centerline back to world coordinates.
        if inverse_rotation is not None:
            centerline_world = (inverse_rotation @ centerline_work.T).T + inverse_translation
        else:
            centerline_world = centerline_work

        # Map primary_filtered into per-index slots (length N == len(centerline_work)).
        # primary_filtered length might be slightly different (we trimmed/extrapolated),
        # so we just slice it to N for the fallback view.
        primary_per_idx: List[dict] = []
        for i in range(len(centerline_work)):
            if i < len(primary_filtered):
                primary_per_idx.append(primary_filtered[i])
            else:
                primary_per_idx.append(primary_filtered[-1] if primary_filtered else {"perimeter": 0.0})

        bbox = mesh.bounds  # original mesh bbox
        bbox_world = [
            float(bbox[0][0]), float(bbox[0][1]), float(bbox[0][2]),
            float(bbox[1][0]), float(bbox[1][1]), float(bbox[1][2]),
        ]

        # If the STL/STP describes a closed wall solid, ``mesh.volume`` gives
        # the wall material volume directly. Open-shell meshes return 0 or
        # a negative number — in that case we fall back to section integration
        # inside _format_output.
        mesh_volume: Optional[float] = None
        try:
            if getattr(mesh, "is_watertight", False):
                v = float(mesh.volume)
                if np.isfinite(v) and v > 0:
                    mesh_volume = v
        except Exception:
            mesh_volume = None

        out = _format_output(
            centerline_world=centerline_world,
            secondary=secondary,
            primary_fallback=primary_per_idx,
            bounding_box_world=bbox_world,
            sparse_step_mm=sparse_step_mm,
            inverse_rotation=inverse_rotation,
            inverse_translation=inverse_translation,
            mesh_volume=mesh_volume,
        )
        out["algorithm"] = algorithm.upper()
        _progress(progress_callback, 95, "分析完成")
        logger.info("analyze_stl_tube 完成: 总耗时 %.2fs", time.monotonic() - t0)
        return out
    except ValueError as exc:
        logger.exception("STL 分析 ValueError（+%.2fs）", time.monotonic() - t0)
        return {"error": str(exc)}
    except Exception as exc:  # pragma: no cover
        logger.exception("STL 分析失败（+%.2fs）", time.monotonic() - t0)
        return {"error": f"STL 分析失败: {exc}"}
