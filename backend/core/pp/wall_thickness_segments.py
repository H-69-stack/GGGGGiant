"""Wall-thickness segmentation.

Given the 1 mm-sampled centerline / wall-thickness data produced by the
upload-analysis engine, automatically detect the seven canonical
segments:

    TL1 | TLM1 | TL2 | TLM2 | TL3 | TLM3 | TL4

A "flat" segment (TL*) is a region where wall thickness is approximately
constant; a "transition" segment (TLM*) is a region where the thickness
ramps up or down smoothly. We segment the curve by binarising
|d(thickness)/d(length)| against a threshold derived from the noise
floor of the signal itself, then merge short runs into the surrounding
flat regions.

If the curve has fewer than three flat plateaus we still try to do
something sensible (e.g. a pure tapered tube becomes a single transition,
mapped to TL1+TLM1+TL2).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np


_FLAT_LABEL = "flat"
_TRANS_LABEL = "transition"


def _smooth(values: np.ndarray, window: int = 11, poly: int = 2) -> np.ndarray:
    if len(values) < 5:
        return values
    try:
        from scipy.signal import savgol_filter
    except Exception:
        kernel = np.ones(window) / window
        return np.convolve(values, kernel, mode="same")

    w = min(window, len(values) if len(values) % 2 == 1 else len(values) - 1)
    if w < poly + 2:
        return values
    if w % 2 == 0:
        w -= 1
    return savgol_filter(values, w, poly)


def _label_flat_or_transition(
    lengths: np.ndarray,
    thickness: np.ndarray,
    slope_threshold: float,
    min_flat_mm: float,
    min_trans_mm: float,
) -> np.ndarray:
    """Classify each sample into flat / transition based on local slope."""
    n = len(lengths)
    if n < 4:
        return np.full(n, _FLAT_LABEL, dtype=object)

    # First derivative (mm thickness per mm length)
    dt = np.gradient(thickness, lengths)
    is_flat = np.abs(dt) <= slope_threshold

    # Convert to run-length and prune short runs to remove noisy flips
    labels = np.where(is_flat, _FLAT_LABEL, _TRANS_LABEL)

    def _prune(labels: np.ndarray, target_label: str, min_mm: float) -> np.ndarray:
        out = labels.copy()
        i = 0
        while i < n:
            j = i
            while j < n and out[j] == labels[i]:
                j += 1
            run_len = float(lengths[min(j, n - 1)] - lengths[i])
            if labels[i] == target_label and run_len < min_mm and 0 < i and j < n:
                # Reassign this short run to whichever side it borders (prefer flat)
                left = out[i - 1]
                right = out[j]
                replacement = left if left == right else (left if left == _FLAT_LABEL else right)
                out[i:j] = replacement
            i = j
        return out

    labels = _prune(labels, _TRANS_LABEL, min_trans_mm)
    labels = _prune(labels, _FLAT_LABEL, min_flat_mm)
    return labels


def _runs_from_labels(labels: np.ndarray) -> List[Dict[str, int]]:
    runs: List[Dict[str, int]] = []
    if len(labels) == 0:
        return runs
    start = 0
    for i in range(1, len(labels)):
        if labels[i] != labels[start]:
            runs.append({"label": str(labels[start]), "start": start, "end": i - 1})
            start = i
    runs.append({"label": str(labels[start]), "start": start, "end": len(labels) - 1})
    return runs


def _segment_stats(
    lengths: np.ndarray,
    thickness: np.ndarray,
    start: int,
    end: int,
) -> Dict[str, float]:
    seg_t = thickness[start: end + 1]
    return {
        "length_mm": float(lengths[end] - lengths[start]),
        "mean_thickness": float(np.mean(seg_t)),
        "median_thickness": float(np.median(seg_t)),
        "std_thickness": float(np.std(seg_t)),
        "min_thickness": float(np.min(seg_t)),
        "max_thickness": float(np.max(seg_t)),
    }


def _normalise_to_seven(
    runs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Force the run list into the canonical 7-segment shape.

    The downstream code expects: TL1, TLM1, TL2, TLM2, TL3, TLM3, TL4.
    Real-world curves rarely give us all seven, so we pad/merge:
        - If the first run is a transition, prepend an empty flat (TL1=0).
        - If the last run is a transition, append an empty flat (TL_n=0).
        - Merge consecutive same-label runs.
        - If we end up with more than 7 segments, merge the smallest
          neighbours until we have 7.
        - If fewer, pad the remainder with zero-length empty flats.
    """
    if not runs:
        return []

    # Merge consecutive same-label runs (shouldn't happen but defensive)
    merged: List[Dict[str, Any]] = []
    for r in runs:
        if merged and merged[-1]["label"] == r["label"]:
            merged[-1]["end"] = r["end"]
            merged[-1].update({"length_mm": merged[-1]["length_mm"] + r["length_mm"]})
        else:
            merged.append(dict(r))

    # Pad front/back with empty flats so we always start AND end with a flat
    if merged[0]["label"] == _TRANS_LABEL:
        merged.insert(0, {
            "label": _FLAT_LABEL, "start": merged[0]["start"], "end": merged[0]["start"],
            "length_mm": 0.0, "mean_thickness": merged[0].get("mean_thickness", 0.0),
            "median_thickness": merged[0].get("median_thickness", 0.0),
            "std_thickness": 0.0,
            "min_thickness": merged[0].get("min_thickness", 0.0),
            "max_thickness": merged[0].get("max_thickness", 0.0),
        })
    if merged[-1]["label"] == _TRANS_LABEL:
        last = merged[-1]
        merged.append({
            "label": _FLAT_LABEL, "start": last["end"], "end": last["end"],
            "length_mm": 0.0, "mean_thickness": last.get("mean_thickness", 0.0),
            "median_thickness": last.get("median_thickness", 0.0),
            "std_thickness": 0.0,
            "min_thickness": last.get("min_thickness", 0.0),
            "max_thickness": last.get("max_thickness", 0.0),
        })

    # Truncate to at most 7 (greedy: drop the shortest transition with its
    # neighbours merged together).
    while len(merged) > 7:
        trans_indices = [i for i, r in enumerate(merged) if r["label"] == _TRANS_LABEL]
        if not trans_indices:
            break
        idx = min(trans_indices, key=lambda i: merged[i]["length_mm"])
        if idx == 0 or idx == len(merged) - 1:
            merged.pop(idx)
            continue
        # Merge idx-1, idx, idx+1 into a single flat with averaged thickness
        left = merged[idx - 1]
        right = merged[idx + 1]
        combined_len = left["length_mm"] + merged[idx]["length_mm"] + right["length_mm"]
        if combined_len > 0:
            avg_t = (
                left["length_mm"] * left["mean_thickness"]
                + merged[idx]["length_mm"] * merged[idx]["mean_thickness"]
                + right["length_mm"] * right["mean_thickness"]
            ) / combined_len
        else:
            avg_t = left["mean_thickness"]
        merged[idx - 1: idx + 2] = [{
            "label": _FLAT_LABEL,
            "start": left["start"], "end": right["end"],
            "length_mm": combined_len,
            "mean_thickness": float(avg_t),
            "median_thickness": float(avg_t),
            "std_thickness": 0.0,
            "min_thickness": min(left["min_thickness"], right["min_thickness"]),
            "max_thickness": max(left["max_thickness"], right["max_thickness"]),
        }]

    # Pad to exactly 7 — append zero-length transitions then flats at the tail
    while len(merged) < 7:
        # alternate insertion to maintain F/T/F/T/F/T/F pattern
        target = _TRANS_LABEL if len(merged) % 2 == 1 else _FLAT_LABEL
        last = merged[-1]
        merged.append({
            "label": target,
            "start": last["end"], "end": last["end"],
            "length_mm": 0.0,
            "mean_thickness": last["mean_thickness"],
            "median_thickness": last["median_thickness"],
            "std_thickness": 0.0,
            "min_thickness": last["min_thickness"],
            "max_thickness": last["max_thickness"],
        })

    return merged


