"""服务器硬件指纹采集，用于把许可证绑定到具体机器（防止软件被整体拷走盗用）。

v2 策略（锚定）
--------------
1. **单一强标识优先**：Windows 先读注册表
   ``HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid``（快，无需子进程）；
   取不到再用一条 PowerShell CIM 命令取整机 UUID
   （``Win32_ComputerSystemProduct.UUID``）；再取不到才用**物理网卡 MAC 列表**
   （已统一小写、排序、去重，且剔除虚拟网卡）；主机名只作最后兜底。
2. **首次锚定**：第一次成功生成指纹后写入锚点文件（见 ``machine_anchor.py``，
   默认 ``license/machine_anchor.json``）。之后只要主分量不变就直接复用锚定
   指纹：CIM 信息、网卡列表等次要分量增减都不再改码，也不会重写锚点文件。
   主分量变化（换机 / 重装 / 克隆）才重新计算，并把旧机器码压入 history。
3. **绝不使用** ``uuid.getnode()``：它可能返回 Tailscale / Hyper-V 等虚拟网卡的
   MAC，这些网卡随网络状态出现或消失，正是旧版机器码在重启或切换网络后
   从 EE368… 漂移到 82C1F… 的根因。

容器场景仍支持通过环境变量 ``GTAPP_HOST_FINGERPRINT`` 注入宿主机指纹
（source="env"，不写锚点）。

对外接口
--------
* :func:`get_machine_fingerprint` —— 64 位十六进制指纹（授权绑定用）。
* :func:`format_machine_code` —— 用户可见机器码（前 20 hex，5 位一组大写）。
* :func:`fingerprint_details` —— 采集 / 锚定诊断信息（API、工具使用）。
* :func:`legacy_machine_fingerprint` —— 按 v1 旧算法重算，仅供诊断对比，
  不参与新授权。
"""
from __future__ import annotations

import csv
import hashlib
import os
import platform
import re
import socket
import subprocess
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import machine_anchor

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ENV_INJECT = 'GTAPP_HOST_FINGERPRINT'

KIND_MACHINE_GUID = 'machine-guid'
KIND_CIM_UUID = 'cim-uuid'
KIND_NIC_MAC = 'nic-mac'
KIND_PLATFORM = 'platform'
KIND_HOSTNAME = 'hostname'
KIND_ENV = 'env'
KIND_ANCHOR = 'anchor'

#: 认为"稳定"的主分量类型（不随网络/外设变化）。
_STABLE_KINDS = frozenset({KIND_MACHINE_GUID, KIND_CIM_UUID, KIND_PLATFORM, KIND_ENV})

#: 主分量优先级：强标识在前，主机名只作最后兜底。
_PRIMARY_ORDER = (
    KIND_MACHINE_GUID,
    KIND_CIM_UUID,
    KIND_PLATFORM,
    KIND_NIC_MAC,
    KIND_HOSTNAME,
)

_KIND_COMPONENT = {
    KIND_MACHINE_GUID: 'machine_guid',
    KIND_CIM_UUID: 'cim_uuid',
    KIND_PLATFORM: 'platform_id',
    KIND_HOSTNAME: 'hostname',
}

#: 虚拟网卡名称关键词（小写匹配描述 / 连接名 / 接口名）。
_VIRTUAL_HINTS = (
    'virtual',
    'hyper-v',
    'hyperv',
    'vmware',
    'virtualbox',
    'tailscale',
    'zerotier',
    'vpn',
    'tap',
    'loopback',
    'bluetooth',
    'wan miniport',
    'vethernet',
    'wintun',
    'wireguard',
    'openvpn',
    'teredo',
    'isatap',
    'npcap',
    'pseudo',
    'multiplexor',
)

#: Linux/macOS 上按接口名前缀剔除的虚拟接口。
_VIRTUAL_IFACE_PREFIXES = (
    'lo',
    'docker',
    'veth',
    'br-',
    'virbr',
    'vnet',
    'tun',
    'tap',
    'wg',
    'tailscale',
    'zt',
    'vmnet',
    'vboxnet',
    'bridge',
    'awdl',
    'llw',
    'anpi',
    'utun',
)

