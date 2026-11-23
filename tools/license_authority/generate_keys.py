"""生成开发侧专用的 Ed25519 密钥对（发证根密钥）。

用法：
    python generate_keys.py                 # 生成无口令保护的私钥
    python generate_keys.py --password 123  # 生成带口令保护的私钥（更安全）

产物：
    keys/private_key.pem   —— 私钥，绝密！只保存在开发/维护侧，切勿外发。
    keys/public_key.pem    —— 公钥，需要嵌入客户端（见下方提示）。

⚠️ 重要：
  1. 私钥一旦泄露，任何人都能伪造许可证，整套防盗体系失效。请离线妥善保管。
  2. 重新生成密钥会使"所有已签发的旧许可证全部失效"。仅在初次部署或
     私钥泄露需要吊销时才重新生成。
"""
import argparse
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.abspath(os.path.join(_THIS_DIR, '..', '..', 'backend'))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import importlib.util  # noqa: E402

# 直接按文件路径加载 crypto 模块，避免触发包 __init__ 的连锁导入。
_crypto_path = os.path.join(_BACKEND_DIR, 'core', 'common', 'licensing', 'crypto.py')
_spec = importlib.util.spec_from_file_location('_gt_crypto', _crypto_path)
_crypto = importlib.util.module_from_spec(_spec)
sys.modules['_gt_crypto'] = _crypto
_spec.loader.exec_module(_crypto)
generate_keypair = _crypto.generate_keypair


def main():
    parser = argparse.ArgumentParser(description='生成发证用 Ed25519 密钥对')
    parser.add_argument('--password', default=None, help='私钥保护口令（可选，建议设置）')
    parser.add_argument('--force', action='store_true', help='已存在时强制覆盖')
    args = parser.parse_args()

    keys_dir = os.path.join(_THIS_DIR, 'keys')
    os.makedirs(keys_dir, exist_ok=True)
    private_path = os.path.join(keys_dir, 'private_key.pem')
    public_path = os.path.join(keys_dir, 'public_key.pem')

    if os.path.exists(private_path) and not args.force:
        print(f'私钥已存在：{private_path}')
        print('如确实要重新生成（会使所有旧许可证失效），请加 --force')
        return

    password = args.password.encode('utf-8') if args.password else None
    private_pem, public_pem = generate_keypair(password=password)

    with open(private_path, 'wb') as f:
        f.write(private_pem)
    os.chmod(private_path, 0o600)
    with open(public_path, 'wb') as f:
        f.write(public_pem)

    print('已生成密钥对：')
    print(f'  私钥（绝密）：{private_path}')
    print(f'  公钥        ：{public_path}')

    # 自动回填客户端 public_key.py
    pubkey_module = os.path.join(
        _BACKEND_DIR, 'core', 'common', 'licensing', 'public_key.py'
    )
    if os.path.exists(pubkey_module):
        _write_public_key_module(pubkey_module, public_pem)
        print(f'  已自动写入客户端公钥：{pubkey_module}')
    else:
        print()
        print('未找到 public_key.py，请手动把以下公钥粘贴进 PUBLIC_KEY_PEM：')
        print('-' * 60)
        print(public_pem.decode('ascii'))
        print('-' * 60)


def _write_public_key_module(path: str, public_pem: bytes) -> None:
    pem_text = public_pem.decode('ascii').strip()
    content = (
        '"""内置于客户端的发证公钥（PEM）。\n\n'
        '此公钥用于*验证*许可证签名。它可以公开，泄露无安全风险。\n'
        '真正需要保密的是与之配对的私钥（只存在于开发/维护侧的发证工具中）。\n\n'
        '⚠️ 由 generate_keys.py 自动回填。若你重新生成了密钥对，请重新运行\n'
        '   generate_keys.py 以更新此文件。\n"""\n\n'
        '# 由 generate_keys.py 写入\n'
        'PUBLIC_KEY_PEM = b"""' + pem_text + '\n"""\n\n\n'
        'def is_configured() -> bool:\n'
        "    return b'PLACEHOLDER_PUBLIC_KEY' not in PUBLIC_KEY_PEM\n"
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


if __name__ == '__main__':
    main()