def detect_wall_thickness_segments(
    sections_full: List[Dict[str, Any]],
    *,
    smooth_window_mm: float = 11.0,
    slope_threshold: Optional[float] = None,
    min_flat_mm: float = 20.0,
    min_trans_mm: float = 5.0,
) -> Dict[str, Any]:
    """Detect wall-thickness segmentation from analyzed sections.

    Returns dict with keys:
        - 'curve': list of {length, thickness, perimeter} (1mm step)
        - 'segments': normalised 7-segment list
        - 'boundaries_mm': cumulative arc-length positions of segment boundaries
        - 'tl_t_values': dict with TL1..TL4, TLM1..TLM3, T1..T4
    """
    if not sections_full:
        return {"error": "无截面数据"}

    lengths: List[float] = []
    thicknesses: List[float] = []
    perimeters: List[float] = []
    for s in sections_full:
        cum = s.get("cumulative_length")
        wt = s.get("wall_thickness")
        p = s.get("perimeter")
        if cum is None or wt is None:
            continue
        lengths.append(float(cum))
        thicknesses.append(float(wt))
        perimeters.append(float(p) if p is not None else 0.0)

    if len(lengths) < 10:
        return {"error": f"有效采样点太少: {len(lengths)}"}

    lengths_arr = np.array(lengths, dtype=float)
    thickness_arr = np.array(thicknesses, dtype=float)

    # Resample to a strict 1mm grid (defensive: sections_full *should* already be 1mm)
    grid_step = 1.0
    grid_lengths = np.arange(lengths_arr[0], lengths_arr[-1] + 1e-9, grid_step)
    grid_thickness = np.interp(grid_lengths, lengths_arr, thickness_arr)
    grid_perim = np.interp(grid_lengths, lengths_arr, np.array(perimeters))

    # Smooth
    window = max(5, int(round(smooth_window_mm)))
    if window % 2 == 0:
        window += 1
    smoothed = _smooth(grid_thickness, window=window, poly=2)

    if slope_threshold is None:
        # Auto threshold from the noise floor of the smoothed signal.
        baseline = np.abs(np.gradient(smoothed, grid_lengths))
        slope_threshold = float(np.percentile(baseline, 35)) * 2.5
        slope_threshold = max(slope_threshold, 0.005)  # mm/mm — at least 0.5%

    labels = _label_flat_or_transition(
        grid_lengths, smoothed, slope_threshold, min_flat_mm, min_trans_mm
    )
    raw_runs = _runs_from_labels(labels)

    enriched_runs: List[Dict[str, Any]] = []
    for r in raw_runs:
        s, e = r["start"], r["end"]
        stats = _segment_stats(grid_lengths, smoothed, s, e)
        enriched_runs.append({**r, **stats})

    normalised = _normalise_to_seven(enriched_runs)

    # Map to TL/T values. The canonical layout is:
    #   index 0 → TL1 (flat, T1)
    #   index 1 → TLM1 (transition)
    #   index 2 → TL2 (flat, T2)
    #   index 3 → TLM2 (transition)
    #   index 4 → TL3 (flat, T3)
    #   index 5 → TLM3 (transition)
    #   index 6 → TL4 (flat, T4)
    tl_keys = ["TL1", "TLM1", "TL2", "TLM2", "TL3", "TLM3", "TL4"]
    t_keys_per_flat = ["T1", "T2", "T3", "T4"]  # 4 flats interleaved with 3 trans
    tl_t_values: Dict[str, float] = {k: 0.0 for k in tl_keys}
    for k in t_keys_per_flat:
        tl_t_values[k] = 0.0

    flat_idx = 0
    for i, run in enumerate(normalised):
        key = tl_keys[i] if i < len(tl_keys) else f"extra_{i}"
        tl_t_values[key] = round(float(run["length_mm"]), 2)
        if run["label"] == _FLAT_LABEL and flat_idx < len(t_keys_per_flat):
            tl_t_values[t_keys_per_flat[flat_idx]] = round(float(run["mean_thickness"]), 2)
            flat_idx += 1

    # Cumulative boundary positions (arc length, mm)
    boundaries = [float(grid_lengths[0])]
    cum = float(grid_lengths[0])
    for run in normalised:
        cum += float(run["length_mm"])
        boundaries.append(cum)

    curve = [
        {
            "length_mm": float(l),
            "thickness_raw": float(g),
            "thickness_smoothed": float(s),
            "perimeter_mm": float(p),
        }
        for l, g, s, p in zip(grid_lengths, grid_thickness, smoothed, grid_perim)
    ]

    return {
        "success": True,
        "curve": curve,
        "segments": [
            {
                "label": run["label"],
                "length_mm": round(float(run["length_mm"]), 2),
                "mean_thickness": round(float(run["mean_thickness"]), 2),
                "median_thickness": round(float(run["median_thickness"]), 2),
                "std_thickness": round(float(run["std_thickness"]), 2),
                "min_thickness": round(float(run["min_thickness"]), 2),
                "max_thickness": round(float(run["max_thickness"]), 2),
            }
            for run in normalised
        ],
        "boundaries_mm": [round(b, 2) for b in boundaries],
        "tl_t_values": tl_t_values,
        "stats": {
            "total_length_mm": round(float(grid_lengths[-1] - grid_lengths[0]), 2),
            "slope_threshold_used": round(float(slope_threshold), 5),
            "smooth_window_mm": int(window),
        },
    }