#: 常见无效整机 UUID / 序列号（BIOS 默认值）。
_BOGUS_IDS = frozenset({
    'none',
    'null',
    'n/a',
    'na',
    'unknown',
    'default string',
    'to be filled by o.e.m.',
    'to be filled by oem',
    'system serial number',
    'system product name',
    'not applicable',
    'base board serial number',
    'chassis serial number',
    '03000200-0400-0500-0006-000700080009',
})

_MAC_RE = re.compile(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$')
_MACHINE_CODE_RE = re.compile(r'^[0-9A-F]{5}(?:-[0-9A-F]{5}){3}$')
_ALL_ZERO_OR_F_UUID_RE = re.compile(
    r'^[0f]{8}-[0f]{4}-[0f]{4}-[0f]{4}-[0f]{12}$'
)

_FP_DOMAIN = 'gtapp-machine-fingerprint:v2'

#: 一条 PowerShell 采集整机 UUID + 物理网卡 MAC（避免多次启动子进程）。
_WINDOWS_INVENTORY_PS = (
    '$ErrorActionPreference = "SilentlyContinue"; '
    '$uuid = ""; '
    'try { $uuid = [string](Get-CimInstance -ClassName Win32_ComputerSystemProduct '
    '-ErrorAction Stop).UUID } catch {}; '
    'Write-Output ("UUID|" + $uuid); '
    '$rows = @(); '
    'try { $rows = @(Get-NetAdapter -Physical -ErrorAction Stop | ForEach-Object '
    '{ "NIC|$($_.InterfaceDescription)|$($_.Name)|$($_.MacAddress)" }) } catch {}; '
    'if (-not $rows -or $rows.Count -eq 0) { '
    'try { $rows = @(Get-CimInstance -ClassName Win32_NetworkAdapter '
    '-Filter "PhysicalAdapter=True" -ErrorAction Stop | ForEach-Object '
    '{ "NIC|$($_.Description)|$($_.NetConnectionID)|$($_.MacAddress)" }) } catch {} }; '
    '$rows | ForEach-Object { Write-Output $_ }'
)


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _system_name() -> str:
    """当前平台名（单独抽出来，便于测试 monkeypatch）。"""
    return platform.system()


def _decode_stdout(raw) -> str:
    """宽容解码外部命令输出。

    PowerShell 5.1 走管道时按控制台代码页（中文系统是 GBK）输出，而
    Python 在 UTF-8 模式下默认按 UTF-8 解码，直接 ``text=True`` 会在读线程
    里抛 ``UnicodeDecodeError``，整个采集结果变成空串（CIM UUID / 物理网卡
    MAC 就是这样丢的）。这里统一收 bytes，再按 UTF-8 → 本机 ANSI（mbcs）
    → GB18030 顺序尝试，全部失败才用替换字符兜底。
    """
    if isinstance(raw, str):
        return raw
    if not raw:
        return ''
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        try:
            return raw.decode('utf-16')
        except UnicodeDecodeError:
            pass
    for encoding in ('utf-8', 'mbcs', 'gb18030'):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if '\ufffd' not in text:
            return text
    return raw.decode('utf-8', errors='replace')


def _run(cmd: List[str], timeout: int = 8) -> str:
    """执行外部命令并返回 stdout；任何失败都返回空串。"""
    kwargs: Dict[str, object] = {}
    if os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
            **kwargs,
        )
    except Exception:
        return ''
    return _decode_stdout(proc.stdout).strip()


def _run_powershell(script: str, timeout: int = 15) -> str:
    """执行 PowerShell 脚本；powershell 不存在或超时都返回空串。"""
    executable = 'powershell.exe' if os.name == 'nt' else 'powershell'
    return _run(
        [
            executable,
            '-NoProfile',
            '-NonInteractive',
            '-NoLogo',
            '-ExecutionPolicy',
            'Bypass',
            '-Command',
            script,
        ],
        timeout=timeout,
    )


