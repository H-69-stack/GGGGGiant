import os
import re
import stat
import shutil
import tempfile
import zipfile

_UNSAFE_EXPORT_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}


def safe_export_stem(name, default='output'):
    """Turn 车种规格 into a folder/zip stem that Windows can create.

    Empty, ``/``, reserved device names, and ``<>:"/\\|?*`` become ``default``.
    """
    text = '' if name is None else str(name).strip()
    text = text.replace('\\', '_').replace('/', '_')
    text = _UNSAFE_EXPORT_CHARS.sub('_', text)
    text = text.strip(' ._')
    if not text or text in ('.', '..') or text.upper() in _WIN_RESERVED:
        return default
    return text[:80]


def export_zip_path(stem, suffix=''):
    """Zip path in the OS temp dir (not under the app tree).

    Werkzeug's watchdog reloader watches ``*.zip`` inside the project; writing
    the download there restarts the server and wipes in-memory login sessions.
    """
    name = f'{safe_export_stem(stem)}{suffix}.zip'
    return os.path.join(tempfile.gettempdir(), name)


def remove_readonly(func, path, excinfo):
    """Modify file permissions and retry deletion."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path):
    """Remove a directory tree, handling read-only files."""
    if os.path.exists(path):
        shutil.rmtree(path, onerror=remove_readonly)


def create_zip(source_dir, output_path):
    """Create a ZIP archive from a directory."""
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                zf.write(file_path, arcname)
    return output_path


def ensure_dir(path):
    """Ensure a directory exists."""
    os.makedirs(path, exist_ok=True)
    return path