_RECOMMEND_LENGTH_KEYS = ("TL1", "TLM1", "TL2", "TLM2", "TL3", "TLM3", "TL4")
_RECOMMEND_THICKNESS_KEYS = {"TL1": "T1", "TL2": "T2", "TL3": "T3", "TL4": "T4"}
_PLATEAU_TOLERANCE_MM = 0.1
_MIN_OUTLIER_PLATEAU_MM = 20.0


def _empty_wall_fields() -> Dict[str, Optional[float]]:
    fields: Dict[str, Optional[float]] = {key: None for key in _RECOMMEND_LENGTH_KEYS}
    for key in _RECOMMEND_THICKNESS_KEYS.values():
        fields[key] = None
    return fields


def _wall_grid(sections_full: List[Dict[str, Any]]):
    lengths: List[float] = []
    thicknesses: List[float] = []
    for section in sections_full or []:
        position = section.get("cumulative_length")
        thickness = section.get("wall_thickness")
        if position is None or thickness is None:
            continue
        try:
            lengths.append(float(position))
            thicknesses.append(float(thickness))
        except (TypeError, ValueError):
            continue
    if len(lengths) < 10:
        return None
    lengths_arr = np.asarray(lengths, dtype=float)
    thickness_arr = np.asarray(thicknesses, dtype=float)
    grid_lengths = np.arange(lengths_arr[0], lengths_arr[-1] + 1e-9, 1.0)
    grid_thickness = np.interp(grid_lengths, lengths_arr, thickness_arr)
    return grid_lengths, grid_thickness


