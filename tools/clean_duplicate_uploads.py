"""清理同名上传产生的 ``_2…_N`` 重复件（2026-11-11 起上传改为覆盖同名，不再产生副本）。

用法::

    python .tmp/clean_duplicate_uploads.py --root D:\\GiantTubeApp --dry-run
    python .tmp/clean_duplicate_uploads.py --root D:\\GiantTubeApp --apply

规则：
  * 件号 = 文件名去扩展名；``X_2.stp`` 这类在 ``X.stp`` / ``X.step`` 存在时视为重复件；
  * 重复件移到 ``uploads/_removed_duplicates/<时间戳>/``，不直接删（可回滚）；
  * 同时删掉重复件的测量缓存 ``_cache/<件>_measured.json`` / ``_measured_v1.json``
    / ``<件>_profile.json``；
  * ``--dedupe-builtin-copies``：``uploads/`` 里与顶层内置件逐字节相同的副本也移走
    （让件表回到 builtin 口径）；
  * 分析数据库 ``tube_analysis`` 一条不动。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

SUFFIXES = {'.stp', '.step'}
DUP_RE = re.compile(r'^(?P<base>.+)_(?P<index>\d{1,3})$')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _step_files(directory: Path) -> list:
    if not directory.is_dir():
        return []
    return [path for path in sorted(directory.iterdir(), key=lambda p: p.name.lower())
            if path.is_file() and path.suffix.lower() in SUFFIXES
            and not path.name.startswith(('_', '.'))]


def scan(uploads: Path, src: Path) -> tuple:
    """返回 ``(canonical, duplicates)``；顶层内置件与规范上传件都算「规范件名」。"""
    builtin_names = {path.stem for path in _step_files(src)}
    canonical = {}
    dups = []
    for path in _step_files(uploads):
        match = DUP_RE.match(path.stem)
        if match and match.group('base') in builtin_names:
            dups.append(path)
            continue
        canonical[path.stem] = path
    return canonical, dups


def builtin_copies(root: Path, canonical: dict) -> list:
    src = root / 'gnt管件'
    found = []
    for stem, upload_path in canonical.items():
        for suffix in SUFFIXES:
            builtin = src / f'{stem}{suffix}'
            if not builtin.is_file():
                continue
            try:
                if sha256(builtin) == sha256(upload_path):
                    found.append(upload_path)
            except OSError:
                pass
            break
    return found


def cache_paths(root: Path, stem: str) -> list:
    cache_dir = root / 'entity_out' / '_cache'
    names = [f'{stem}_measured.json', f'{stem}_measured_v1.json', f'{stem}_profile.json']
    return [cache_dir / name for name in names if (cache_dir / name).is_file()]


def prune_queue(root: Path, stems: set, apply: bool) -> int:
    queue_file = root / 'entity_out' / '_config' / 'import_queue.json'
    if not queue_file.is_file():
        return 0
    try:
        payload = json.loads(queue_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    parts = payload.get('parts') if isinstance(payload.get('parts'), list) else []
    kept = [item for item in parts
            if isinstance(item, dict) and str(item.get('part') or '') not in stems]
    removed = len(parts) - len(kept)
    if removed and apply:
        payload['parts'] = kept
        payload['version'] = payload.get('version') or 1
        tmp = queue_file.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(queue_file)
    return removed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='清理 _N 重复上传件与缓存')
    parser.add_argument('--root', required=True, help='安装/仓库根目录（含 gnt管件 与 entity_out）')
    parser.add_argument('--apply', action='store_true', help='真正执行；缺省只报告')
    parser.add_argument('--dry-run', action='store_true', help='只报告（默认行为）')
    parser.add_argument('--dedupe-builtin-copies', action='store_true',
                        help='把与内置件逐字节相同的 uploads 副本也移走')
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    uploads = root / 'gnt管件' / 'uploads'
    if not uploads.is_dir():
        print(f'[skip] 没有上传目录：{uploads}')
        return 0

    apply = bool(args.apply) and not args.dry_run
    src = root / 'gnt管件'
    canonical, targets = scan(uploads, src)
    if args.dedupe_builtin_copies:
        targets += builtin_copies(root, canonical)
    targets = sorted({path for path in targets}, key=lambda p: p.name.lower())

    if not targets:
        print(f'[ok] {uploads} 没有需要清理的重复件')
        return 0

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    trash = uploads / '_removed_duplicates' / stamp
    freed = 0
    removed_stems = set()
    print(f'{"移除" if apply else "将移除"} {len(targets)} 个文件 → {trash}')
    for path in targets:
        size = path.stat().st_size if path.exists() else 0
        freed += size
        removed_stems.add(path.stem)
        print(f'  {"MOVE" if apply else "DRY "} {path.name:28s} {size/1048576:8.1f} MB')
        if apply:
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(trash / path.name))

    caches = []
    for stem in sorted(removed_stems):
        caches.extend(cache_paths(root, stem))
    cache_bytes = sum(path.stat().st_size for path in caches)
    print(f'{"删除" if apply else "将删除"} {len(caches)} 个缓存文件（{cache_bytes/1048576:.1f} MB）')
    for path in caches:
        print(f'  {"DEL " if apply else "DRY "} {path.name}')
        if apply:
            try:
                path.unlink()
            except OSError as exc:
                print(f'  [warn] 删缓存失败：{path} - {exc}')
    freed += cache_bytes

    queue_removed = prune_queue(root, removed_stems, apply)
    if queue_removed:
        print(f'{"清出" if apply else "将清出"}主流程队列 {queue_removed} 条')

    print(f'合计{"已释放" if apply else "可释放"} {freed/1048576:.1f} MB')
    if not apply:
        print('（这是 dry-run；加 --apply 真正执行）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
