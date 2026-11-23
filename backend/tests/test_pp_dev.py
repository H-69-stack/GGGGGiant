"""DEV QA shortcuts: production 404, fixture sandbox, QA-only vs STP reread."""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

os.environ.setdefault("GTAPP_LICENSE_GATE", "0")


@pytest.fixture
def auth_user(monkeypatch):
    monkeypatch.setattr(
        "api.auth.resolve_current_user",
        lambda: {"id": 1, "name": "dev", "authority": "admin", "workrole": "ALL"},
    )


def _client(monkeypatch, tmp_path, *, tools: bool):
    if tools:
        monkeypatch.setenv("GTAPP_DEV_TOOLS", "1")
    else:
        monkeypatch.setenv("GTAPP_DEV_TOOLS", "0")
    cache_dir = tmp_path / "dev"
    cache_dir.mkdir()
    cache_path = cache_dir / "current_stp.json"
    monkeypatch.setattr("core.pp.dev_cache.CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("core.pp.dev_cache.CACHE_PATH", str(cache_path))
    from api import pp as pp_mod
    with pp_mod._session_lock:
        pp_mod._pp_sessions.clear()
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_dev_routes_absent_when_tools_off(auth_user, monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, tools=False)
    res = client.get("/api/pp/dev/status")
    assert res.status_code == 404


def test_dev_status_ok_when_tools_on(auth_user, monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, tools=True)
    res = client.get("/api/pp/dev/status")
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    assert body["enabled"] is True
    assert body["present"] is False


@pytest.mark.parametrize("bad", [
    r"C:\Windows\notepad.exe",
    r"D:\桌面\x.stp",
    "../app.py",
    "..\\app.py",
    "/etc/passwd",
    "foo.txt",
    "",
])
def test_load_fixture_rejects_unsafe_paths(auth_user, monkeypatch, tmp_path, bad):
    client = _client(monkeypatch, tmp_path, tools=True)
    res = client.post("/api/pp/dev/load-fixture", json={"filename": bad})
    assert res.status_code == 400
    assert res.get_json().get("code") == "DEV_BAD_FIXTURE"


def _seed_session(monkeypatch, tmp_path, filename="unit.stp"):
    from core.pp.dev_cache import persist_from_session, session_dict_from_cache
    from api import pp as pp_mod
    from core.pp.profile_qa.pipeline import initialize_qa_from_records

    wt = [{"中心线长度(mm)": float(i), "外径周长(mm)": 52.7 * 3.1416} for i in range(80)]
    qa = initialize_qa_from_records(wt, centerline_length=79, profile_source="stp_raw", original_filename=filename)
    path = str(tmp_path / filename)
    with open(path, "wb") as f:
        f.write(b"ISO-10303-21;ENDSEC;")
    sess = {
        "file_path": path,
        "file_type": "stp",
        "algorithm": "A",
        "original_filename": filename,
        "results": {
            "centerline_length": 79,
            "wall_thickness_data": wt,
            "centerline_perimeter_data": wt,
            "sections_full": [{"cumulative_length": float(i), "perimeter": 52.7 * 3.1416} for i in range(80)],
            "center_line": {"points": [[0, 0, i] for i in range(80)], "total_length": 79},
            "qa": qa,
        },
        "progress": {"pct": 100, "done": True, "error": None},
    }
    with pp_mod._session_lock:
        pp_mod._pp_sessions["unit-dev"] = sess
    persist_from_session("unit-dev", sess, stp_elapsed_s=9.5, qa_elapsed_ms=12.0, increment_stp=True, increment_qa=True)
    return sess


def test_rerun_qa_does_not_call_analyze_tube(auth_user, monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, tools=True)
    _seed_session(monkeypatch, tmp_path)

    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("analyze_tube must not run on QA-only rerun")

    monkeypatch.setattr("api.pp_dev.analyze_tube", boom)
    t0 = __import__("time").perf_counter()
    res = client.post("/api/pp/dev/rerun-qa", json={})
    elapsed = __import__("time").perf_counter() - t0
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["stp_read"] is False
    assert body["qa_count"] >= 2
    assert body["stp_read_count"] == 1
    assert calls["n"] == 0
    assert elapsed < 2.0
    print("QA-only elapsed_ms", body.get("elapsed_ms"), "wall_s", elapsed)


def test_reread_stp_calls_analyze_tube(auth_user, monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, tools=True)
    _seed_session(monkeypatch, tmp_path)
    calls = {"n": 0}

    def fake_analyze(path, **_k):
        calls["n"] += 1
        return {
            "center_line_points": [[0, 0, i] for i in range(40)],
            "center_line_length": 39.0,
            "sections_full": [
                {"cumulative_length": float(i), "perimeter": 40.0, "position": [0, 0, i]}
                for i in range(40)
            ],
            "algorithm": "A",
        }

    monkeypatch.setattr("api.pp_dev.analyze_tube", fake_analyze)
    res = client.post("/api/pp/dev/reread-stp", json={})
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["stp_read"] is True
    assert calls["n"] == 1
    assert body["stp_read_count"] == 2
    print("full reread stp_read_s", body.get("stp_read_s"))


def test_switching_current_stp_filename(auth_user, monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, tools=True)
    _seed_session(monkeypatch, tmp_path, filename="first.stp")

    def fake_analyze(path, **_k):
        name = os.path.basename(path)
        return {
            "center_line_points": [[0, 0, 0], [0, 0, 1]],
            "center_line_length": 10.0,
            "sections_full": [
                {"cumulative_length": float(i), "perimeter": 30.0, "position": [0, 0, i]}
                for i in range(40)
            ],
            "algorithm": "A",
            "source_name": name,
        }

    monkeypatch.setattr("api.pp_dev.analyze_tube", fake_analyze)
    from core.pp.dev_cache import resolve_fixture_name
    real = resolve_fixture_name("24TK_1324-2.stp")
    if not real:
        pytest.skip("24TK fixture missing")
    res = client.post("/api/pp/dev/load-fixture", json={"filename": "24TK_1324-2.stp"})
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["filename"] == "24TK_1324-2.stp"
    assert body["session_id"]
    assert "24TK" in (body.get("filename") or "")


def test_24tk_qa_only_is_faster_than_full_reread(auth_user, monkeypatch, tmp_path):
    """Real fixture: first STP+QA, then QA-only must not re-parse and must be faster."""
    from core.pp.dev_cache import resolve_fixture_name
    if not resolve_fixture_name("24TK_1324-2.stp"):
        pytest.skip("24TK fixture missing")
    client = _client(monkeypatch, tmp_path, tools=True)
    t0 = __import__("time").perf_counter()
    load = client.post("/api/pp/dev/load-fixture", json={"filename": "24TK_1324-2.stp"})
    full_s = __import__("time").perf_counter() - t0
    assert load.status_code == 200, load.get_data(as_text=True)
    load_body = load.get_json()
    assert load_body["stp_read"] is True
    assert load_body["qa_status"]  # must not auto-PASS
    t1 = __import__("time").perf_counter()
    rerun = client.post("/api/pp/dev/rerun-qa", json={})
    qa_s = __import__("time").perf_counter() - t1
    assert rerun.status_code == 200, rerun.get_data(as_text=True)
    rerun_body = rerun.get_json()
    assert rerun_body["stp_read"] is False
    assert rerun_body["stp_read_count"] == 1
    assert rerun_body["qa_count"] == 2
    assert qa_s < full_s
    assert qa_s < 2.0
    print({
        "full_reread_s": full_s,
        "qa_only_s": qa_s,
        "qa_only_ms": rerun_body.get("elapsed_ms"),
        "qa_status": rerun_body.get("qa_status"),
        "stp_read_count": rerun_body.get("stp_read_count"),
        "qa_count": rerun_body.get("qa_count"),
    })