def _plateau_thickness(grid_thickness: np.ndarray, start: int, end: int) -> float:
    return round(float(np.median(grid_thickness[start: end + 1])), 1)


def _split_plateau_outliers(
    runs: List[Dict[str, Any]],
    grid_lengths: np.ndarray,
    grid_thickness: np.ndarray,
) -> List[Dict[str, Any]]:
    """Pull 1.46-type ripples back into a 1.4 plateau.

    A sample within 0.1 mm of the plateau's 1-decimal thickness stays on
    that plateau. A run farther than 0.1 mm becomes its own plateau only
    when it is at least 20 mm long. Shorter spikes are absorbed.
    """
    split_runs: List[Dict[str, Any]] = []
    for run in runs:
        if run.get("label") != _FLAT_LABEL or int(run["end"]) <= int(run["start"]):
            split_runs.append(run)
            continue
        start = int(run["start"])
        end = int(run["end"])
        plateau = _plateau_thickness(grid_thickness, start, end)
        deviant = np.abs(grid_thickness[start: end + 1] - plateau) > _PLATEAU_TOLERANCE_MM + 1e-9
        pieces: List[Dict[str, Any]] = []
        cursor = 0
        count = end - start + 1
        while cursor < count:
            nxt = cursor
            while nxt < count and bool(deviant[nxt]) == bool(deviant[cursor]):
                nxt += 1
            piece_start = start + cursor
            piece_end = start + nxt - 1
            is_deviant = bool(deviant[cursor])
            if nxt < count:
                piece_length = float(grid_lengths[start + nxt] - grid_lengths[piece_start])
            else:
                piece_length = float(run["length_mm"]) - sum(item["length_mm"] for item in pieces)
            if is_deviant and piece_length < _MIN_OUTLIER_PLATEAU_MM:
                is_deviant = False
            pieces.append({
                "deviant": is_deviant,
                "start": piece_start,
                "end": piece_end,
                "length_mm": piece_length,
            })
            cursor = nxt
        merged: List[Dict[str, Any]] = []
        for piece in pieces:
            if merged and merged[-1]["deviant"] == piece["deviant"]:
                merged[-1]["end"] = piece["end"]
                merged[-1]["length_mm"] += piece["length_mm"]
            else:
                merged.append(dict(piece))
        if len(merged) == 1 and not merged[0]["deviant"]:
            split_runs.append(run)
            continue
        for index, piece in enumerate(merged):
            if index > 0:
                boundary = int(merged[index - 1]["end"])
                split_runs.append({
                    "label": _TRANS_LABEL,
                    "start": boundary,
                    "end": boundary,
                    "length_mm": 0.0,
                    "mean_thickness": plateau,
                    "median_thickness": plateau,
                    "std_thickness": 0.0,
                    "min_thickness": plateau,
                    "max_thickness": plateau,
                })
            stats = _segment_stats(grid_lengths, grid_thickness, piece["start"], piece["end"])
            stats["length_mm"] = float(piece["length_mm"])
            split_runs.append({
                "label": _FLAT_LABEL,
                "start": piece["start"],
                "end": piece["end"],
                **stats,
            })
    return split_runs