def _safe(func, default):
    """调用采集函数，异常一律吞掉并返回默认值（采集失败不影响主流程）。"""
    try:
        return func()
    except Exception:
        return default


def _from_env() -> str:
    """允许通过环境变量显式注入指纹（用于容器场景注入宿主机指纹）。"""
    return (os.environ.get(ENV_INJECT) or '').strip()


def _normalize_mac(value: str) -> str:
    """MAC 统一为小写冒号分隔；非法 / 全零 / 多播地址返回空串。"""
    text = str(value or '').strip().strip('"').replace('-', ':').lower()
    if not _MAC_RE.match(text):
        return ''
    if text == '00:00:00:00:00:00':
        return ''
    try:
        first_octet = int(text[:2], 16)
    except ValueError:
        return ''
    if first_octet & 0x1:  # 多播 / 广播位，说明不是真实网卡 MAC
        return ''
    return text


def _normalize_mac_list(values) -> List[str]:
    """批量规范化 MAC：小写、去重、排序（保证主分量取值确定）。"""
    macs = {_normalize_mac(value) for value in (values or [])}
    macs.discard('')
    return sorted(macs)


def _is_virtual_label(label: str) -> bool:
    text = str(label or '').strip().lower()
    if not text:
        return False
    return any(hint in text for hint in _VIRTUAL_HINTS)


def _clean_platform_id(value: str) -> str:
    """清掉占位值并统一小写；看起来无效时返回空串。"""
    text = str(value or '').strip().strip('{}').strip().lower()
    if not text or len(text) < 8:
        return ''
    if text in _BOGUS_IDS:
        return ''
    if set(text) <= {'0', '-'}:
        return ''
    if _ALL_ZERO_OR_F_UUID_RE.match(text):
        return ''
    return text


def _normalize_uuid(value: str) -> str:
    """整机 UUID 清洗：去引号/花括号、小写、剔除 BIOS 占位值。"""
    text = str(value or '').strip().strip('"').strip().strip('{}').strip().lower()
    if not text or text in _BOGUS_IDS:
        return ''
    if len(text) < 16:
        return ''
    if set(text) <= {'0', '-'}:
        return ''
    if _ALL_ZERO_OR_F_UUID_RE.match(text):
        return ''
    return text


def _mask_secret(value: str) -> str:
    """脱敏展示：保留前 8 个字符，其余省略。"""
    text = str(value or '').strip()
    if not text:
        return ''
    if len(text) <= 6:
        return text[:1] + '*' * max(0, len(text) - 1)
    return text[:8] + '...'


def _mask_mac(mac: str) -> str:
    """MAC 脱敏：保留前 3 段（厂商标识），后 3 段打码。"""
    parts = str(mac or '').split(':')
    if len(parts) != 6:
        return _mask_secret(mac)
    return ':'.join(parts[:3] + ['**', '**', '**'])


def _compute_fingerprint(kind: str, value: str) -> str:
    """按主分量类型 + 原值计算 64 位 hex 指纹（带域分隔，避免与旧算法撞车）。"""
    payload = f'{_FP_DOMAIN}|{kind}|{value}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def format_machine_code(fingerprint: str) -> str:
    """把完整指纹格式化为用户可见的机器码（前 20 位 hex，5 位一组大写）。

    示例：6ecf1d9f4ab33313f00a... → 6ECF1-D9F4A-B3331-3F00A
    """
    raw = str(fingerprint or '').replace('-', '').strip().lower()
    prefix = raw[:20].upper()
    return '-'.join(prefix[i:i + 5] for i in range(0, len(prefix), 5))


# ---------------------------------------------------------------------------
# 各平台采集
# ---------------------------------------------------------------------------

def _read_windows_machine_guid() -> str:
    """读取注册表 MachineGuid（系统安装时生成，重装系统前不变，最稳定）。"""
    try:
        import winreg
    except ImportError:
        return ''
    paths = (
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', getattr(winreg, 'KEY_WOW64_64KEY', 0)),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography', 0),
    )
    for root, subkey, extra in paths:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | extra) as key:
                value, _ = winreg.QueryValueEx(key, 'MachineGuid')
        except OSError:
            continue
        cleaned = str(value or '').strip().strip('{}').strip().lower()
        if cleaned:
            return cleaned
    return ''


