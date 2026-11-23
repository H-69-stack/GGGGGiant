"""巨管软件 - 机器码采集工具（客户服务器侧）。

在 Windows 服务器上运行，读取本机硬件指纹并展示格式化机器码。
若已部署（存在 .env 中的 GTAPP_HOST_FINGERPRINT），优先使用该值，
与登录页 / Docker 容器内展示保持一致。

用法：
  python tools/machine_code/get_machine_code.py
  python tools/machine_code/get_machine_code.py --details
  python tools/machine_code/get_machine_code.py --json
  或 dist/tools/gt-machine-code.exe（在部署目录下运行）

说明：
  * 机器码由「主分量」+ 首次锚定决定（详见 backend/core/common/licensing/fingerprint.py），
    正常重启 / 切换网络后不应再变化。
  * ``--legacy`` 会额外计算旧算法机器码，仅用于对比历史许可证，不参与新授权。
"""
import argparse
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_BACKEND = os.path.join(_ROOT, 'backend')
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.common.licensing import fingerprint as fp_module  # noqa: E402

_SOURCE_LABELS = {
    'machine-guid': 'machine-guid（注册表 MachineGuid，最稳定）',
    'cim-uuid': 'cim-uuid（PowerShell CIM 整机 UUID）',
    'nic-mac': 'nic-mac（物理网卡 MAC 列表，硬件变化会改码）',
    'platform': 'platform（系统 machine-id / IOPlatformUUID）',
    'hostname': 'hostname（主机名兜底，不稳定）',
    'anchor': 'anchor（沿用首次锚定的机器码）',
    'env': 'env（GTAPP_HOST_FINGERPRINT 注入）',
}

_COMPONENT_LABELS = {
    'machine_guid': 'MachineGuid',
    'cim_uuid': 'CIM 整机 UUID',
    'platform_id': '系统稳定标识',
    'nic_macs': '物理网卡 MAC',
    'hostname': '主机名',
}


def _read_deployed_fingerprint() -> str:
    """读取 install.ps1 写入的宿主机指纹（与容器内 GTAPP_HOST_FINGERPRINT 一致）。"""
    candidates = []
    cwd = os.getcwd()
    candidates.append(os.path.join(cwd, '.env'))
    candidates.append(os.path.join(os.path.dirname(sys.executable), '.env'))
    candidates.append(os.path.join(os.path.dirname(sys.executable), '..', '.env'))
    if getattr(sys, 'frozen', False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), '..', '..', '.env'))
    for path in candidates:
        path = os.path.normpath(path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('GTAPP_HOST_FINGERPRINT='):
                        val = line.split('=', 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            pass
    return ''


def _apply_deployed_fingerprint() -> bool:
    """把部署目录 .env 里的指纹注入本进程（等价于容器内的环境变量）。"""
    deployed = _read_deployed_fingerprint()
    if deployed and not (os.environ.get(fp_module.ENV_INJECT) or '').strip():
        os.environ[fp_module.ENV_INJECT] = deployed
        return True
    return bool(deployed)


def resolve_fingerprint() -> str:
    """兼容旧调用方式：返回最终用于授权的完整指纹。"""
    _apply_deployed_fingerprint()
    return fp_module.get_machine_fingerprint()


def _source_label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source or '未知')


def _print_report(details, deployed: bool, show_details: bool) -> None:
    line = '=' * 56
    print(line)
    print(' 巨管软件 - 本机机器码')
    print(line)
    print(f"  机器码   : {details['machine_code']}")
    print(f"  来源     : {_source_label(details['source'])}")
    anchored = '是' if details.get('anchored') else '否'
    print(f"  已锚定   : {anchored}")
    print(f"  稳定标识 : {'是' if details.get('stable') else '否'}")
    print(f"  主分量   : {details.get('primary_kind') or '未知'}")
    if details.get('components'):
        print('  采集分量 : ' + '、'.join(
            _COMPONENT_LABELS.get(key, key) for key in details['components']
        ))
    print(f"  完整指纹 : {details['fingerprint']}")
    if deployed:
        print('  说明     : 指纹来自部署目录 .env（GTAPP_HOST_FINGERPRINT），与登录页一致。')

    warnings = details.get('warnings') or []
    if warnings:
        print('-' * 56)
        print('  注意：')
        for item in warnings:
            print(f"    - {item}")

    if show_details:
        print('-' * 56)
        print('  脱敏采集明细：')
        components = details.get('components') or {}
        if not components:
            print('    （无）')
        for key, value in components.items():
            label = _COMPONENT_LABELS.get(key, key)
            if isinstance(value, list):
                value = ', '.join(str(item) for item in value)
            print(f'    {label:<12}: {value}')
        print(f"  锚点文件 : {details.get('anchor_path') or '-'}")
        print(f"  运行平台 : {details.get('platform') or '-'}")

    print()
    print('  请把「机器码」发送给软件供应商以申请许可证。')
    print(line)


def _show_gui(machine_code: str, details) -> None:
    """弹出图形提示（双击 exe 场景）；无桌面环境时静默跳过。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        source = _source_label(details.get('source') or '')
        anchored = '已锚定' if details.get('anchored') else '未锚定'
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            '巨管软件 · 本机机器码',
            f'机器码：{machine_code}\n\n来源：{source}\n状态：{anchored}\n\n'
            f'请复制后发送给软件供应商申请许可证。',
        )
        root.destroy()
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='get_machine_code',
        description='读取本机稳定机器码（含来源 / 锚定状态诊断）。',
    )
    parser.add_argument('--json', action='store_true', help='以 JSON 输出，便于脚本/工单采集')
    parser.add_argument('--details', action='store_true', help='显示脱敏采集明细与锚点路径')
    parser.add_argument('--legacy', action='store_true', help='额外输出旧算法机器码（仅诊断对比）')
    parser.add_argument('--no-gui', action='store_true', help='不弹出图形提示框')
    parser.add_argument('--anchor', metavar='PATH', help='临时覆盖锚点文件路径（诊断用）')
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    if args.anchor:
        os.environ['GTAPP_MACHINE_ANCHOR'] = args.anchor

    deployed = _apply_deployed_fingerprint()
    details = fp_module.fingerprint_details()

    if args.legacy:
        legacy_fp = fp_module.legacy_machine_fingerprint()
        details['legacy_fingerprint'] = legacy_fp
        details['legacy_machine_code'] = fp_module.format_machine_code(legacy_fp)

    if args.json:
        print(json.dumps(details, ensure_ascii=False, indent=2))
        return 0

    _print_report(details, deployed, args.details)
    if args.legacy:
        print(f"  旧算法机器码（仅对比）: {details['legacy_machine_code']}")
        print()

    if not args.no_gui:
        _show_gui(details['machine_code'], details)
    return 0


if __name__ == '__main__':
    sys.exit(main())