def _collapse_same_plateau_gaps(
    runs: List[Dict[str, Any]],
    grid_thickness: np.ndarray,
) -> List[Dict[str, Any]]:
    """Join a bump that leaves a plateau and comes back to the same thickness.

    A 1.46 ripple between two 1.4 plateaus is not a new wall. A 1.60 run is
    separated later, after the surrounding samples are one plateau again.
    """
    current = list(runs)
    changed = True
    while changed:
        changed = False
        merged: List[Dict[str, Any]] = []
        index = 0
        while index < len(current):
            if index + 2 < len(current):
                left, middle, right = current[index: index + 3]
                same_labels = (
                    left.get("label") == _FLAT_LABEL
                    and middle.get("label") == _TRANS_LABEL
                    and right.get("label") == _FLAT_LABEL
                )
                if same_labels and int(left["end"]) >= int(left["start"]) and int(right["end"]) >= int(right["start"]):
                    left_t = _plateau_thickness(grid_thickness, int(left["start"]), int(left["end"]))
                    right_t = _plateau_thickness(grid_thickness, int(right["start"]), int(right["end"]))
                    if left_t == right_t:
                        merged.append({
                            "label": _FLAT_LABEL,
                            "start": int(left["start"]),
                            "end": int(right["end"]),
                            "length_mm": float(left["length_mm"]) + float(middle["length_mm"]) + float(right["length_mm"]),
                            "mean_thickness": left_t,
                            "median_thickness": left_t,
                            "std_thickness": 0.0,
                            "min_thickness": left_t,
                            "max_thickness": left_t,
                        })
                        index += 3
                        changed = True
                        continue
            merged.append(current[index])
            index += 1
        current = merged
    return current


