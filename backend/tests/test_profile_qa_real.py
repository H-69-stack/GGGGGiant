"""API-level QA session tests (no STP re-parse) + real STP fixture.

Real-part fixture: tests/fixtures/stp/24TK_1324-2.stp
HTTP checks use Flask test_client (in-process, no listening port).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time

import numpy as np
import pytest

# Must be set before `app` / `config` import so the license/IP gates stay off.
os.environ.setdefault("GTAPP_LICENSE_GATE", "0")
os.environ.setdefault("GTAPP_IP_ALLOWLIST", "0")

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.pp.profile_qa.pipeline import (  # noqa: E402
    finalize_straight,
    initialize_qa_from_records,
    qa_summary,
)
from core.pp.profile_qa.state import (  # noqa: E402
    QA_PASS,
    QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
)


def _rec(s, d):
    return [{"中心线长度(mm)": float(a), "外径周长(mm)": float(b) * np.pi} for a, b in zip(s, d)]


def _run_case(name, s, d, expect_straight, expect_local=False):
    t0 = time.perf_counter()
    qa = initialize_qa_from_records(_rec(s, d), centerline_length=float(s[-1]))
    elapsed = time.perf_counter() - t0
    report = {
        "name": name,
        "n": int(len(s)),
        "centerline_mm": float(s[-1] - s[0]),
        "anomaly_point_ratio": qa.get("anomaly_point_ratio"),
        "suspect_segments": qa.get("suspect_segments"),
        "straight_segments": [
            {
                "id": seg.get("segment_id"),
                "start_s": seg.get("start_s"),
                "end_s": seg.get("end_s"),
                "rep_d": seg.get("representative_diameter"),
            }
            for seg in (qa.get("straight_segments") or [])
        ],
        "status": qa.get("status"),
        "elapsed_s": elapsed,
    }
    if expect_local:
        assert qa["status"] == "QA_LOCAL_ANOMALY", report
    elif expect_straight:
        assert qa["status"] == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION, report
        assert report["straight_segments"], report
        qa = finalize_straight(qa, confirmed_ids=[s["id"] for s in report["straight_segments"]])
        assert qa["status"] == QA_PASS
        report["status"] = qa["status"]
    else:
        assert qa["status"] in (
            QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
            QA_PASS,
        ) or (qa.get("monotonic") or {}).get("status") == "QA_MONOTONIC_PASS", report
        if qa["status"] == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION:
            assert not qa.get("straight_segments"), report
            qa = finalize_straight(qa, confirmed_ids=[])
            assert qa["status"] == QA_PASS
            report["status"] = qa["status"]
    return report


class TestGeometryLikeProfiles:
    """Stand-ins for real STP samples: straight, taper, noisy, complex."""

    def test_straight_tube_like(self):
        s = np.arange(0, 640.0, 1.0)
        d = np.full_like(s, 52.686)
        report = _run_case("straight_like", s, d, expect_straight=True)
        assert report["n"] == 640
        print(report)

    def test_taper_tube_like(self):
        s = np.arange(0, 400.0, 1.0)
        d = np.linspace(52.0, 44.0, s.size)
        report = _run_case("taper_like", s, d, expect_straight=False)
        print(report)

    def test_noisy_straight_like(self):
        s = np.arange(0, 500.0, 1.0)
        rng = np.random.default_rng(7)
        d = 52.69 + rng.normal(0, 0.08, s.size)
        report = _run_case("noisy_straight_like", s, d, expect_straight=True)
        print(report)

    def test_complex_with_local_read_error(self):
        s = np.arange(0, 600.0, 1.0)
        d = 52.0 - 12.0 * (s / 600.0)
        d[(s >= 100) & (s <= 120)] = 54.0
        report = _run_case("complex_local_error", s, d, expect_straight=False, expect_local=True)
        print(report)


def test_qa_summary_optional_field():
    s = np.arange(0, 80.0, 1.0)
    d = np.full_like(s, 40.0)
    qa = initialize_qa_from_records(_rec(s, d), centerline_length=80)
    summary = qa_summary(qa)
    assert summary["present"] is True
    assert "status" in summary


_STP_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "stp", "24TK_1324-2.stp"
)
_SESSION_ID = "24TK_1324-2"


def _wt_from_analysis(result):
    wt = result.get("wall_thickness_data") or []
    if wt:
        return wt
    return [
        {
            "中心线长度(mm)": row.get("cumulative_length", 0),
            "外径周长(mm)": row.get("perimeter", 0),
            "壁厚(mm)": row.get("wall_thickness"),
        }
        for row in (result.get("sections_full") or [])
    ]


def _frontend_card(qa, *, filename, centerline_mm, n, elapsed_s, extra=None):
    """Compact dump of what Tab1 形线质量检查 would render."""
    status = qa.get("status")
    if status == "QA_LOCAL_ANOMALY":
        ui = "warning 检测到形线局部异常 — 按钮: 预览建议平滑 / 应用建议平滑 / 保留原始数据"
    elif status == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION:
        ui = "info 请确认候选直管段 — 表格 + 确认/取消 + 完成本步确认"
    elif status == QA_PASS:
        ui = f"success 最终 QA PASS。形线来源：{qa.get('profile_source')}"
    elif status in ("QA_MANUAL_REQUIRED", "QA_RECHECK_FAILED"):
        ui = "error 请上传人工测量 XLSX"
    else:
        ui = f"status={status}"
    card = {
        "filename": filename,
        "centerline_mm": centerline_mm,
        "n": n,
        "elapsed_s": elapsed_s,
        "qa_status": status,
        "profile_source": qa.get("profile_source"),
        "anomaly_point_ratio": qa.get("anomaly_point_ratio"),
        "suspect_segments": qa.get("suspect_segments"),
        "straight_segments": qa.get("straight_segments"),
        "frontend_card": ui,
        "apply_to_optimization_enabled": bool(qa.get("profile_choice")),
    }
    if extra:
        card.update(extra)
    return card


@pytest.fixture(scope="module")
def real_stp_bundle():
    """Read 24TK_1324-2.stp once with the production tube engine + QA."""
    if not os.path.isfile(_STP_FIXTURE):
        pytest.fail(f"missing STP fixture: {_STP_FIXTURE}")

    from core.pp.tube import analyze_tube

    # A regression report may supply its freshly measured result to avoid
    # repeating this expensive CAD parse for API/QA-only assertions. Normal
    # test runs still exercise the production reader directly.
    measured_path = os.environ.get("GTAPP_TEST_STP_ANALYSIS")
    if measured_path:
        with open(measured_path, encoding="utf-8") as stream:
            result = json.load(stream)
        with open(_STP_FIXTURE, "rb") as stream:
            fixture_hash = hashlib.sha256(stream.read()).hexdigest()
        assert result["validation"]["sha256"] == fixture_hash
        assert result["analysis_quality"]["engine"] == "native_brep_v1"
        elapsed = result["validation"]["seconds"]
    else:
        t0 = time.perf_counter()
        result = analyze_tube(
            _STP_FIXTURE, file_type="stp", algorithm="A", sparse_step_mm=25.0
        )
        elapsed = time.perf_counter() - t0
    if "error" in result:
        pytest.fail(f"STP 读取失败: {result['error']}")

    wt = _wt_from_analysis(result)
    assert wt, "STP 形线为空"
    qa = initialize_qa_from_records(
        wt,
        centerline_length=result.get("center_line_length"),
        profile_source="stp_raw",
        original_filename="24TK_1324-2.stp",
    )
    return {
        "result": result,
        "wt": wt,
        "qa": qa,
        "elapsed_s": elapsed,
    }


def _write_offline_preview(qa, *, filename, centerline_mm, elapsed_s, path):
    """Self-contained HTML mimicking Tab1 QA card + perimeter chart (no server)."""
    raw = qa.get("raw_profile") or {}
    s = raw.get("s_mm") or []
    d = raw.get("outer_diameter_mm") or []
    suspects = qa.get("suspect_segments") or []
    if not s or not d:
        return
    s_min, s_max = min(s), max(s)
    d_min, d_max = min(d), max(d)
    pad_d = (d_max - d_min) * 0.08 or 1.0
    y0, y1 = d_min - pad_d, d_max + pad_d
    w, h, l, t, r, b = 920, 320, 56, 16, 16, 40
    pw, ph = w - l - r, h - t - b

    def xy(si, di):
        x = l + (si - s_min) / max(s_max - s_min, 1e-9) * pw
        y = t + (y1 - di) / max(y1 - y0, 1e-9) * ph
        return x, y

    pts = " ".join(f"{xy(si, di)[0]:.1f},{xy(si, di)[1]:.1f}" for si, di in zip(s, d))
    bands = []
    for seg in suspects:
        x0, _ = xy(seg["start_s"], d_min)
        x1, _ = xy(seg["end_s"], d_min)
        bands.append(
            f'<rect x="{x0:.1f}" y="{t}" width="{max(x1-x0, 1):.1f}" height="{ph}" '
            f'fill="rgba(245,108,108,0.22)"/>'
        )
    status = qa.get("status")
    segs_html = "".join(
        f"<li>{seg.get('start_s'):.1f}–{seg.get('end_s'):.1f} mm "
        f"（{seg.get('severity')}，偏差 {seg.get('max_positive_deviation'):.2f} mm）</li>"
        for seg in suspects
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>{filename} 形线 QA 离线预览</title>
<style>
body {{ font-family: Segoe UI, sans-serif; margin: 24px; color: #303133; background: #f5f7fa; }}
.card {{ background: #fff; border-radius: 8px; padding: 16px 20px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
.row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.stat {{ flex: 1; min-width: 160px; background: #fff; border-radius: 8px; padding: 12px 16px; }}
.stat .k {{ color: #909399; font-size: 12px; }} .stat .v {{ font-size: 22px; margin-top: 4px; }}
.tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; background: #fdf6ec; color: #e6a23c; }}
.alert {{ background: #fdf6ec; border: 1px solid #f5dab1; padding: 12px; border-radius: 4px; }}
svg {{ width: 100%; height: auto; background: #fff; }}
code {{ background: #f4f4f5; padding: 1px 4px; }}
</style></head><body>
<h2>工艺规划 · 形线质量检查（离线预览）</h2>
<p>对应前端 <code>Tab1ModelLoader</code> 上传 STP 后的 QA 卡片与周长曲线。此文件可直接双击打开，无需启动服务。</p>
<div class="row">
  <div class="stat"><div class="k">文件名</div><div class="v">{filename}</div></div>
  <div class="stat"><div class="k">中心线长度 (mm)</div><div class="v">{centerline_mm:.1f}</div></div>
  <div class="stat"><div class="k">采样点数</div><div class="v">{len(s)}</div></div>
  <div class="stat"><div class="k">外径范围 (mm)</div><div class="v">{d_min:.2f} – {d_max:.2f}</div></div>
</div>
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center">
    <strong>形线质量检查</strong> <span class="tag">{status}</span>
  </div>
  <div class="alert" style="margin-top:12px">
    检测到形线局部异常。该区域与管件总体单调递减趋势不一致，可能由 STP 几何识别或采样误差导致。是否对该区间进行平滑修正？
    <ul>{segs_html}</ul>
    前端按钮：查看原始数据 / 预览建议平滑 · 应用建议平滑 · 保留原始数据
  </div>
  <p style="color:#909399;font-size:13px">读取耗时 {elapsed_s:.1f}s · 来源 {qa.get("profile_source")} · 异常点比例 {qa.get("anomaly_point_ratio")}</p>
</div>
<div class="card">
  <strong>周长数据（以外径 mm 显示，红带为可疑区间）</strong>
  <svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">
    {''.join(bands)}
    <polyline fill="none" stroke="#409EFF" stroke-width="1.5" points="{pts}"/>
    <text x="{l}" y="{h-8}" font-size="12" fill="#909399">{s_min:.0f} mm</text>
    <text x="{w-r-40}" y="{h-8}" font-size="12" fill="#909399">{s_max:.0f} mm</text>
    <text x="8" y="{t+8}" font-size="12" fill="#909399">{y1:.1f}</text>
    <text x="8" y="{t+ph}" font-size="12" fill="#909399">{y0:.1f}</text>
  </svg>
</div>
</body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def test_24tk_1324_2_stp_profile_read(real_stp_bundle, tmp_path):
    """Native CAD sections no longer reproduce the legacy STL slicing bumps."""
    result = real_stp_bundle["result"]
    qa = real_stp_bundle["qa"]
    wt = real_stp_bundle["wt"]
    diameters = (qa.get("raw_profile") or {}).get("outer_diameter_mm") or []
    card = _frontend_card(
        qa,
        filename="24TK_1324-2.stp",
        centerline_mm=result.get("center_line_length"),
        n=len(wt),
        elapsed_s=real_stp_bundle["elapsed_s"],
        extra={
            "is_bended": result.get("is_bended"),
            "bend_angle": result.get("bend_angle"),
            "diameter_min": min(diameters) if diameters else None,
            "diameter_max": max(diameters) if diameters else None,
        },
    )
    preview = tmp_path / "24TK_1324-2_qa_preview.html"
    _write_offline_preview(
        qa,
        filename="24TK_1324-2.stp",
        centerline_mm=result.get("center_line_length") or 0,
        elapsed_s=real_stp_bundle["elapsed_s"],
        path=preview,
    )
    print("\n=== 24TK_1324-2 frontend snapshot ===")
    print(json.dumps(card, ensure_ascii=False, indent=2, default=str))
    print("offline preview:", preview)
    assert result.get("center_line_length", 0) > 0
    assert len(wt) >= 30
    assert qa.get("status") in (
        QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
        QA_PASS,
        "QA_LOCAL_ANOMALY",
        "QA_MANUAL_REQUIRED",
        "QA_RECHECK_FAILED",
        "QA_INVALID_INPUT",
    )
    assert qa.get("raw_profile") is not None
    assert os.path.isfile(preview)
    segs = qa.get("suspect_segments") or []
    assert result["analysis_quality"]["engine"] == "native_brep_v1"
    assert qa["status"] in (QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION, QA_PASS)
    assert not segs
    # Passing the one-dimensional profile QA does not approve the CAD frames.
    assert result["analysis_quality"]["review_station_count"] > 0


@pytest.fixture
def pp_http_client(real_stp_bundle, monkeypatch):
    """In-process Flask client with a session that already has STP results."""
    monkeypatch.setattr(
        "api.auth.resolve_current_user",
        lambda: {
            "id": 1,
            "name": "qa-e2e",
            "authority": "admin",
            "workrole": "ALL",
        },
    )
    from app import create_app
    from api import pp as pp_mod

    result = real_stp_bundle["result"]
    wt = real_stp_bundle["wt"]
    sf = []
    for row in result.get("sections_full") or []:
        sf.append({
            "position": row.get("position"),
            "perimeter": row.get("perimeter"),
            "cumulative_length": row.get("cumulative_length"),
            "wall_thickness": row.get("wall_thickness"),
        })

    session = {
        "file_path": _STP_FIXTURE,
        "file_type": "stp",
        "algorithm": "A",
        "sparse_step_mm": 25.0,
        "original_filename": "24TK_1324-2.stp",
        "progress": {"pct": 100, "message": "分析完成", "done": True, "error": None},
        "results": {
            "algorithm": "A",
            "sparse_step_mm": 25.0,
            "center_line": {
                "points": result.get("center_line_points") or [],
                "total_length": result.get("center_line_length", 0),
            },
            "sections": result.get("sections") or [],
            "sections_full": sf,
            "thickness_stats": result.get("wall_thickness_stats"),
            "analysis_quality": result.get("analysis_quality"),
            "centerline_length": result.get("center_line_length", 0),
            "actual_z_length": result.get("actual_z_length", result.get("center_line_length", 0)),
            "centerline_endpoint_length": result.get("centerline_endpoint_length"),
            "volume": 0,
            "is_bended": result.get("is_bended", False),
            "bend_angle": result.get("bend_angle", 0),
            "wall_thickness_data": wt,
            "centerline_perimeter_data": wt,
            "qa": copy.deepcopy(real_stp_bundle["qa"]),
            "mesh_source": "generated",
            "mesh_data": None,
        },
        "ready_for_tab2": False,
    }
    with pp_mod._session_lock:
        pp_mod._pp_sessions[_SESSION_ID] = session

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_24tk_1324_2_frontend_http_flow(pp_http_client, real_stp_bundle):
    """Same API sequence as Tab1ModelLoader after analysis, no open port."""
    client = pp_http_client
    sid = _SESSION_ID

    info = client.get(f"/api/pp/session-info?id={sid}")
    assert info.status_code == 200, info.get_data(as_text=True)
    info_body = info.get_json()
    assert info_body["success"] is True
    assert info_body["filename"] == "24TK_1324-2.stp"
    assert info_body["centerline_length"] > 0
    assert info_body["qa_summary"]["present"] is True
    assert info_body["sections_full"]

    qa_res = client.get(f"/api/pp/qa?id={sid}")
    assert qa_res.status_code == 200, qa_res.get_data(as_text=True)
    qa_body = qa_res.get_json()
    assert qa_body["success"] is True
    qa = qa_body["qa"]
    status = qa["status"]
    print("\n=== HTTP /api/pp/qa ===")
    print(json.dumps(qa_body["summary"], ensure_ascii=False, indent=2, default=str))

    blocked = client.post("/api/pp/apply-to-optimization", json={"session_id": sid})
    assert blocked.status_code == 400
    assert "校核" in blocked.get_json()["message"]

    if status == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION:
        for seg in qa.get("straight_segments") or []:
            conf = client.post(
                "/api/pp/qa/confirm-straight",
                json={"session_id": sid, "segment_id": seg["segment_id"]},
            )
            assert conf.status_code == 200, conf.get_data(as_text=True)
        fin = client.post(
            "/api/pp/qa/finalize-straight",
            json={
                "session_id": sid,
                "confirmed_ids": [
                    s["segment_id"] for s in (qa.get("straight_segments") or [])
                ],
            },
        )
        assert fin.status_code == 200, fin.get_data(as_text=True)
        qa = fin.get_json()["qa"]
        status = qa["status"]
    elif status == "QA_LOCAL_ANOMALY":
        preview = client.post("/api/pp/qa/preview-smooth", json={"session_id": sid})
        assert preview.status_code == 200, preview.get_data(as_text=True)
        conf = client.post("/api/pp/qa/confirm-smooth", json={"session_id": sid})
        assert conf.status_code == 200, conf.get_data(as_text=True)
        qa = conf.get_json()["qa"]
        status = qa["status"]
        if status == QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION:
            fin = client.post(
                "/api/pp/qa/finalize-straight",
                json={
                    "session_id": sid,
                    "confirmed_ids": [
                        s["segment_id"] for s in (qa.get("straight_segments") or [])
                    ],
                },
            )
            assert fin.status_code == 200, fin.get_data(as_text=True)
            qa = fin.get_json()["qa"]
            status = qa["status"]

    if status == QA_PASS:
        final = client.get(f"/api/pp/qa/final-profile?id={sid}")
        assert final.status_code == 200, final.get_data(as_text=True)
        final_body = final.get_json()
        assert final_body["success"] is True
        sel = client.post("/api/pp/qa/select-profile", json={"session_id": sid, "choice": "auto"})
        assert sel.status_code == 200, sel.get_data(as_text=True)
        apply = client.post(
            "/api/pp/apply-to-optimization",
            json={"session_id": sid, "profile_choice": "auto"},
        )
        assert apply.status_code == 400, apply.get_data(as_text=True)
        assert "校核" in apply.get_json()["message"]
        print("Profile QA passed; unresolved CAD section frames still block manufacturing")
    else:
        print(f"HTTP flow stopped at {status} (manual XLSX would be required in UI)")
        assert status in (
            "QA_MANUAL_REQUIRED",
            "QA_RECHECK_FAILED",
            "QA_MANUAL_DATA_INVALID",
            "QA_INVALID_INPUT",
            QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
            "QA_LOCAL_ANOMALY",
        )
