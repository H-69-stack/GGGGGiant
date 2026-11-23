"""Measure one fitting with a per-station checkpoint, then cache the result.

The production engine measures a station every 1 mm and cannot be paused, so a
long fitting loses all work if the process dies. This wrapper memoises
`section_shape` (the expensive CAD plane cut) in an append-only JSONL file: the
geometry algorithm is untouched, but a restart re-uses every section already
measured. The finished analysis is written to the same `_measured.json` cache
that run_outer_cavity.py consumes.

Usage:
    python tools/rfm_outer_cavity/measure_resume.py --case 24TK_1324-2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.pp.tube import brep_engine  # noqa: E402
from core.pp.tube.brep_sections import section_shape as real_section_shape  # noqa: E402
from core.pp.tube import analyze_tube  # noqa: E402

ARRAY_KEY = "__arr__"


def encode(value):
    if isinstance(value, np.ndarray):
        return {ARRAY_KEY: value.tolist()}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def decode(value):
    if isinstance(value, dict):
        if ARRAY_KEY in value and len(value) == 1:
            return np.asarray(value[ARRAY_KEY], float)
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


class CheckpointedSections:
    """Drop-in replacement for brep_engine.section_shape."""

    def __init__(self, path: Path):
        self.path = path
        self.raw = {}
        self.hits = 0
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        self.raw[record["k"]] = record["v"]
                    except (ValueError, KeyError):
                        continue  # a killed write leaves one partial line
        self.stream = path.open("a", encoding="utf-8")

    @staticmethod
    def key(origin, normal, details, deflection):
        p = np.asarray(origin, float)
        n = np.asarray(normal, float)
        return "{:.6f},{:.6f},{:.6f}|{:.6f},{:.6f},{:.6f}|{}|{}".format(
            p[0], p[1], p[2], n[0], n[1], n[2], int(bool(details)), deflection)

    def __call__(self, shape, origin, normal, *, details=True, deflection=0.003):
        key = self.key(origin, normal, details, deflection)
        cached = self.raw.get(key)
        if cached is not None:
            self.hits += 1
            return decode(json.loads(cached))
        result = real_section_shape(shape, origin, normal, details=details, deflection=deflection)
        payload = json.dumps(encode(result), ensure_ascii=False)
        self.stream.write(json.dumps({"k": key, "v": payload}, ensure_ascii=False) + "\n")
        self.stream.flush()
        self.raw[key] = payload
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--src", default="gnt管件")
    parser.add_argument("--out", default="gnt管件_外径形腔")
    parser.add_argument("--algorithm", default="A", choices=["A", "B"])
    args = parser.parse_args()

    src = ROOT / args.src
    out = ROOT / args.out
    cache_dir = out / "_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = cache_dir / f"{args.case}_stations.jsonl"

    memo = CheckpointedSections(checkpoint)
    print(f"[{args.case}] 已缓存站位 {len(memo.raw)} 个（{checkpoint.name}）", flush=True)
    brep_engine.section_shape = memo

    started = time.monotonic()

    def progress(pct, msg):
        print(f"[{args.case}] {pct:3d}% {msg}  (命中缓存 {memo.hits}, {time.monotonic() - started:.0f}s)",
              flush=True)

    analysis = analyze_tube(str(src / f"{args.case}.stp"), "stp", args.algorithm,
                            sparse_step_mm=25.0, progress_callback=progress)
    if not isinstance(analysis, dict) or analysis.get("error"):
        print(f"[{args.case}] 失败：{(analysis or {}).get('error')}", flush=True)
        return 1

    (cache_dir / f"{args.case}_measured.json").write_text(
        json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
    memo.stream.close()
    print(f"[{args.case}] 测量完成，{len(analysis['sections_full'])} 个站位，"
          f"缓存命中 {memo.hits}，用时 {time.monotonic() - started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
