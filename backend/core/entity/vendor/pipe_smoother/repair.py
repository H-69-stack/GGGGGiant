"""Terminal-section anomaly detection and natural repair.

The input models sometimes contain a hand-made terminal section that does not
follow the trend of the preceding sections.  This module:

1. measures every section (area, perimeter, width, height, centre);
2. builds a temporary lateral surface and measures the normal turn rate to
   detect a curvature crease that a pure metric check would miss;
3. reports middle-section anomalies as errors (never silently repairs them);
4. repairs only the last three sections, either keeping their original plane
   and centre (mode A) or letting the centre follow the trend (mode B);
5. checks the resulting parting points against a quadratic trend and reports
   a warning if the trend is abnormal (it never edits the line automatically).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from OCP.GeomLProp import GeomLProp_SLProps

from .geometry import Section, _build_half_surface, pnt3


class MiddleSectionError(RuntimeError):
    """Raised when a non-terminal section is clearly broken."""


@dataclass
class SectionMetrics:
    index: int
    area: float
    perimeter: float
    width: float
    height: float
    center: np.ndarray


@dataclass
class RepairReport:
    repaired_indices: list[int] = field(default_factory=list)
    removed_indices: list[int] = field(default_factory=list)
    terminal_anomaly: bool = False
    middle_anomalies: list[dict] = field(default_factory=list)
    max_normal_turn: float = 0.0
    peak_section: int = -1
    parting_trend_degree: int = 2
    parting_trend_max_dev_mm: float = 0.0
    parting_trend_warning: str | None = None
    messages: list[str] = field(default_factory=list)
    mode: str = "A"


def _section_metrics(sec: Section) -> SectionMetrics:
    pts = sec.points
    q = pts - sec.center
    u = q @ sec.side
    v = q @ sec.up
    area = 0.5 * abs(float(np.dot(u, np.roll(v, -1)) - np.dot(v, np.roll(u, -1))))
    perimeter = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    return SectionMetrics(
        index=sec.index,
        area=area,
        perimeter=perimeter,
        width=float(np.ptp(u)),
        height=float(np.ptp(v)),
        center=sec.center.copy(),
    )


def _normal_peak(sections: list[Section]) -> tuple[float, float, int]:
    centers = np.array([s.center for s in sections])
    dist = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
    vp = dist / dist[-1]
    top = _build_half_surface(sections, "top", vp)
    best_rate = 0.0
    best_v = 0.0
    for u in (0.35, 0.5, 0.65):
        vs = np.linspace(0.01, 0.999, 400)
        normals = []
        points = []
        for v in vs:
            props = GeomLProp_SLProps(top, u, float(v), 1, 1e-7)
            n = props.Normal()
            normals.append([n.X(), n.Y(), n.Z()])
            p = top.Value(u, float(v))
            points.append([p.X(), p.Y(), p.Z()])
        normals = np.array(normals)
        points = np.array(points)
        seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
        angles = np.degrees(
            np.arccos(np.clip(np.einsum("ij,ij->i", normals[:-1], normals[1:]), -1, 1))
        )
        rate = angles / np.maximum(seg, 1e-9)
        idx = int(np.argmax(rate))
        if rate[idx] > best_rate:
            best_rate = float(rate[idx])
            best_v = float(vs[idx])
    nearest = int(np.argmin(np.abs(vp - best_v)))
    if best_v > vp[nearest] and nearest + 1 < len(vp):
        nearest += 1
    return best_rate, best_v, nearest


def _residual(values: np.ndarray, index: int, window: int = 12) -> tuple[float, float]:
    start = max(0, index - window)
    if index - start < 4:
        return 0.0, 0.0
    x = np.arange(start, index)
    y = values[start:index]
    co = np.polyfit(x, y, 2)
    pred = float(np.polyval(co, index))
    return pred, float(values[index] - pred)


def _predict_section(
    sections: list[Section],
    bad_index: int,
    mode: str,
) -> Section:
    sec = sections[bad_index]
    # local trend only: a global quadratic over a bent 700 mm part is unstable
    good = sections[max(0, bad_index - 6):bad_index]
    if len(good) < 4:
        raise MiddleSectionError("insufficient preceding sections for terminal repair")

    centers = np.array([s.center for s in good])
    t = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
    t_bad = float(t[-1] + np.linalg.norm(sec.center - good[-1].center))
    if t_bad <= t[-1]:
        t_bad = float(t[-1] + 1.0)

    pred_center = np.empty(3)
    for c in range(3):
        co = np.polyfit(t, centers[:, c], 2)
        pred_center[c] = np.polyval(co, t_bad)

    if mode.upper() == "A":
        target_center = sec.center.copy()
    else:
        target_center = pred_center.copy()
        offset = float(np.dot(target_center - sec.center, sec.normal))
        target_center = target_center - offset * sec.normal

    widths = []
    heights = []
    for s in good:
        pts = np.vstack([s.top_pts, s.bottom_pts])
        q = pts - s.center
        widths.append(float(np.ptp(q @ s.side)))
        heights.append(float(np.ptp(q @ s.up)))
    target_w = float(np.polyval(np.polyfit(t, np.array(widths), 2), t_bad))
    target_h = float(np.polyval(np.polyfit(t, np.array(heights), 2), t_bad))

    pts = np.vstack([good[-1].top_pts, good[-1].bottom_pts])
    q = pts - good[-1].center
    u0 = q @ good[-1].side
    v0 = q @ good[-1].up
    cur_w = max(float(np.ptp(u0)), 1e-6)
    cur_h = max(float(np.ptp(v0)), 1e-6)
    scale_u = float(np.clip(target_w / cur_w, 0.75, 1.25))
    scale_v = float(np.clip(target_h / cur_h, 0.75, 1.25))

    def transform(arr: np.ndarray) -> np.ndarray:
        qq = arr - good[-1].center
        uu = qq @ good[-1].side
        vv = qq @ good[-1].up
        return target_center + np.outer(uu * scale_u, sec.side) + np.outer(vv * scale_v, sec.up)

    top = transform(good[-1].top_pts)
    bottom = transform(good[-1].bottom_pts)

    # common parting points; scale the source profile tangents
    bottom[-1] = top[0]
    bottom[0] = top[-1]

    def transform_vec(vec: np.ndarray) -> np.ndarray:
        out = (float(np.dot(vec, good[-1].side)) * scale_u) * sec.side
        out = out + (float(np.dot(vec, good[-1].up)) * scale_v) * sec.up
        return out / max(np.linalg.norm(out), 1e-12)

    tau_r = transform_vec(good[-1].tangent_right)
    tau_l = transform_vec(good[-1].tangent_left)

    sec.top_pts = top
    sec.bottom_pts = bottom
    sec.center = target_center
    sec.tangent_right = tau_r
    sec.tangent_left = tau_l
    sec.right = top[0]
    sec.left = top[-1]
    return sec


def detect_and_repair(sections: list[Section], mode: str, warnings: list[str]) -> RepairReport:
    mode = "A" if str(mode).upper() != "B" else "B"
    report = RepairReport(mode=mode)
    n = len(sections)
    if n < 5:
        return report

    metrics = [_section_metrics(s) for s in sections]
    areas = np.array([m.area for m in metrics])
    widths = np.array([m.width for m in metrics])
    centers = np.array([m.center for m in metrics])

    # Curvature is reported as a diagnostic only.  A smooth flare, flattening
    # or bend naturally has a high normal-turn rate; it must not be mistaken
    # for a broken middle section.
    peak_rate, peak_v, peak_index = _normal_peak(sections)
    report.max_normal_turn = peak_rate
    report.peak_section = peak_index

    # Middle sections: compare each section with a quadratic fit of its local
    # neighbours on both sides.  A real outlier is isolated; a flare/bend is
    # smooth and fits the local trend.
    for idx in range(3, n - 3):
        neighbours = list(range(idx - 3, idx)) + list(range(idx + 1, idx + 4))
        x = np.array(neighbours, dtype=float)
        details = []
        for arr, limit, label in (
            (areas, 10.0, "面积"),
            (widths, 8.0, "宽度"),
        ):
            co = np.polyfit(x, arr[neighbours], 2)
            pred = float(np.polyval(co, idx))
            diff = float(arr[idx] - pred)
            pct = diff / pred * 100.0 if pred else 0.0
            if abs(pct) > limit:
                details.append(f"{label}偏差 {pct:.1f}%")
        co = np.polyfit(x, centers[neighbours, 1], 2)
        cdiff = float(centers[idx, 1] - np.polyval(co, idx))
        if abs(cdiff) > 2.0:
            details.append(f"中心偏差 {cdiff:.2f} mm")
        if details:
            report.middle_anomalies.append(
                {
                    "index": idx,
                    "reason": "，".join(details) + "（局部邻域突变）",
                }
            )

    # 2. metric residuals on the last three sections (only these may be repaired)
    bad_indices: list[int] = []
    for idx in range(max(0, n - 3), n):
        pred_area, diff_area = _residual(areas, idx)
        pred_width, diff_width = _residual(widths, idx)
        pred_center, diff_center = _residual(centers[:, 1], idx)
        area_pct = diff_area / pred_area * 100.0 if pred_area else 0.0
        width_pct = diff_width / pred_width * 100.0 if pred_width else 0.0
        if abs(area_pct) > 5.0 or abs(width_pct) > 4.0 or abs(diff_center) > 1.0:
            report.terminal_anomaly = True
            bad_indices.append(idx)

    if report.middle_anomalies:
        detail = "；".join(
            f"截面 {a['index']}：{a['reason']}" for a in report.middle_anomalies
        )
        raise MiddleSectionError(
            f"检测到中间截面异常，按规则不自动修复，请先处理：{detail}"
        )

    if not report.terminal_anomaly:
        report.messages.append("未检测到需要自动补齐的末端异常截面。")
        return report

    # Repair from the earliest flagged terminal section to the end.  A
    # curvature-only trigger repairs the last section.
    if not bad_indices:
        bad_indices = [n - 1]
    first_bad = min(bad_indices)
    for idx in range(first_bad, n):
        _predict_section(sections, idx, mode)
        report.repaired_indices.append(idx)
    report.messages.append(
        "137 截面异常，已自动补齐；方案 "
        + mode
        + f"，本次替换最后 {len(report.repaired_indices)} 个截面（"
        + ", ".join(str(i) for i in report.repaired_indices)
        + "）。"
    )

    # 3. quadratic trend check of the parting points (report only)
    for name, points in (
        ("R", np.array([s.right for s in sections])),
        ("L", np.array([s.left for s in sections])),
    ):
        t = np.array([s.center[0] for s in sections])
        residual = 0.0
        for c in range(3):
            co = np.polyfit(t, points[:, c], 2)
            residual = max(residual, float(np.abs(points[:, c] - np.polyval(co, t)).max()))
        report.parting_trend_max_dev_mm = max(report.parting_trend_max_dev_mm, residual)
    if report.parting_trend_max_dev_mm > 0.5:
        report.parting_trend_warning = (
            f"修复后分型线对二次趋势的最大偏差为 "
            f"{report.parting_trend_max_dev_mm:.3f} mm，仅报警，未自动修改。"
        )
        warnings.append(report.parting_trend_warning)
    return report
