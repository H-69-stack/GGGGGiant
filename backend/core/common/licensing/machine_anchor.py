"""机器码锚点存储：把首次生成的机器指纹固化到磁盘，避免机器码随分量抖动漂移。

背景
----
机器码由若干硬件/系统标识哈希而来。网卡 MAC、CIM 信息这类分量会随网络状态、
驱动安装、系统升级而增减；如果每次都按"当次采集"重算，机器码就会在重启或
切换网络后变化，已签发的许可证随之失效（现场表现为 EE368… 变成 82C1F…）。

策略
----
1. 首次成功生成指纹后，把指纹、机器码、**主分量类型及其哈希**写入锚点文件。
2. 之后每次启动只比较主分量：一致就直接复用锚定指纹，其它分量增减不改码，
   也不重写文件（updated_at 保持不动）。
3. 主分量不一致（换机 / 重装 / 克隆）才重新计算指纹，并把旧机器码压入
   history（最多保留 10 条）。

安全与健壮性
------------
* 锚点里不保存 MachineGuid / UUID 明文，只保存 SHA-256 与脱敏展示值。
* 写入采用"临时文件 + ``os.replace``"原子替换，避免断电/并发写坏文件。
* 读失败、JSON 损坏、字段缺失都返回 ``None``；写失败返回 ``False``。
  本模块**从不**向调用方抛异常，调用方因此总能拿到一个可用指纹。

锚点路径默认 ``<仓库根>/license/machine_anchor.json``，可用环境变量
``GTAPP_MACHINE_ANCHOR`` 覆盖（测试与现场诊断用）。

锚点文件示例::

    {
      "version": 2,
      "fingerprint": "<64 位 hex>",
      "machine_code": "ABCDE-FGHIJ-KLMNO-PQRST",
      "primary": {"kind": "machine-guid", "value": "<sha256(主分量原值)>"},
      "components": {
        "machine_guid": "6ba5c1f2...",
        "cim_uuid": "4c4c4544...",
        "nic_macs": ["aa:bb:cc:**:**:**"],
        "hostname": "SERVER-01"
      },
      "created_at": "2026-11-06T22:30:00+08:00",
      "updated_at": "2026-11-06T22:30:00+08:00",
      "history": [{"machine_code": "OLDCO-DEOLD-CODE0-00000", "at": "..."}]
    }
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional

ANCHOR_VERSION = 2
MAX_HISTORY = 10
ANCHOR_ENV = 'GTAPP_MACHINE_ANCHOR'

_DEFAULT_RELATIVE = ('license', 'machine_anchor.json')
_FINGERPRINT_RE = re.compile(r'^[0-9a-f]{64}$')
_PRIMARY_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
_MACHINE_CODE_RE = re.compile(r'^[0-9A-F]{5}(?:-[0-9A-F]{5}){3}$')


def _now_iso() -> str:
    """本地时区的 ISO 时间戳（精确到秒，便于人读）。"""
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def _base_dir() -> str:
    """锚点默认目录的基准：源码运行时是仓库根，打包运行时是 exe 所在目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # backend/core/common/licensing -> common -> core -> backend -> 仓库根
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, '..', '..', '..', '..'))


def default_anchor_path() -> str:
    """默认锚点文件：``<仓库根>/license/machine_anchor.json``。"""
    return os.path.join(_base_dir(), *_DEFAULT_RELATIVE)


def anchor_path() -> str:
    """实际使用的锚点路径，支持 ``GTAPP_MACHINE_ANCHOR`` 覆盖。"""
    override = (os.environ.get(ANCHOR_ENV) or '').strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return default_anchor_path()


def primary_hash(value: str) -> str:
    """主分量原值的 SHA-256（锚点里只存这个，不存明文）。"""
    return hashlib.sha256(str(value or '').encode('utf-8')).hexdigest()


