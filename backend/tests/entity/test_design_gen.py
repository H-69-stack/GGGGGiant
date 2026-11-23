"""上游工艺设计补算（core.entity.design_gen）单测。

覆盖：
- 实测缓存 → 分析输入的口径（行数、长度、管壁体积、L4、分段）
- 已登记件的幂等短路（不调用优化器）
- 不可行不写库、可行写库并解析 save_design 的 (id, err) 元组
- 缺测量缓存返回 no_measured
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.entity import design_gen  # noqa: E402

PART = "24TK_1324-1"


@pytest.fixture(scope="module")
def analysis_input():
    try:
        return design_gen.build_analysis_input(PART)
    except FileNotFoundError as exc:
        pytest.skip(f"缺少真实测量缓存，跳过数值断言：{exc}")


def test_build_analysis_input_numbers(analysis_input):
    assert analysis_input["part"] == PART
    assert len(analysis_input["rows"]) == 761
    assert analysis_input["centerline_length"] == pytest.approx(757.268, abs=0.01)
    # 管壁体积（∫π(d·w−w²)ds），不是实体体积
    assert 250_000 < analysis_input["volume"] < 270_000
    assert analysis_input["L4"] == pytest.approx(685.28, abs=0.5)
    assert analysis_input["segments"].get("T1", 0) > 0
    assert analysis_input["line_length"] == pytest.approx(
        analysis_input["centerline_length"] - analysis_input["L4"], abs=0.01
    )
    row = analysis_input["rows"][0]
    assert set(row) == {"中心线长度(mm)", "壁厚(mm)", "外径周长(mm)"}


@pytest.fixture()
def registered_design(tmp_path, monkeypatch):
    """自包含地造一条已登记设计，避免依赖本机数据库里的历史记录。"""
    from core.pp import design_db as ddb

    path = tmp_path / "pp_design.db"
    conn = ddb.init_database(str(path))
    conn.execute(
        "insert into pp_design (id, display_name, pp_method, source_file_name)"
        " values (3, 'test', '仅TP抽', '24TK_1324-1.stp')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(design_gen, "design_db_path", lambda: path)
    return path


def test_existing_design_readonly(registered_design):
    design = design_gen.existing_design(PART)
    assert design is not None
    assert design["id"] == 3
    assert design["pp_method"] == "仅TP抽"


def test_registered_short_circuit(monkeypatch, registered_design):
    def boom(*_args, **_kwargs):
        raise AssertionError("已登记件不应调用优化器")

    monkeypatch.setattr(design_gen, "_optimize", boom)
    result = design_gen.create_default_design(PART)
    assert result["state"] == "registered"
    assert result["design_id"] == 3
    assert result["created"] is False


def _stub_inputs():
    return {
        "part": "STUB",
        "rows": [
            {"中心线长度(mm)": 0.0, "壁厚(mm)": 2.0, "外径周长(mm)": 226.0},
            {"中心线长度(mm)": 1.0, "壁厚(mm)": 2.0, "外径周长(mm)": 226.0},
        ],
        "segments": {"T1": 2.0, "D1": 72.0},
        "centerline_length": 757.268,
        "volume": 260_000.0,
        "L4": 685.28,
        "line_length": 71.988,
        "max_diameter": 72.4,
        "min_diameter": 56.5,
        "measured": "",
    }


def _patch_common(monkeypatch, fitness):
    monkeypatch.setattr(design_gen, "existing_design", lambda _part: None)
    monkeypatch.setattr(design_gen, "build_analysis_input", lambda *_a, **_k: _stub_inputs())
    monkeypatch.setattr(design_gen, "_recommend", lambda _inputs: ("仅TP抽", "stub 理由"))
    monkeypatch.setattr(design_gen, "_optimize", lambda _inputs, _route: {"best": {"fitness": fitness}})
    monkeypatch.setattr(design_gen, "_modified_wall", lambda *_a, **_k: [])


def test_infeasible_not_saved(monkeypatch):
    _patch_common(monkeypatch, 0.1)
    saved = {"called": False}

    def fake_save(**_kwargs):
        saved["called"] = True
        return (99, None)

    monkeypatch.setattr("core.pp.design_db.save_design", fake_save)
    result = design_gen.create_default_design("STUB-PART")
    assert result["state"] == "infeasible"
    assert result["created"] is False
    assert "不可行" in result["message"]
    assert saved["called"] is False


def test_created_parses_tuple_id(monkeypatch):
    _patch_common(monkeypatch, 1.5)
    captured = {}

    def fake_save(**kwargs):
        captured.update(kwargs)
        return (77, None)

    monkeypatch.setattr("core.pp.design_db.save_design", fake_save)
    result = design_gen.create_default_design("STUB-PART")
    assert result["state"] == "created"
    assert result["created"] is True
    assert result["design_id"] == 77
    assert captured["pp_method"] == "仅TP抽"
    assert captured["source_file_name"] == "STUB-PART.stp"
    assert captured["extra_params"]["自动补算"] is True
    assert len(captured["centerline_perimeter_data"]) == 2


def test_no_measured(tmp_path):
    result = design_gen.create_default_design("不存在的件-000", str(tmp_path))
    assert result["state"] == "no_measured"
    assert result["created"] is False
