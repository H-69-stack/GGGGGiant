"""机器码指纹 / 锚点逻辑测试。

全部用例都 monkeypatch 掉真实的 PowerShell / wmic / 注册表采集，
不会启动子进程，也不碰仓库里的 license/ 目录。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.common.licensing import fingerprint as fp_mod  # noqa: E402
from core.common.licensing import machine_anchor  # noqa: E402

MACHINE_CODE_RE = r"^[0-9A-F]{5}(?:-[0-9A-F]{5}){3}$"


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    """每个用例独立锚点 + 独立缓存，且强制走 Windows 采集分支。"""
    monkeypatch.delenv(fp_mod.ENV_INJECT, raising=False)
    monkeypatch.setenv(
        machine_anchor.ANCHOR_ENV, str(tmp_path / "license" / "machine_anchor.json")
    )
    monkeypatch.setattr(fp_mod, "_system_name", lambda: "Windows")
    fp_mod._reset_cache()
    yield
    fp_mod._reset_cache()


def _stub_windows(monkeypatch, *, guid="", cim="", macs=(), hostname="HOST-A"):
    """把 Windows 采集全部替换成固定值。"""
    monkeypatch.setattr(fp_mod, "_read_windows_machine_guid", lambda: guid)
    monkeypatch.setattr(fp_mod, "_read_windows_ids", lambda: (cim, list(macs)))
    monkeypatch.setattr(fp_mod, "_read_getmac_macs", lambda: [])
    monkeypatch.setattr(fp_mod, "_read_hostname", lambda: hostname)


def _anchor_path() -> Path:
    return Path(machine_anchor.anchor_path())


def _read_anchor() -> dict:
    with open(_anchor_path(), "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_anchor_text() -> str:
    with open(_anchor_path(), "r", encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# 基础格式
# ---------------------------------------------------------------------------

def test_format_machine_code():
    fingerprint = "6ecf1d9f4ab33313f00a" + "0" * 44
    code = fp_mod.format_machine_code(fingerprint)
    assert code == "6ECF1-D9F4A-B3331-3F00A"
    assert len(code) == 23
    # 带短横线的输入也应被规范化
    assert fp_mod.format_machine_code("6ECF1D9F-4AB33313-F00A") == code


def test_fingerprint_is_64_hex_and_anchored(monkeypatch):
    guid = "a1b2c3d4-1111-2222-3333-444455556666"
    _stub_windows(monkeypatch, guid=guid, cim="UUID-A", macs=["aa:bb:cc:dd:ee:01"])
    fingerprint = fp_mod.get_machine_fingerprint()
    assert len(fingerprint) == 64
    assert all(ch in "0123456789abcdef" for ch in fingerprint)

    details = fp_mod.fingerprint_details()
    assert details["source"] == "machine-guid"
    assert details["anchored"] is True
    assert details["stable"] is True
    assert details["primary_kind"] == "machine-guid"
    assert details["machine_code"] == fp_mod.format_machine_code(fingerprint)

    data = _read_anchor()
    assert data["version"] == machine_anchor.ANCHOR_VERSION
    assert data["fingerprint"] == fingerprint
    assert data["machine_code"] == details["machine_code"]
    assert data["primary"]["kind"] == "machine-guid"
    assert data["primary"]["value"] == machine_anchor.primary_hash(guid)
    assert data["history"] == []
    # 组件脱敏：不能出现原始 MachineGuid 明文
    dumped = json.dumps(data["components"], ensure_ascii=False)
    assert guid not in dumped
    assert data["components"]["machine_guid"] == "a1b2c3d4..."


def test_details_contract(monkeypatch):
    _stub_windows(monkeypatch, guid="0123456789abcdef", cim="", macs=[], hostname="HOST-A")
    details = fp_mod.fingerprint_details()
    for key in (
        "fingerprint",
        "machine_code",
        "source",
        "anchored",
        "stable",
        "primary_kind",
        "components",
        "warnings",
    ):
        assert key in details
    assert details["source"] in (
        "machine-guid",
        "cim-uuid",
        "nic-mac",
        "platform",
        "env",
        "anchor",
    )
    assert "0123456789abcdef" not in json.dumps(details["components"])
    assert details["anchor_path"] == str(_anchor_path())


# ---------------------------------------------------------------------------
# 锚点复用 / 重建
# ---------------------------------------------------------------------------

def test_anchor_reuse_when_cim_and_macs_change(monkeypatch):
    _stub_windows(monkeypatch, guid="GUID-A", cim="UUID-A", macs=["aa:bb:cc:dd:ee:01"])
    first = fp_mod.get_machine_fingerprint()
    anchor_text = _read_anchor_text()

    # 模拟重启后：MachineGuid 不变，但 CIM UUID / 网卡列表变了
    fp_mod._reset_cache()
    slow_calls = []
    monkeypatch.setattr(fp_mod, "_read_windows_ids", lambda: slow_calls.append(1) or ("UUID-B", ["aa:bb:cc:dd:ee:99"]))
    second = fp_mod.get_machine_fingerprint()

    assert second == first
    assert slow_calls == []  # 快路径命中，不再跑 PowerShell 慢采集
    assert _read_anchor_text() == anchor_text  # 锚点文件未被重写

    details = fp_mod.fingerprint_details()
    assert details["source"] == "anchor"
    assert details["anchored"] is True
    assert details["primary_kind"] == "machine-guid"
    assert "cim_uuid" not in details["components"]


def test_anchor_not_rewritten_when_secondary_components_change(monkeypatch):
    _stub_windows(monkeypatch, guid="GUID-A", cim="UUID-A", macs=["aa:bb:cc:dd:ee:01"])
    fp_mod.get_machine_fingerprint()
    before = os.stat(_anchor_path())

    fp_mod._reset_cache()
    _stub_windows(monkeypatch, guid="GUID-A", cim="UUID-Z", macs=["aa:bb:cc:dd:ee:77"])
    fp_mod.get_machine_fingerprint()

    after = os.stat(_anchor_path())
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_size == before.st_size


def test_primary_change_recomputes_and_records_history(monkeypatch):
    _stub_windows(monkeypatch, guid="GUID-A")
    first = fp_mod.get_machine_fingerprint()
    first_code = fp_mod.format_machine_code(first)
    created_at = _read_anchor()["created_at"]

    fp_mod._reset_cache()
    _stub_windows(monkeypatch, guid="GUID-B")
    second = fp_mod.get_machine_fingerprint()

    assert second != first
    data = _read_anchor()
    assert data["fingerprint"] == second
    assert data["machine_code"] == fp_mod.format_machine_code(second)
    assert data["primary"]["value"] == machine_anchor.primary_hash("GUID-B")
    assert data["created_at"] == created_at
    assert first_code in [item["machine_code"] for item in data["history"]]


def test_history_is_capped(monkeypatch):
    codes = []
    for index in range(13):
        _stub_windows(monkeypatch, guid=f"GUID-{index:02d}")
        fp_mod._reset_cache()
        fingerprint = fp_mod.get_machine_fingerprint()
        codes.append(fp_mod.format_machine_code(fingerprint))

    data = _read_anchor()
    assert data["machine_code"] == codes[-1]
    assert len(data["history"]) == machine_anchor.MAX_HISTORY
    history_codes = [item["machine_code"] for item in data["history"]]
    assert codes[-2] == history_codes[-1]
    assert codes[-1] not in history_codes


def test_corrupt_anchor_is_rebuilt(monkeypatch):
    path = _anchor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    _stub_windows(monkeypatch, guid="GUID-A")
    fingerprint = fp_mod.get_machine_fingerprint()
    assert len(fingerprint) == 64

    data = _read_anchor()
    assert data["fingerprint"] == fingerprint
    assert data["primary"]["kind"] == "machine-guid"


def test_structurally_invalid_anchor_is_rebuilt(monkeypatch):
    path = _anchor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "fingerprint": "zzz"}), encoding="utf-8")

    _stub_windows(monkeypatch, guid="GUID-A")
    fingerprint = fp_mod.get_machine_fingerprint()
    assert _read_anchor()["fingerprint"] == fingerprint


def test_anchor_reused_when_primary_temporarily_unavailable(monkeypatch):
    _stub_windows(monkeypatch, guid="GUID-A")
    first = fp_mod.get_machine_fingerprint()

    fp_mod._reset_cache()
    _stub_windows(monkeypatch, guid="", cim="", macs=())
    second = fp_mod.get_machine_fingerprint()

    assert second == first
    details = fp_mod.fingerprint_details()
    assert details["source"] == "anchor"
    assert any("沿用" in item for item in details["warnings"])


def test_anchor_directory_created_without_temp_leftovers(monkeypatch):
    path = _anchor_path()
    assert not path.parent.exists()

    _stub_windows(monkeypatch, guid="GUID-A")
    fp_mod.get_machine_fingerprint()

    assert path.is_file()
    leftovers = list(path.parent.glob(".machine_anchor.*"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# 只有物理 MAC 时的确定性
# ---------------------------------------------------------------------------

def test_mac_only_primary_is_sorted_and_deterministic(monkeypatch):
    macs = ["AA:BB:CC:DD:EE:02", "aa:bb:cc:dd:ee:01"]
    _stub_windows(monkeypatch, guid="", cim="", macs=list(reversed(macs)), hostname="H1")
    first = fp_mod.get_machine_fingerprint()

    details = fp_mod.fingerprint_details()
    assert details["primary_kind"] == "nic-mac"
    assert details["stable"] is False
    assert any("MAC" in item for item in details["warnings"])

    data = _read_anchor()
    assert data["primary"]["kind"] == "nic-mac"
    assert data["primary"]["value"] == machine_anchor.primary_hash(
        "aa:bb:cc:dd:ee:01,aa:bb:cc:dd:ee:02"
    )

    # 删掉锚点后按相反顺序重新采集，指纹仍应一致（排序去重）
    _anchor_path().unlink()
    fp_mod._reset_cache()
    _stub_windows(monkeypatch, guid="", cim="", macs=macs, hostname="H1")
    second = fp_mod.get_machine_fingerprint()
    assert second == first


def test_parse_windows_inventory_filters_placeholders_and_virtual():
    raw = "\n".join([
        "UUID|None",
        "UUID|03000200-0400-0500-0006-000700080009",
        "NIC|Hyper-V Virtual Ethernet Adapter|vEthernet (Default Switch)|00-15-5D-01-02-03",
        "NIC|Intel(R) Wi-Fi 6E AX211|WLAN|AA-BB-CC-DD-EE-01",
        "NIC|TAP-Windows Adapter V9|Ethernet 2|AA-BB-CC-DD-EE-99",
    ])
    cim_uuid, macs = fp_mod._parse_windows_inventory(raw)
    assert cim_uuid == ""
    assert macs == ["aa:bb:cc:dd:ee:01"]


@pytest.mark.skipif(os.name != "nt", reason="getmac 仅在 Windows 存在")
def test_getmac_csv_parses_physical_column(monkeypatch):
    rows = "\n".join([
        '"Connection Name","Network Adapter","Physical Address","Transport Name"',
        '"vEthernet (Default Switch)","Hyper-V Virtual Ethernet Adapter",'
        '"00-15-5D-01-02-03","\\Device\\Tcpip_{11111111-1111-1111-1111-111111111111}"',
        '"WLAN","Intel(R) Wi-Fi 6E AX211 160MHz",'
        '"AA-BB-CC-DD-EE-01","\\Device\\Tcpip_{22222222-2222-2222-2222-222222222222}"',
    ])
    monkeypatch.setattr(fp_mod, "_run", lambda *args, **kwargs: rows)
    assert fp_mod._read_getmac_macs() == ["aa:bb:cc:dd:ee:01"]


def test_weak_anchor_hints_upgrade_without_changing_code(monkeypatch):
    macs = ["aa:bb:cc:dd:ee:01"]
    _stub_windows(monkeypatch, guid="", cim="", macs=macs)
    first = fp_mod.get_machine_fingerprint()

    fp_mod._reset_cache()
    _stub_windows(monkeypatch, guid="GUID-A", cim="", macs=macs)
    second = fp_mod.get_machine_fingerprint()

    assert second == first
    details = fp_mod.fingerprint_details()
    assert details["source"] == "anchor"
    assert details["primary_kind"] == "nic-mac"
    assert any("machine-guid" in item for item in details["warnings"])


# ---------------------------------------------------------------------------
# 环境变量注入
# ---------------------------------------------------------------------------

def test_env_injection_has_priority_and_skips_anchor(monkeypatch):
    monkeypatch.setenv(fp_mod.ENV_INJECT, "env-injected-fingerprint")
    _stub_windows(monkeypatch, guid="GUID-A")

    assert fp_mod.get_machine_fingerprint() == "env-injected-fingerprint"

    details = fp_mod.fingerprint_details()
    assert details["source"] == "env"
    assert details["primary_kind"] == "env"
    assert details["anchored"] is False
    assert details["stable"] is True
    assert not _anchor_path().exists()


# ---------------------------------------------------------------------------
# 旧算法对比（诊断用）
# ---------------------------------------------------------------------------

def test_legacy_fingerprint_matches_v1_algorithm(monkeypatch):
    monkeypatch.setattr(fp_mod, "_read_windows_machine_guid", lambda: "GUID-LEGACY")
    monkeypatch.setattr(fp_mod, "_legacy_wmic_ids", lambda: [])
    monkeypatch.setattr(fp_mod, "_legacy_cim_ids", lambda: [])
    monkeypatch.setattr(fp_mod, "_legacy_mac_address", lambda: "")

    got = fp_mod.legacy_machine_fingerprint()
    expected = hashlib.sha256("machine-guid:GUID-LEGACY".encode("utf-8")).hexdigest()
    assert got == expected
    assert got != fp_mod.get_machine_fingerprint()


# ---------------------------------------------------------------------------
# machine_anchor 单元行为
# ---------------------------------------------------------------------------

def test_machine_anchor_save_and_load_roundtrip(tmp_path):
    target = tmp_path / "nested" / "anchor.json"
    data = machine_anchor.build_anchor(
        fingerprint="a" * 64,
        machine_code="AAAAA-AAAAA-AAAAA-AAAAA",
        primary_kind="machine-guid",
        primary_value=machine_anchor.primary_hash("GUID-A"),
        components={"machine_guid": "GUID-A..."},
    )
    assert machine_anchor.save_anchor(data, str(target)) is True
    loaded = machine_anchor.load_anchor(str(target))
    assert loaded is not None
    assert loaded["fingerprint"] == "a" * 64
    assert loaded["primary"]["kind"] == "machine-guid"


def test_machine_anchor_load_is_tolerant(tmp_path):
    assert machine_anchor.load_anchor(str(tmp_path / "missing.json")) is None

    broken = tmp_path / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    assert machine_anchor.load_anchor(str(broken)) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"fingerprint": "abc"}), encoding="utf-8")
    assert machine_anchor.load_anchor(str(invalid)) is None


def test_machine_anchor_build_keeps_history(tmp_path):
    first = machine_anchor.build_anchor(
        fingerprint="a" * 64,
        machine_code="AAAAA-AAAAA-AAAAA-AAAAA",
        primary_kind="machine-guid",
        primary_value=machine_anchor.primary_hash("GUID-A"),
        components={},
    )
    second = machine_anchor.build_anchor(
        fingerprint="b" * 64,
        machine_code="BBBBB-BBBBB-BBBBB-BBBBB",
        primary_kind="machine-guid",
        primary_value=machine_anchor.primary_hash("GUID-B"),
        components={},
        previous=first,
    )
    assert second["created_at"] == first["created_at"]
    assert [item["machine_code"] for item in second["history"]] == [
        "AAAAA-AAAAA-AAAAA-AAAAA"
    ]