def _parse_windows_inventory(raw: str) -> Tuple[str, List[str]]:
    """解析 PowerShell 输出：``UUID|...`` 与 ``NIC|描述|名称|MAC``。"""
    cim_uuid = ''
    macs = set()
    for line in str(raw or '').splitlines():
        parts = line.strip().split('|')
        if len(parts) < 2:
            continue
        tag = parts[0].strip().upper()
        if tag == 'UUID':
            if not cim_uuid:
                cim_uuid = _normalize_uuid(parts[1])
        elif tag == 'NIC':
            if len(parts) < 4:
                continue
            label = ' '.join(parts[1:-1])
            if _is_virtual_label(label):
                continue
            mac = _normalize_mac(parts[-1])
            if mac:
                macs.add(mac)
    return cim_uuid, sorted(macs)


def _read_getmac_macs() -> List[str]:
    """``getmac`` 兜底（同样按适配器名称过滤虚拟网卡）。

    兼容两种输出格式：
    * ``getmac /v /fo csv``  → 连接名, 网卡描述, MAC, 传输名称
    * ``getmac /fo csv``     → MAC, 传输名称
    因此逐个单元格识别 MAC，而不是固定取某一列。
    """
    if os.name != 'nt':
        return []
    raw = _run(['getmac', '/v', '/fo', 'csv', '/nh'], timeout=8)
    if not raw:
        raw = _run(['getmac', '/fo', 'csv', '/nh'], timeout=8)
    macs = set()
    for row in csv.reader(str(raw or '').splitlines()):
        if not row:
            continue
        normalized = [_normalize_mac(cell) for cell in row]
        found = [mac for mac in normalized if mac]
        if not found:
            continue
        label = ' '.join(cell for cell, mac in zip(row, normalized) if not mac)
        if _is_virtual_label(label):
            continue
        macs.update(found)
    return sorted(macs)


def _read_windows_ids() -> Tuple[str, List[str]]:
    """一次 PowerShell 采集整机 UUID + 物理网卡 MAC，失败返回 ``('', [])``。"""
    if os.name != 'nt':
        return '', []
    raw = _run_powershell(_WINDOWS_INVENTORY_PS, timeout=15)
    cim_uuid, macs = _parse_windows_inventory(raw)
    if not macs:
        macs = _read_getmac_macs()
    return cim_uuid, macs


def _read_linux_platform_id() -> str:
    """Linux 稳定标识：优先 /etc/machine-id，其次 dbus machine-id / DMI UUID。"""
    for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as handle:
                cleaned = _clean_platform_id(handle.read())
        except OSError:
            continue
        if cleaned:
            return cleaned
    try:
        with open('/sys/class/dmi/id/product_uuid', 'r', encoding='utf-8', errors='replace') as handle:
            return _clean_platform_id(handle.read())
    except OSError:
        return ''


