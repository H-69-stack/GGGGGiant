"""命令行发证工具（开发/维护侧）。

示例：
  # 给客户签发：绑定机器码、允许"硬模成型+工艺规划"、到期 2027-06-30
  python issue_license.py \
      --customer "某某管业" \
      --machine-code 6ECF1-D9F4A-B3331-3F00A \
      --roles 硬模成型,工艺规划 \
      --expires 2027-06-30 \
      --out issued/某某管业_license.dat

  # 续期：沿用同一机器码与账户类型，只改到期日期，重新签发即可。

  # 自检已签发的许可证（验签）
  python issue_license.py --verify issued/某某管业_license.dat

说明：
  --machine-code 接受两种形式：
    1) 客户端展示的"机器码"（形如 6ECF1-D9F4A-...），工具会自动转换；
    2) 完整 64 位指纹（fingerprint）。
  机器码与指纹一一对应；签发时存的是完整指纹。
"""
import argparse
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.abspath(os.path.join(_THIS_DIR, '..', '..', 'backend'))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import signer  # noqa: E402


def _resolve_fingerprint(machine_code_or_fp: str) -> str:
    """把"机器码"或完整指纹统一成签发时存储的形式。

    机器码是指纹前 20 位的大写分组展示；为保证唯一可还原，签发时直接
    存储"机器码去掉分隔符后的小写"作为绑定标识，客户端校验时用相同规则
    比对。这样开发侧无需知道客户的完整 64 位指纹也能签发。
    """
    raw = machine_code_or_fp.strip()
    # 完整指纹（64 位 hex）直接用前 20 位规整
    cleaned = raw.replace('-', '').strip().lower()
    return cleaned


def main():
    p = argparse.ArgumentParser(description='巨管软件 - 命令行发证工具')
    p.add_argument('--verify', help='自检指定许可证文件（验签）后退出')
    p.add_argument('--customer', help='客户名称')
    p.add_argument('--machine-code', help='客户端展示的机器码，或完整指纹')
    p.add_argument('--roles', help='允许的账户类型，逗号分隔：ALL 或 工艺规划,模具设计,硬模成型')
    p.add_argument('--expires', help='到期日期 YYYY-MM-DD')
    p.add_argument('--issued', help='签发日期 YYYY-MM-DD（默认今天）')
    p.add_argument('--max-users', type=int, default=0, help='最大账户数，0=不限')
    p.add_argument('--edition', default='standard', help='版本/套餐标识')
    p.add_argument('--note', default='', help='备注')
    p.add_argument('--out', help='输出文件路径（默认 issued/<客户>_license.dat）')
    p.add_argument('--password', default=None, help='私钥口令（若生成密钥时设置过）')
    args = p.parse_args()

    if args.verify:
        ok, payload = signer.verify_license_file(args.verify)
        print('验签结果：', '通过 [OK]' if ok else '失败 [FAIL]')
        print('内容：')
        for k, v in payload.items():
            print(f'  {k}: {v}')
        sys.exit(0 if ok else 1)

    missing = [n for n in ('customer', 'machine_code', 'roles', 'expires')
               if not getattr(args, n.replace('-', '_'))]
    if missing:
        p.error('缺少必填参数：' + ', '.join('--' + m.replace('_', '-') for m in missing))

    roles = [r.strip() for r in args.roles.split(',') if r.strip()]
    fingerprint = _resolve_fingerprint(args.machine_code)

    payload = signer.build_payload(
        customer=args.customer,
        machine_fingerprint=fingerprint,
        allowed_workroles=roles,
        expires_at=args.expires,
        issued_at=args.issued,
        max_users=args.max_users,
        edition=args.edition,
        note=args.note,
    )

    out = args.out or os.path.join(
        signer.DEFAULT_ISSUED_DIR, f'{args.customer}_license.dat'
    )
    path = signer.issue_license_file(payload, out, password=args.password)
    code = signer.issue_activation_code(payload, password=args.password)
    print('短激活码（发给客户）：')
    print(code)
    print()
    print('已签发 license.dat 备份：', path)
    print('  许可证编号 :', payload.license_id)
    print('  客户       :', payload.customer)
    print('  账户类型   :', '、'.join(payload.allowed_workroles))
    print('  到期日期   :', payload.expires_at)


if __name__ == '__main__':
    main()