def is_valid_anchor(data: Any) -> bool:
    """结构校验：至少要有合法的 fingerprint 与 primary(kind, sha256)。"""
    if not isinstance(data, dict):
        return False
    fingerprint = str(data.get('fingerprint') or '').strip().lower()
    if not _FINGERPRINT_RE.match(fingerprint):
        return False
    primary = data.get('primary')
    if not isinstance(primary, dict):
        return False
    kind = str(primary.get('kind') or '').strip()
    value = str(primary.get('value') or '').strip().lower()
    if not kind or not _PRIMARY_HASH_RE.match(value):
        return False
    return True


def load_anchor(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """读取锚点；不存在 / 损坏 / 字段不合法时返回 ``None``（不抛异常）。"""
    target = path or anchor_path()
    try:
        with open(target, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except Exception:  # OSError / ValueError / UnicodeDecodeError ...
        return None
    if not is_valid_anchor(data):
        return None
    return data


def save_anchor(data: Dict[str, Any], path: Optional[str] = None) -> bool:
    """原子写入锚点（临时文件 + ``os.replace``）；失败返回 ``False``，不抛异常。"""
    if not isinstance(data, dict):
        return False
    target = path or anchor_path()
    absolute = os.path.abspath(target)
    directory = os.path.dirname(absolute) or '.'
    tmp_name = ''
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix='.machine_anchor.', suffix='.tmp', dir=directory
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, absolute)
        tmp_name = ''
        return True
    except Exception:
        return False
    finally:
        if tmp_name:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _clean_history(raw_history: Any, fallback_at: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    if not isinstance(raw_history, list):
        return entries
    for item in raw_history:
        if isinstance(item, dict):
            code = str(item.get('machine_code') or '').strip().upper()
            at = str(item.get('at') or '').strip() or fallback_at
        else:
            code = str(item or '').strip().upper()
            at = fallback_at
        if code:
            entries.append({'machine_code': code, 'at': at})
    return entries


def build_anchor(
    fingerprint: str,
    machine_code: str,
    primary_kind: str,
    primary_value: str,
    components: Dict[str, Any],
    previous: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """构造新的锚点数据；``previous`` 用于继承 created_at 与历史机器码。"""
    stamp = now or _now_iso()
    prev = previous if isinstance(previous, dict) else {}
    created_at = str(prev.get('created_at') or '').strip() or stamp

    history = _clean_history(prev.get('history'), stamp)
    previous_code = str(prev.get('machine_code') or '').strip().upper()
    new_code = str(machine_code or '').strip().upper()
    if previous_code and previous_code != new_code:
        history.append({'machine_code': previous_code, 'at': stamp})

    # 同一机器码多次出现时只保留最后一次（并移到末尾，保持时间顺序），
    # 然后截断到最近 MAX_HISTORY 条。
    deduped: Dict[str, str] = {}
    for entry in history:
        deduped.pop(entry['machine_code'], None)
        deduped[entry['machine_code']] = entry['at']
    trimmed = [
        {'machine_code': code, 'at': at} for code, at in deduped.items()
    ][-MAX_HISTORY:]

    return {
        'version': ANCHOR_VERSION,
        'fingerprint': str(fingerprint or '').strip().lower(),
        'machine_code': new_code,
        'primary': {
            'kind': str(primary_kind or '').strip(),
            'value': str(primary_value or '').strip().lower(),
        },
        'components': dict(components or {}),
        'created_at': created_at,
        'updated_at': stamp,
        'history': trimmed,
    }


def anchor_primary_kind(anchor: Optional[Dict[str, Any]]) -> str:
    """取锚点的主分量类型（缺失时返回空串）。"""
    if not isinstance(anchor, dict):
        return ''
    primary = anchor.get('primary')
    if not isinstance(primary, dict):
        return ''
    return str(primary.get('kind') or '').strip()


def anchor_primary_value(anchor: Optional[Dict[str, Any]]) -> str:
    """取锚点的主分量哈希（缺失时返回空串）。"""
    if not isinstance(anchor, dict):
        return ''
    primary = anchor.get('primary')
    if not isinstance(primary, dict):
        return ''
    return str(primary.get('value') or '').strip().lower()


def looks_like_machine_code(value: str) -> bool:
    """机器码格式：5 位大写 hex × 4 组，短横线分隔。"""
    return bool(_MACHINE_CODE_RE.match(str(value or '').strip().upper()))