def _read_macos_platform_id() -> str:
    """macOS 稳定标识：IOPlatformUUID。"""
    out = _run(['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], timeout=8)
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
    return _clean_platform_id(match.group(1)) if match else ''


def _read_hostname() -> str:
    """主机名（最后兜底用）；取不到返回空串。"""
    try:
        name = socket.gethostname()
    except Exception:
        name = ''
    return (name or platform.node() or '').strip()


def _read_posix_physical_macs() -> List[str]:
    """Linux/macOS 物理网卡 MAC（剔除回环、容器、隧道等虚拟接口）。"""
    system = _system_name()
    macs = set()
    if system == 'Linux':
        base = '/sys/class/net'
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return []
        for name in names:
            lowered = name.strip().lower()
            if not lowered or lowered.startswith(_VIRTUAL_IFACE_PREFIXES):
                continue
            if _is_virtual_label(lowered):
                continue
            try:
                with open(os.path.join(base, name, 'address'), 'r', encoding='utf-8') as handle:
                    mac = _normalize_mac(handle.read())
            except OSError:
                continue
            if mac:
                macs.add(mac)
    elif system == 'Darwin':
        out = _run(['ifconfig', '-a'], timeout=8)
        current = ''
        for line in str(out or '').splitlines():
            if line and not line.startswith((' ', '\t')):
                current = line.split(':', 1)[0].strip().lower()
                continue
            stripped = line.strip()
            if not stripped.startswith('ether '):
                continue
            if current.startswith(_VIRTUAL_IFACE_PREFIXES) or _is_virtual_label(current):
                continue
            mac = _normalize_mac(stripped[6:])
            if mac:
                macs.add(mac)
    return sorted(macs)


# ---------------------------------------------------------------------------
# 采集快照
# ---------------------------------------------------------------------------

@dataclass
class _Snapshot:
    """一次采集得到的原始分量（内部使用，含未脱敏值）。"""

    platform_name: str = ''
    components: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    slow_loaded: bool = False

    # -- 分量访问 -----------------------------------------------------------
    def set(self, key: str, value) -> None:
        if value:
            self.components[key] = value

    def raw(self, kind: str) -> str:
        """主分量当前原值；nic-mac 返回排序去重后的 MAC 列表拼接。"""
        if kind == KIND_NIC_MAC:
            macs = self.components.get('nic_macs') or []
            return ','.join(sorted({str(mac).strip().lower() for mac in macs}))
        key = _KIND_COMPONENT.get(kind, '')
        if not key:
            return ''
        value = self.components.get(key)
        return str(value).strip() if value else ''

    def has(self, kind: str) -> bool:
        return bool(self.raw(kind))

    def primary_hash(self, kind: str) -> str:
        return machine_anchor.primary_hash(self.raw(kind))

    def masked(self) -> Dict[str, object]:
        """脱敏后的分量，写入锚点 / 诊断接口。"""
        masked: Dict[str, object] = {}
        guid = self.raw(KIND_MACHINE_GUID)
        if guid:
            masked['machine_guid'] = _mask_secret(guid)
        cim_uuid = self.raw(KIND_CIM_UUID)
        if cim_uuid:
            masked['cim_uuid'] = _mask_secret(cim_uuid)
        platform_id = self.raw(KIND_PLATFORM)
        if platform_id:
            masked['platform_id'] = _mask_secret(platform_id)
        macs = self.components.get('nic_macs') or []
        if macs:
            masked['nic_macs'] = [
                _mask_mac(mac) for mac in sorted({str(m).strip().lower() for m in macs})
            ]
        hostname = str(self.components.get('hostname') or '').strip()
        if hostname:
            masked['hostname'] = hostname
        return masked


def _collect_fast(snapshot: _Snapshot) -> None:
    """快速采集：不需要子进程的强标识（Windows 注册表 / 类 Unix 文件）。"""
    system = snapshot.platform_name
    if system == 'Windows':
        snapshot.set('machine_guid', _safe(_read_windows_machine_guid, ''))
    elif system == 'Linux':
        snapshot.set('platform_id', _safe(_read_linux_platform_id, ''))
    elif system == 'Darwin':
        snapshot.set('platform_id', _safe(_read_macos_platform_id, ''))
    snapshot.set('hostname', _safe(_read_hostname, ''))


def _collect_slow(snapshot: _Snapshot) -> None:
    """慢速采集：PowerShell CIM / 网卡 MAC（只做一次，失败静默）。"""
    if snapshot.slow_loaded:
        return
    snapshot.slow_loaded = True
    if snapshot.platform_name == 'Windows':
        cim_uuid, macs = _safe(_read_windows_ids, ('', []))
        if cim_uuid:
            snapshot.set('cim_uuid', cim_uuid)
        macs = _normalize_mac_list(macs)
        if macs:
            snapshot.set('nic_macs', macs)
        else:
            snapshot.warnings.append(
                '未能采集到物理网卡 MAC（虚拟网卡已按名称过滤），如需更高稳定性请确认注册表 MachineGuid 可读。'
            )
    else:
        macs = _normalize_mac_list(_safe(_read_posix_physical_macs, []))
        if macs:
            snapshot.set('nic_macs', macs)


def _pick_primary(snapshot: _Snapshot) -> str:
    """按优先级挑选主分量类型；全空时退回 hostname。"""
    for kind in _PRIMARY_ORDER:
        if snapshot.has(kind):
            return kind
    return KIND_HOSTNAME


def _primary_warnings(kind: str, snapshot: _Snapshot) -> List[str]:
    """主分量偏弱时给出面向现场的说明。"""
    warnings: List[str] = []
    if kind == KIND_CIM_UUID:
        warnings.append('注册表 MachineGuid 不可读，已降级使用 CIM 整机 UUID。')
    elif kind == KIND_NIC_MAC:
        warnings.append(
            '未取得 MachineGuid / 整机 UUID，机器码基于物理网卡 MAC；'
            '更换或禁用网卡可能导致机器码变化（锚点可缓解，但请优先修复 MachineGuid 读取）。'
        )
    elif kind == KIND_HOSTNAME:
        warnings.append('未采集到任何硬件标识，机器码仅基于主机名兜底，非常不稳定。')
    return warnings


# ---------------------------------------------------------------------------
# 锚点判定与结果组装
# ---------------------------------------------------------------------------

def _anchor_state(anchor: Optional[Dict], snapshot: _Snapshot) -> str:
    """锚点与本次采集的关系：match / changed / missing / none。"""
    if not isinstance(anchor, dict):
        return 'none'
    kind = machine_anchor.anchor_primary_kind(anchor)
    stored = machine_anchor.anchor_primary_value(anchor)
    if not kind or not stored:
        return 'none'
    if not snapshot.has(kind):
        return 'missing'
    return 'match' if snapshot.primary_hash(kind) == stored else 'changed'


def _build_details(
    fingerprint: str,
    source: str,
    anchored: bool,
    primary_kind: str,
    snapshot: _Snapshot,
    warnings: List[str],
    anchor_path: str,
) -> Dict[str, object]:
    fingerprint = str(fingerprint or '').strip()
    return {
        'fingerprint': fingerprint,
        'machine_code': format_machine_code(fingerprint),
        'source': source,
        'anchored': bool(anchored),
        'stable': primary_kind in _STABLE_KINDS,
        'primary_kind': primary_kind,
        'components': snapshot.masked(),
        'warnings': list(warnings or []),
        'anchor_path': anchor_path,
        'platform': snapshot.platform_name,
    }


def _details_from_anchor(
    anchor: Dict, snapshot: _Snapshot, anchor_path: str
) -> Dict[str, object]:
    fingerprint = str(anchor.get('fingerprint') or '').strip().lower()
    kind = machine_anchor.anchor_primary_kind(anchor)
    details = _build_details(
        fingerprint, KIND_ANCHOR, True, kind, snapshot, snapshot.warnings, anchor_path
    )
    recorded = str(anchor.get('machine_code') or '').strip().upper()
    if _MACHINE_CODE_RE.match(recorded):
        details['machine_code'] = recorded
    return details


def _env_details(injected: str) -> Dict[str, object]:
    snapshot = _Snapshot(platform_name=_system_name())
    warnings = [
        f'指纹由环境变量 {ENV_INJECT} 注入（容器/宿主场景），不写锚点文件。'
    ]
    return _build_details(
        injected, KIND_ENV, False, KIND_ENV, snapshot, warnings, machine_anchor.anchor_path()
    )


def _upgrade_hint(anchor_kind: str, snapshot: _Snapshot) -> str:
    """锚定码基于弱标识、本次已能取到更强标识时的提示（只提示，不改码）。"""
    try:
        index = _PRIMARY_ORDER.index(anchor_kind)
    except ValueError:
        return ''
    for candidate in _PRIMARY_ORDER[:index]:
        if snapshot.has(candidate):
            return (
                f'本次已可读取更稳定的标识（{candidate}），但为保持机器码不变，'
                f'仍沿用锚定的 {anchor_kind}；如需改用强标识，请删除锚点文件后重新签发许可证。'
            )
    return ''


def _reuse_anchor(anchor: Dict, snapshot: _Snapshot, path: str) -> Dict[str, object]:
    """复用锚定指纹，并在标识可升级时补充提示。"""
    hint = _upgrade_hint(machine_anchor.anchor_primary_kind(anchor), snapshot)
    if hint:
        snapshot.warnings.append(hint)
    return _details_from_anchor(anchor, snapshot, path)


# ---------------------------------------------------------------------------
# 进程内缓存与主流程
# ---------------------------------------------------------------------------

_CACHE: Optional[Dict[str, object]] = None


def _reset_cache() -> None:
    """清空进程内缓存（测试与工具 --refresh 使用）。"""
    global _CACHE
    _CACHE = None


def _resolve_uncached() -> Dict[str, object]:
    """真正执行采集 / 锚点判定的地方；结果会被缓存。"""
    path = machine_anchor.anchor_path()
    anchor = machine_anchor.load_anchor(path)
    snapshot = _Snapshot(platform_name=_system_name())
    _collect_fast(snapshot)

    if isinstance(anchor, dict):
        state = _anchor_state(anchor, snapshot)
        if state == 'match':
            # 快路径命中：不改码、不重写文件，也无需跑 PowerShell。
            return _reuse_anchor(anchor, snapshot, path)
        if state != 'changed':
            # 快路径没拿到锚点主分量：补采慢分量再判定一次。
            _collect_slow(snapshot)
            state = _anchor_state(anchor, snapshot)
            if state == 'match':
                return _reuse_anchor(anchor, snapshot, path)
            if state == 'missing':
                # 主分量本次确实采集不到：沿用锚定码，避免机器码无故变化。
                snapshot.warnings.append(
                    '本次未能读取锚点主分量（%s），沿用锚定机器码以保持稳定。'
                    % machine_anchor.anchor_primary_kind(anchor)
                )
                return _reuse_anchor(anchor, snapshot, path)

    _collect_slow(snapshot)

    kind = _pick_primary(snapshot)
    snapshot.warnings.extend(_primary_warnings(kind, snapshot))
    fingerprint = _compute_fingerprint(kind, snapshot.raw(kind))
    anchor_data = machine_anchor.build_anchor(
        fingerprint=fingerprint,
        machine_code=format_machine_code(fingerprint),
        primary_kind=kind,
        primary_value=snapshot.primary_hash(kind),
        components=snapshot.masked(),
        previous=anchor if isinstance(anchor, dict) else None,
    )
    stored = machine_anchor.save_anchor(anchor_data, path)
    if not stored:
        snapshot.warnings.append(
            f'锚点文件写入失败（{path}），下次启动若分量变化机器码可能漂移。'
        )
    return _build_details(
        fingerprint, kind, stored, kind, snapshot, snapshot.warnings, path
    )


def _resolve() -> Dict[str, object]:
    """带缓存的解析入口；环境变量注入的指纹每次实时读取且不缓存。"""
    injected = _from_env()
    if injected:
        return _env_details(injected)
    global _CACHE
    if _CACHE is None:
        _CACHE = _resolve_uncached()
    return _CACHE


def get_machine_fingerprint() -> str:
    """返回该机器的稳定指纹（64 位十六进制 SHA-256）。"""
    return str(_resolve()['fingerprint'])


def fingerprint_details() -> Dict[str, object]:
    """机器码诊断信息（脱敏），供 API / 工具展示。

    返回字段至少包含：``fingerprint``、``machine_code``、``source``、
    ``anchored``、``stable``、``primary_kind``、``components``、``warnings``。
    """
    data = _resolve()
    details = dict(data)
    components = dict(data.get('components') or {})
    macs = components.get('nic_macs')
    if isinstance(macs, list):
        components['nic_macs'] = list(macs)
    details['components'] = components
    details['warnings'] = list(data.get('warnings') or [])
    return details


# ---------------------------------------------------------------------------
# v1 旧算法（仅诊断对比，不参与授权）
# ---------------------------------------------------------------------------

def _legacy_mac_address() -> str:
    node = uuid.getnode()
    # 当 getnode 无法取得真实 MAC 时会返回带多播位的随机值，做个标记。
    if (node >> 40) & 0x1:
        return ''
    return ':'.join(f'{(node >> ele) & 0xff:02x}' for ele in range(40, -1, -8))


def _legacy_wmic_ids() -> List[str]:
    ids: List[str] = []
    if os.name != 'nt':
        return ids
    for cmd, header in (
        (['wmic', 'csproduct', 'get', 'uuid'], 'uuid'),
        (['wmic', 'cpu', 'get', 'ProcessorId'], 'processorid'),
        (['wmic', 'baseboard', 'get', 'SerialNumber'], 'serialnumber'),
    ):
        for line in _run(cmd, timeout=5).splitlines():
            value = line.strip()
            if value and value.lower() != header:
                ids.append(value)
    return ids


def _legacy_cim_ids() -> List[str]:
    script = (
        '$ErrorActionPreference="SilentlyContinue";'
        '(Get-CimInstance Win32_ComputerSystemProduct).UUID;'
        '(Get-CimInstance Win32_BaseBoard).SerialNumber;'
        '(Get-CimInstance Win32_Processor).ProcessorId'
    )
    out = _run_powershell(script, timeout=15)
    ids: List[str] = []
    for line in out.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.lower() in ('uuid', 'serialnumber', 'processorid', 'none', 'to be filled by o.e.m.'):
            continue
        ids.append(value)
    return ids


def _legacy_linux_ids() -> List[str]:
    ids: List[str] = []
    for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            with open(path, 'r') as handle:
                value = handle.read().strip()
                if value:
                    ids.append(value)
                    break
        except OSError:
            pass
    for path in ('/sys/class/dmi/id/product_uuid', '/sys/class/dmi/id/board_serial'):
        try:
            with open(path, 'r') as handle:
                value = handle.read().strip()
        except OSError:
            continue
        if value and value.lower() not in ('none', ''):
            ids.append(value)
    return ids


def _legacy_macos_ids() -> List[str]:
    ids: List[str] = []
    out = _run(['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'])
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
    if match:
        ids.append(match.group(1))
    serial = _run(['ioreg', '-l'])
    match = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', serial)
    if match:
        ids.append(match.group(1))
    return ids


def legacy_machine_fingerprint() -> str:
    """按 v1 旧算法重算一份指纹，仅供诊断 / 对比，不参与新授权。

    旧算法把 MachineGuid + wmic/CIM 的整机 UUID/主板序列号/CPU ID 一起哈希，
    全空时退回 ``uuid.getnode()`` 的单块网卡 MAC + 主机名 —— 即现场机器码
    从 EE368… 漂移为 82C1F… 的根因。
    """
    ids: List[str] = []
    system = _system_name()
    if system == 'Windows':
        stable: List[str] = []
        guid = _safe(_read_windows_machine_guid, '')
        if guid:
            stable.append('machine-guid:' + guid)
        hw_ids = _safe(_legacy_wmic_ids, []) or _safe(_legacy_cim_ids, [])
        stable.extend('hw:' + value for value in hw_ids)
        if stable:
            joined = '|'.join(sorted(set(stable)))
            return hashlib.sha256(joined.encode('utf-8')).hexdigest()
        ids.extend(hw_ids)
    elif system == 'Linux':
        ids.extend(_safe(_legacy_linux_ids, []))
    elif system == 'Darwin':
        ids.extend(_safe(_legacy_macos_ids, []))

    mac = _safe(_legacy_mac_address, '')
    if mac:
        ids.append(mac)
    ids.append(platform.node())
    cleaned = [value.strip() for value in ids if value and value.strip()]
    joined = '|'.join(sorted(set(cleaned)))
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()