def _segment_uploaded_wall(sections_full: List[Dict[str, Any]]):
    grid = _wall_grid(sections_full)
    if grid is None:
        return None
    grid_lengths, grid_thickness = grid
    smoothed = _smooth(grid_thickness, window=11, poly=2)
    baseline = np.abs(np.gradient(smoothed, grid_lengths))
    slope_threshold = max(float(np.percentile(baseline, 35)) * 2.5, 0.005)
    labels = _label_flat_or_transition(grid_lengths, smoothed, slope_threshold, 20.0, 5.0)
    raw_runs = _runs_from_labels(labels)
    enriched: List[Dict[str, Any]] = []
    for index, run in enumerate(raw_runs):
        start, end = int(run["start"]), int(run["end"])
        stats = _segment_stats(grid_lengths, grid_thickness, start, end)
        if index + 1 < len(raw_runs):
            stats["length_mm"] = float(grid_lengths[raw_runs[index + 1]["start"]] - grid_lengths[start])
        enriched.append({**run, **stats})
    collapsed = _collapse_same_plateau_gaps(enriched, grid_thickness)
    split_runs = _split_plateau_outliers(collapsed, grid_lengths, grid_thickness)
    return grid_lengths, grid_thickness, _normalise_to_seven(split_runs)


def _fields_from_runs(
    runs: List[Dict[str, Any]],
    grid_thickness: np.ndarray,
) -> Dict[str, Optional[float]]:
    fields = _empty_wall_fields()
    for index, run in enumerate(runs):
        if index >= len(_RECOMMEND_LENGTH_KEYS):
            break
        length = int(round(float(run["length_mm"])))
        if length <= 0:
            continue
        key = _RECOMMEND_LENGTH_KEYS[index]
        fields[key] = length
        thickness_key = _RECOMMEND_THICKNESS_KEYS.get(key)
        if thickness_key and run.get("label") == _FLAT_LABEL and int(run["end"]) >= int(run["start"]):
            fields[thickness_key] = _plateau_thickness(grid_thickness, int(run["start"]), int(run["end"]))
    return fields


def _rebuild_wall_curve(
    grid_lengths: np.ndarray,
    grid_thickness: np.ndarray,
    runs: List[Dict[str, Any]],
) -> List[Dict[str, float]]:
    """Plateaus become their 1-decimal thickness. Transitions are straight lines."""
    rebuilt = np.array(grid_thickness, dtype=float, copy=True)
    plateau_t: Dict[int, float] = {}
    for index, run in enumerate(runs):
        if run.get("label") != _FLAT_LABEL or float(run["length_mm"]) <= 0:
            continue
        start, end = int(run["start"]), int(run["end"])
        if end < start:
            continue
        plateau_t[index] = _plateau_thickness(grid_thickness, start, end)

    def _neighbor(index: int, step: int) -> Optional[float]:
        cursor = index + step
        while 0 <= cursor < len(runs):
            if cursor in plateau_t:
                return plateau_t[cursor]
            cursor += step
        return None

    for index, run in enumerate(runs):
        if float(run["length_mm"]) <= 0:
            continue
        start, end = int(run["start"]), int(run["end"])
        if end < start:
            continue
        if run.get("label") == _FLAT_LABEL and index in plateau_t:
            rebuilt[start: end + 1] = plateau_t[index]
            continue
        if run.get("label") != _TRANS_LABEL:
            continue
        left = _neighbor(index, -1)
        right = _neighbor(index, 1)
        if left is None and right is None:
            continue
        if left is None:
            left = right
        if right is None:
            right = left
        span = max(end - start, 1)
        for offset, point in enumerate(range(start, end + 1)):
            rebuilt[point] = left + (offset / span) * (right - left)
    return [
        {
            "cumulative_length": round(float(position), 2),
            "wall_thickness": round(float(thickness), 2),
        }
        for position, thickness in zip(grid_lengths, rebuilt)
    ]


