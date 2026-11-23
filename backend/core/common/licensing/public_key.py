"""内置于客户端的发证公钥（PEM）。

此公钥用于*验证*许可证签名。它可以公开，泄露无安全风险。
真正需要保密的是与之配对的私钥（只存在于开发/维护侧的发证工具中）。

⚠️ 由 generate_keys.py 自动回填。若你重新生成了密钥对，请重新运行
   generate_keys.py 以更新此文件。
"""

# 由 generate_keys.py 写入
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAlUUFFe+v+MRoI1c0SVWU2lXjYsvQhxu9Uh+U+tXCHzc=
-----END PUBLIC KEY-----
"""


def is_configured() -> bool:
    return b'PLACEHOLDER_PUBLIC_KEY' not in PUBLIC_KEY_PEM
