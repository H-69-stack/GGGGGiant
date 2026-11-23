"""独立核对：不信 c2_report.json 的数值，逐件重读 STEP 再验一遍。

核对内容：
  1. 几何：4 面 / 1 Solid / 2 条共享分型边 / 0 自由边 / valid
  2. 文件：sha256 确实在 manifest.json 里
  3. 路线与 state.json 的登记是否自洽：
     - 采用件的路线必须等于该件 preferred_route（默认 c1）
     - reference 路线：references.json 里该件必须仍有 121 参考登记

    python -u verify_outputs.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

import c2_config as cfg
import c2_routes as routes


def check() -> tuple[dict, list[str]]:
    rep = json.loads((cfg.C2_DIR / "c2_report.json").read_text(encoding="utf-8"))
    man = {
        f["sha256"]: f["path"]
        for f in json.loads((cfg.C2_DIR / "manifest.json").read_text(encoding="utf-8"))["files"]
    }
    state = cfg.load_state()
    refs = cfg.load_references()

    bad: list[str] = []
    for m in rep["models"]:
        p = cfg.C2_DIR / m["file"]
        info = routes.inspect_step(p)
        geo_ok = (
            info["faces"] == 4
            and info["solids"] == 1
            and info["free_edges"] == 0
            and info["parting_edges"] == 2
            and info["valid"]
        )
        sha_ok = man.get(info["sha256"]) is not None
        route, review = m["route"], m["review"]
        planned = cfg.preferred_route(state, m["id"])
        has_reference = bool((refs.get("cases", {}).get(m["id"]) or {}).get("reference"))
        state_ok = route == planned
        if route == "reference" and not has_reference:
            state_ok = False
        ok = geo_ok and sha_ok and state_ok
        print(
            f"{m['id']:14s} route={route:9s} plan={planned:9s} review={review:8s} faces={info['faces']} "
            f"solids={info['solids']} free={info['free_edges']} parting={info['parting_edges']} "
            f"valid={info['valid']} vol={info['volume_mm3']:,.3f} in_manifest={sha_ok} "
            f"state_ok={state_ok} ok={ok}"
        )
        if not ok:
            bad.append(m["id"])

    print("\nsummary:", rep["summary"])
    print("problems:", bad or "none")
    return rep, bad


def main() -> int:
    cfg.utf8_stdout()
    _, bad = check()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