def analyze_uploaded_wall(sections_full: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Segment the raw wall curve and rebuild the curve shown after upload.

    Segmentation uses the measured thickness, so a 1.46 ripple is not turned
    into 1.5 before the plateau is known. Plateau recommendations are the
    1-decimal median. The rebuilt curve snaps a plateau, including ripples
    within 0.1 mm, to that value, and draws each transition as a straight
    line between the neighbouring plateau thicknesses.
    """
    empty = {"fields": _empty_wall_fields(), "curve": []}
    segmented = _segment_uploaded_wall(sections_full)
    if segmented is None:
        return empty
    grid_lengths, grid_thickness, runs = segmented
    if not runs:
        return empty
    return {
        "fields": _fields_from_runs(runs, grid_thickness),
        "curve": _rebuild_wall_curve(grid_lengths, grid_thickness, runs),
    }


def recommend_wall_fields(sections_full: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Recommend T1–T4, TL1–TL4 and TLM1–TLM3 from the uploaded wall curve."""
    return analyze_uploaded_wall(sections_full)["fields"]


def thickness_curve_from_tl_t(
    tl_t_values: Dict[str, float],
    step_mm: float = 1.0,
) -> Dict[str, Any]:
    """Reverse: build a 1 mm wall-thickness curve from canonical TL/T values."""
    TL1 = float(tl_t_values.get("TL1", 0))
    TLM1 = float(tl_t_values.get("TLM1", 0))
    TL2 = float(tl_t_values.get("TL2", 0))
    TLM2 = float(tl_t_values.get("TLM2", 0))
    TL3 = float(tl_t_values.get("TL3", 0))
    TLM3 = float(tl_t_values.get("TLM3", 0))
    TL4 = float(tl_t_values.get("TL4", 0))
    T1 = float(tl_t_values.get("T1", 0))
    T2 = float(tl_t_values.get("T2", 0))
    T3 = float(tl_t_values.get("T3", 0))
    T4 = float(tl_t_values.get("T4", 0))

    total = TL1 + TLM1 + TL2 + TLM2 + TL3 + TLM3 + TL4
    if total <= 0:
        return {"length_mm": [], "thickness_mm": [], "total_length_mm": 0.0}

    grid = np.arange(0.0, total + step_mm * 0.5, step_mm)
    out_t = np.zeros_like(grid)
    for i, x in enumerate(grid):
        if x <= TL1:
            out_t[i] = T1
        elif x <= TL1 + TLM1:
            r = (x - TL1) / TLM1 if TLM1 > 0 else 0.0
            out_t[i] = T1 + r * (T2 - T1)
        elif x <= TL1 + TLM1 + TL2:
            out_t[i] = T2
        elif x <= TL1 + TLM1 + TL2 + TLM2:
            r = (x - (TL1 + TLM1 + TL2)) / TLM2 if TLM2 > 0 else 0.0
            out_t[i] = T2 + r * (T3 - T2)
        elif x <= TL1 + TLM1 + TL2 + TLM2 + TL3:
            out_t[i] = T3
        elif x <= TL1 + TLM1 + TL2 + TLM2 + TL3 + TLM3:
            r = (x - (TL1 + TLM1 + TL2 + TLM2 + TL3)) / TLM3 if TLM3 > 0 else 0.0
            out_t[i] = T3 + r * (T4 - T3)
        else:
            out_t[i] = T4

    return {
        "length_mm": [float(v) for v in grid],
        "thickness_mm": [float(v) for v in out_t],
        "total_length_mm": float(total),
    }
