"""本机调试入口（仅开发态使用）。

特点：
  - 端口固定 5060，对齐前端 Vite 代理（frontend/vite.config.js 代理到 5060）。
  - 默认关闭许可证门禁（GTAPP_LICENSE_GATE=0），调试时不被许可证拦。
  - 开启 Werkzeug 热重载（debug=True, use_reloader=True）。
  - 开启 DEV QA 面板 API（GTAPP_DEV_TOOLS=1）。release 通道下该开关无效。

用法：
    仓库根目录 .\\dev.ps1
    或：cd backend && python run_dev.py
然后前端： cd frontend && npm run dev  （打开 http://localhost:3001 ，不要打开 5060）
"""
import os

# 必须在导入 config / app 之前设置，使其在模块加载时生效。
os.environ.setdefault("GTAPP_LICENSE_GATE", "0")
os.environ.setdefault("GTAPP_DEV_TOOLS", "1")

import dev_reload_marker  # noqa: F401, E402 — watched by the reloader
from app import create_app, socketio  # noqa: E402

# Werkzeug watchdog matches POSIX globs against Windows paths, so `*/data/*`
# does not ignore `D:\...\data\exports\...`. Do not add `*.zip` here: Werkzeug
# already watches `*.zip` (zipimport) and watchdog rejects the same pattern in
# both include and exclude.
_RELOADER_EXCLUDE = [
    "*/data/*",
    "*\\data\\*",
    "*/uploads/*",
    "*\\uploads\\*",
    "*/cache/*",
    "*\\cache\\*",
    "*/__pycache__/*",
    "*\\__pycache__\\*",
    "*/.git/*",
    "*\\.git\\*",
    "*/node_modules/*",
    "*\\node_modules\\*",
    "*/tests/fixtures/*",
    "*\\tests\\fixtures\\*",
    "*.stp",
    "*.step",
    "*.stl",
    "*.xlsx",
    "*.dxf",
    "*.db",
]

if __name__ == "__main__":
    app = create_app()
    socketio.run(
        app,
        host="127.0.0.1",
        port=5060,
        debug=True,
        use_reloader=True,
        # watchdog 在 Windows 上会把“读取/导入 .py”也报成文件变化，
        # 开发服务因此反复重启，内存中的登录令牌被清空，上传随即 401 并跳回登录页。
        # stat 只看修改时间，保存源码仍会热重载。
        reloader_options={"reloader_type": "stat"},
        exclude_patterns=_RELOADER_EXCLUDE,
        allow_unsafe_werkzeug=True,
    )
