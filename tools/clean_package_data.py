#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打包前清理本机测试数据（默认只预览，加 --apply 才真正删除）。

清理内容：
  1. entity_out/                  一体化产物、测量缓存（*_measured*.json）、报告、route_prefs
  2. gnt管件/uploads/             流水线上传的管件
  3. backend/data/pp_saved_sources、backend/data/uploads   工艺优化上传的副本
  4. backend/data/logs/*.log      运行日志
  5. .tmp/                        临时目录
  6. license/machine_anchor.json  本机机器码锚点（绝不能进安装包）
  7. license/license.dat          本机测试许可证（交付包用 PUT_LICENSE_HERE.txt 占位）
  8. tube_analysis.db / pp_design.db 清空业务表并 VACUUM

不动的：gnt管件根目录的原始 STEP、DC0128 模板库、user.db（仅 admin）、frontend/dist、代码与文档。
用法：
    python tools/clean_package_data.py            # 预览
    python tools/clean_package_data.py --apply    # 执行
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def size_of(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def plan():
    items = [
        ("一体化产物与测量缓存", ROOT / "entity_out", "dir"),
        ("流水线上传的管件", ROOT / "gnt管件" / "uploads", "dir"),
        ("工艺优化上传副本", ROOT / "backend" / "data" / "pp_saved_sources", "dir"),
        ("工艺优化上传目录", ROOT / "backend" / "data" / "uploads", "dir"),
        ("运行日志", ROOT / "backend" / "data" / "logs", "logs"),
        ("临时目录", ROOT / ".tmp", "dir"),
        ("机器码锚点", ROOT / "license" / "machine_anchor.json", "file"),
        ("本机测试许可证", ROOT / "license" / "license.dat", "file"),
    ]
    return items


def db_plan():
    return [
        (ROOT / "backend" / "database" / "pp" / "tube_analysis.db", "tube_analysis"),
        (ROOT / "backend" / "database" / "pp" / "pp_design.db", "pp_design"),
    ]


def count_rows(path: Path, table: str) -> int | None:
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


def show(apply: bool) -> int:
    total = 0
    print(f"仓库：{ROOT}")
    print(f"模式：{'执行删除' if apply else '仅预览（加 --apply 执行）'}\n")

    for label, path, kind in plan():
        if kind == "logs":
            files = list(path.glob("*.log")) if path.is_dir() else []
            size = sum(f.stat().st_size for f in files)
            print(f"[日志] {label:<18} {len(files)} 个文件  {human(size)}")
            total += size
            if apply and path.is_dir():
                for f in files:
                    try:
                        f.unlink(missing_ok=True)
                    except OSError as exc:
                        print(f"        ! 无法删除 {f.name}：{exc}（服务停止后重试）")
            continue
        size = size_of(path)
        if path.exists():
            print(f"[删除] {label:<18} {path.relative_to(ROOT)}  {human(size)}")
        else:
            print(f"[跳过] {label:<18} 不存在")
        total += size
        if apply and path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    for path, table in db_plan():
        rows = count_rows(path, table)
        before = size_of(path)
        print(f"[清表] {path.name:<18} {table}: {rows if rows is not None else '—'} 行  {human(before)}")
        if apply and path.is_file():
            conn = sqlite3.connect(str(path))
            try:
                conn.execute(f'DELETE FROM "{table}"')
                try:
                    conn.execute("DELETE FROM sqlite_sequence")
                except sqlite3.Error:
                    pass
                conn.commit()
                conn.execute("VACUUM")
                conn.commit()
            finally:
                conn.close()
            print(f"        → 清空后 {human(size_of(path))}")

    print(f"\n预计释放：{human(total)}（数据库 VACUUM 后还会再缩）")
    if not apply:
        print("确认无误后执行：python tools/clean_package_data.py --apply")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="打包前清理本机测试数据")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只预览）")
    args = parser.parse_args(argv)
    return show(args.apply)


if __name__ == "__main__":
    sys.exit(main())
