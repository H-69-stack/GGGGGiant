"""巨管软件 - 带图标的启动器（原生安装包入口）。

安装后位于安装目录 GiantTubeApp.exe，双击启动后端服务并打开浏览器。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _install_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2] / 'dist' / 'giant-tube-app-native'


def _find_runner(python_root: Path) -> Path | None:
    """Portable Python: python.exe / pythonw.exe at top level of python_root."""
    for name in ('pythonw.exe', 'python.exe'):
        candidate = python_root / name
        if candidate.is_file():
            return candidate
    # Fallback for legacy venv layout (Scripts\python.exe)
    for name in ('pythonw.exe', 'python.exe'):
        candidate = python_root / 'Scripts' / name
        if candidate.is_file():
            return candidate
    return None


def main() -> None:
    root = _install_root()
    python_root = root / 'python'
    runner = _find_runner(python_root)
    script = root / 'backend' / 'run_release.py'
    if runner is None or not script.is_file():
        # Write the full diagnostic to a log file for support, but do NOT echo
        # absolute install paths in the user-facing dialog — on dev/test
        # machines that leaks the developer's install path.
        try:
            (root / 'install_error.log').write_text(
                f'runner missing: {runner}\nscript: {script}\n', encoding='utf-8')
        except OSError:
            pass
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            '安装不完整：缺少运行环境或后端程序。\n请重新安装软件；如仍失败，请将安装目录下的 '
            'install_error.log 发给供应商。',
            '自行车抽管软件',
            0x10,
        )
        sys.exit(1)

    env = os.environ.copy()
    env.setdefault('GTAPP_INSTALL_ROOT', str(root))
    env.setdefault('GTAPP_LICENSE_PATH', str(root / 'license' / 'license.dat'))
    env.setdefault('GTAPP_FRONTEND_DIST', str(root / 'frontend' / 'dist'))
    env.setdefault('GTAPP_BIND_SERVER_MACHINE', '1')
    env.setdefault('GTAPP_OPEN_BROWSER', '1')
    # Force portable Python to ignore any user-level site-packages (e.g. %APPDATA%\Python)
    # so packages like numpy are always loaded from our bundled site-packages.
    env['PYTHONNOUSERSITE'] = '1'

    # Ensure conda-packaged native DLLs (OpenSSL, ffi, ...) are discoverable at runtime.
    library_bin = python_root / 'Library' / 'bin'
    dll_paths = [str(python_root)]
    if library_bin.is_dir():
        dll_paths.append(str(library_bin))
    existing_path = env.get('PATH', '')
    env['PATH'] = ';'.join(dll_paths + ([existing_path] if existing_path else []))

    subprocess.Popen(
        [str(runner), str(script)],
        cwd=str(root / 'backend'),
        env=env,
        creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
        | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200),
    )


if __name__ == '__main__':
    main()
