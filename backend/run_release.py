"""原生 Windows 发布入口（客户安装包，无需 Docker）。

单进程 Flask 同时提供 API 与前端静态页，默认监听 0.0.0.0:5890，
局域网内其他电脑可通过浏览器访问。

用法（安装目录）：
    python\\python.exe backend\\run_release.py
或双击 start.bat / 开始菜单快捷方式。
"""
from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
INSTALL_ROOT = BACKEND_DIR.parent


def _register_native_dll_dirs() -> None:
    """Ensure conda-packaged native DLLs (OpenSSL for cryptography, ffi, ...) are discoverable.

    Portable Python layout stores them under python\\Library\\bin.
    """
    python_root = Path(sys.executable).resolve().parent
    for candidate in (python_root, python_root / 'Library' / 'bin'):
        try:
            if candidate.is_dir():
                os.add_dll_directory(str(candidate))
        except (AttributeError, OSError):
            pass


_register_native_dll_dirs()


def _load_dotenv() -> None:
    """把安装根目录的 ``.env`` 载入环境变量（既有环境变量优先）。

    这里刻意用 ``setdefault``：命令行/父进程显式给的环境变量应当赢过
    ``.env``，否则同一台机器上想同时跑「1 mm 主实例 + 3 mm 对比实例」
    就会因为 ``.env`` 里的 ``APP_PORT`` 把两个进程都压到同一端口。
    """
    env_file = INSTALL_ROOT / '.env'
    if not env_file.is_file():
        return
    try:
        for line in env_file.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
    except OSError:
        pass


_load_dotenv()

# 安装包目录布局：{InstallRoot}/backend, frontend/dist, license, python/
os.environ.setdefault('GTAPP_INSTALL_ROOT', str(INSTALL_ROOT))
os.environ.setdefault('GTAPP_LICENSE_PATH', str(INSTALL_ROOT / 'license' / 'license.dat'))
os.environ.setdefault('GTAPP_FRONTEND_DIST', str(INSTALL_ROOT / 'frontend' / 'dist'))
os.environ.setdefault('GTAPP_BIND_SERVER_MACHINE', '1')


def _setup_logging() -> None:
    """把 stdout/stderr + logging 同时重定向到日志文件，方便客户机诊断。

    pythonw 启动无控制台，sys.stdout/sys.stderr 默认是 None —— 任何库里
    残留的 print() 都会抛 AttributeError，进而干掉后台线程（比如
    analyze_stp_tube 的分析线程），出现「一直没结果」的假象。所以哪怕
    落盘失败，也必须把 stdout/stderr 换成一个可写对象（os.devnull）。
    """
    def _try_open(dirpath: Path):
        try:
            dirpath.mkdir(parents=True, exist_ok=True)
            fp = open(dirpath / 'app.log', 'a', buffering=1, encoding='utf-8')
            return fp
        except OSError:
            return None

    # 优先落到 backend/data/logs（安装脚本已授 Users:Modify）；再退到
    # %LOCALAPPDATA% 和 %TEMP%，最后 C:\Windows\Temp 作为万能兜底。
    tmp_env = os.environ.get('TEMP') or os.environ.get('TMP') or ''
    lad_env = os.environ.get('LOCALAPPDATA') or ''
    candidates = [
        BACKEND_DIR / 'data' / 'logs',
        BACKEND_DIR / 'logs',
        Path(lad_env) / 'GiantTubeApp' / 'logs' if lad_env else None,
        Path(tmp_env) / 'GiantTubeApp' if tmp_env else None,
        Path(r'C:\Windows\Temp') / 'GiantTubeApp',
    ]
    log_fp = None
    for c in candidates:
        if c is None:
            continue
        log_fp = _try_open(c)
        if log_fp is not None:
            break

    # 兜底：三个候选路径都失败（几乎不可能），仍必须把 stdout/stderr
    # 换成一个 no-op 可写对象，否则 print() 会 AttributeError 干掉线程。
    if log_fp is None:
        try:
            log_fp = open(os.devnull, 'w', encoding='utf-8')
        except OSError:
            return

    sys.stdout = log_fp
    sys.stderr = log_fp
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
        handlers=[logging.StreamHandler(log_fp)],
        force=True,
    )
    try:
        logging.info("=== log opened at %s ===", getattr(log_fp, 'name', '?'))
    except Exception:
        pass


_setup_logging()

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

from app import create_app, socketio  # noqa: E402

DEFAULT_PORT = 5890


def _open_browser_once(port: int) -> None:
    if os.environ.get('GTAPP_OPEN_BROWSER', '1') != '1':
        return
    url = f'http://127.0.0.1:{port}'
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == '__main__':
    port = int(os.environ.get('APP_PORT', str(DEFAULT_PORT)))
    pid_file = INSTALL_ROOT / 'giant_tube.pid'
    try:
        pid_file.write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        pass
    app = create_app()
    if os.environ.get('GTAPP_OPEN_BROWSER', '1') == '1':
        threading.Timer(1.5, _open_browser_once, args=(port,)).start()
    print(f'服务已启动：本机 http://127.0.0.1:{port}  局域网 http://<本机IP>:{port}')
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True,
    )
