"""
Mold Database Module - 列名映射和表创建逻辑。

老程序每个机床+模具类型的数据库表有固定列名。
新程序通过此模块将 mold.parameters dict 映射到正确的 SQL 列名，
并创建符合老程序结构的表。
"""
import os
import re
import sqlite3
import pandas as pd
from contextlib import contextmanager
from config import DATABASE_DIR, NORMAL_MOLD_DIR, TAPER_MOLD_DIR, TP_MOLD_DIR, USER_DB


def _validate_table_name(name):
    if not re.match(r'^[A-Za-z0-9_\u4e00-\u9fff-]+$', name):
        raise ValueError(f"Invalid table name: {name}")
    return name


@contextmanager
def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ─── Normal 模具列名映射 (mold.parameters key → SQL column) ─────────────────

_NORMAL_COLUMN_MAPS = {
    # 通用映射（所有机床共用）
    'ADIE': {
        '%%CD': '%%CD',
        '%%Cd': '%%Cd0',   # 参数名%%Cd -> SQL列%%Cd0（老程序约定）
        '%%Cd1': '%%Cd1',
        '%%Cd2': '%%Cd2',
        '件数': '件数', '成品直径': '成品直径',
        '日期': '日期', '模具名称': '模具名称',
        '设计者': '设计者', '车种规格': '车种规格',
    },
    'DIEO': {
        '%%CD': '%%CD',
        '%%Cd': '%%Cd0',
        '%%Cd1': '%%Cd1',
        '%%Cd2': '%%Cd2',
        '件数': '件数', '成品直径': '成品直径',
        '日期': '日期', '模具名称': '模具名称',
        '设计者': '设计者', '车种规格': '车种规格',
    },
    'ADBT': {
        '%%CA': '%%CA',
        '%%Cd1': '%%Cd1', '%%Cd2': '%%Cd2', '%%Cd3': '%%Cd3',
        '%%Cd4': '%%Cd4', '%%Cd5': '%%Cd5',
        '(L6)': '(L6)',
        'C': 'C', 'L': 'L',
        'L1': 'L1', 'L2': 'L2', 'L3': 'L3', 'L4': 'L4', 'L5': 'L5',
        'LT': 'LT', 'T': 'T',
        '抽管尺寸': '抽管尺寸', '缩管模直径': '缩管模直径',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', 'M螺纹': 'M螺纹',
        '车种规格': '车种规格',
    },
    # SS01: DC0124/DC0121 共用（无L1）
    'SS01': {
        '%%CA': '%%CA', '%%CD': '%%CD', 'BXB': 'BXB',
        'L': 'L', 'LT': 'LT', 'M螺纹': 'M螺纹',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0125 SS01 多 L1
    'SS01_DC0125': {
        '%%CA': '%%CA', '%%CD': '%%CD', 'BXB': 'BXB',
        'L1': 'L1', 'L': 'L', 'LT': 'LT', 'M螺纹': 'M螺纹',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0124 AD02（用%%CA）
    'DC0124_AD02': {
        '%%CA': '%%CA',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0121 AD02（用%%CD+A）
    'DC0121_AD02': {
        '%%CD': '%%CD', 'A': 'A',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0124 AD01（只有A）
    'DC0124_AD01': {
        'A': 'A',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0121 AD01（%%CD+A）
    'DC0121_AD01': {
        '%%CD': '%%CD', 'A': 'A',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0125 AD06_S / AD06_F（%%CD+成品直径）
    'DC0125_AD06': {
        '%%CD': '%%CD', '成品直径': '成品直径',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0124 AD03（只有%%CD）
    'DC0124_AD03': {
        '%%CD': '%%CD',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0121 AD03（%%CD+A）
    'DC0121_AD03': {
        '%%CD': '%%CD', 'A': 'A',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0121 AD04（%%CD+A）
    'DC0121_AD04': {
        '%%CD': '%%CD', 'A': 'A',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
    # DC0125 AD07（只有%%CD）
    'DC0125_AD07': {
        '%%CD': '%%CD',
        '件数': '件数', '模具名称': '模具名称',
        '日期': '日期', '设计者': '设计者', '车种规格': '车种规格',
    },
}

# Normal 表创建语句（按机床+模具类型）
_NORMAL_CREATE_SQL = {
    'DC0124': {
        'AD03': """CREATE TABLE IF NOT EXISTS AD03 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD02': """CREATE TABLE IF NOT EXISTS AD02 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD01': """CREATE TABLE IF NOT EXISTS AD01 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'DIEO': """CREATE TABLE IF NOT EXISTS DIEO (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%Cd0' TEXT NOT NULL, '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL,
            件数 TEXT NOT NULL, 成品直径 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'SS01': """CREATE TABLE IF NOT EXISTS SS01 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL, '%%CD' TEXT NOT NULL, BXB TEXT NOT NULL,
            L TEXT NOT NULL, LT TEXT NOT NULL, M螺纹 TEXT NOT NULL,
            件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADIE': """CREATE TABLE IF NOT EXISTS ADIE (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%Cd0' TEXT NOT NULL, '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL,
            件数 TEXT NOT NULL, 成品直径 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADBT': """CREATE TABLE IF NOT EXISTS ADBT (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL,
            '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL, '%%Cd3' TEXT NOT NULL,
            '%%Cd4' TEXT NOT NULL, '%%Cd5' TEXT NOT NULL,
            '(L6)' TEXT NOT NULL, C TEXT NOT NULL, L TEXT NOT NULL,
            L1 TEXT NOT NULL, L2 TEXT NOT NULL, L3 TEXT NOT NULL, L4 TEXT NOT NULL, L5 TEXT NOT NULL,
            LT TEXT NOT NULL, T TEXT NOT NULL,
            抽管尺寸 TEXT NOT NULL, 缩管模直径 TEXT NOT NULL,
            件数 TEXT NOT NULL, 模具名称 TEXT NOT NULL, 日期 TEXT NOT NULL,
            设计者 TEXT NOT NULL, M螺纹 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
    },
    'DC0121': {
        'AD03': """CREATE TABLE IF NOT EXISTS AD03 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD04': """CREATE TABLE IF NOT EXISTS AD04 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD02': """CREATE TABLE IF NOT EXISTS AD02 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD01': """CREATE TABLE IF NOT EXISTS AD01 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'DIEO': """CREATE TABLE IF NOT EXISTS DIEO (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%Cd0' TEXT NOT NULL, '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL,
            件数 TEXT NOT NULL, 成品直径 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'SS01': """CREATE TABLE IF NOT EXISTS SS01 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL, '%%CD' TEXT NOT NULL, BXB TEXT NOT NULL,
            L TEXT NOT NULL, LT TEXT NOT NULL, M螺纹 TEXT NOT NULL,
            件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADIE': """CREATE TABLE IF NOT EXISTS ADIE (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%Cd0' TEXT NOT NULL, '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL,
            件数 TEXT NOT NULL, 成品直径 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADBT': """CREATE TABLE IF NOT EXISTS ADBT (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL,
            '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL, '%%Cd3' TEXT NOT NULL,
            '%%Cd4' TEXT NOT NULL, '%%Cd5' TEXT NOT NULL,
            '(L6)' TEXT NOT NULL, C TEXT NOT NULL, L TEXT NOT NULL,
            L1 TEXT NOT NULL, L2 TEXT NOT NULL, L3 TEXT NOT NULL, L4 TEXT NOT NULL, L5 TEXT NOT NULL,
            LT TEXT NOT NULL, T TEXT NOT NULL,
            抽管尺寸 TEXT NOT NULL, 缩管模直径 TEXT NOT NULL,
            件数 TEXT NOT NULL, 模具名称 TEXT NOT NULL, 日期 TEXT NOT NULL,
            设计者 TEXT NOT NULL, M螺纹 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
    },
    'DC0125': {
        'AD07': """CREATE TABLE IF NOT EXISTS AD07 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD06_F': """CREATE TABLE IF NOT EXISTS AD06_F (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, 成品直径 TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD06_S': """CREATE TABLE IF NOT EXISTS AD06_S (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, 成品直径 TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'DIEO': """CREATE TABLE IF NOT EXISTS DIEO (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%Cd0' TEXT NOT NULL, '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL,
            件数 TEXT NOT NULL, 成品直径 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'SS01': """CREATE TABLE IF NOT EXISTS SS01 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL, '%%CD' TEXT NOT NULL, BXB TEXT NOT NULL,
            L1 TEXT NOT NULL, L TEXT NOT NULL, LT TEXT NOT NULL, M螺纹 TEXT NOT NULL,
            件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADIE': """CREATE TABLE IF NOT EXISTS ADIE (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%Cd0' TEXT NOT NULL, '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL,
            件数 TEXT NOT NULL, 成品直径 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADBT': """CREATE TABLE IF NOT EXISTS ADBT (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CA' TEXT NOT NULL,
            '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL, '%%Cd3' TEXT NOT NULL,
            '%%Cd4' TEXT NOT NULL, '%%Cd5' TEXT NOT NULL,
            '(L6)' TEXT NOT NULL, C TEXT NOT NULL, L TEXT NOT NULL,
            L1 TEXT NOT NULL, L2 TEXT NOT NULL, L3 TEXT NOT NULL, L4 TEXT NOT NULL, L5 TEXT NOT NULL,
            LT TEXT NOT NULL, T TEXT NOT NULL,
            抽管尺寸 TEXT NOT NULL, 缩管模直径 TEXT NOT NULL,
            件数 TEXT NOT NULL, 模具名称 TEXT NOT NULL, 日期 TEXT NOT NULL,
            设计者 TEXT NOT NULL, M螺纹 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
    },
}

# TP 模具表创建语句
# 注意：ADIE/ADIE_ 使用老库列名 s_bore/b_bore/length/L，与 mold 参数 T_s/T_D/T_L/T_e 通过 col_map 映射
_TP_CREATE_SQL = {
    'DC0128': {
        'AD01': """CREATE TABLE IF NOT EXISTS AD01 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD01_': """CREATE TABLE IF NOT EXISTS AD01_ (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADIE': """CREATE TABLE IF NOT EXISTS ADIE (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            s_bore TEXT NOT NULL, b_bore TEXT NOT NULL, length TEXT NOT NULL,
            L TEXT NOT NULL, line TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADIE_': """CREATE TABLE IF NOT EXISTS ADIE_ (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            s_bore TEXT NOT NULL, b_bore TEXT NOT NULL, length TEXT NOT NULL,
            line TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADBT': """CREATE TABLE IF NOT EXISTS ADBT (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            L1 TEXT NOT NULL, M1 TEXT NOT NULL, L2 TEXT NOT NULL, M2 TEXT NOT NULL,
            L3 TEXT NOT NULL, IL TEXT NOT NULL, L4 TEXT NOT NULL,
            '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL, '%%Cd3' TEXT NOT NULL, '%%Cd4' TEXT NOT NULL,
            M TEXT NOT NULL, LT TEXT NOT NULL, '%%CA' TEXT NOT NULL, L TEXT NOT NULL,
            件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'ADBT_': """CREATE TABLE IF NOT EXISTS ADBT_ (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            L1 TEXT NOT NULL, M1 TEXT NOT NULL, L2 TEXT NOT NULL, M2 TEXT NOT NULL,
            L3 TEXT NOT NULL, IL TEXT NOT NULL, L4 TEXT NOT NULL,
            '%%Cd1' TEXT NOT NULL, '%%Cd2' TEXT NOT NULL, '%%Cd3' TEXT NOT NULL, '%%Cd4' TEXT NOT NULL,
            M TEXT NOT NULL, LT TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'AD04': """CREATE TABLE IF NOT EXISTS AD04 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
    },
}

# Taper 模具表创建语句
_TAPER_CREATE_SQL = {
    'EC0120': {
        'B000': """CREATE TABLE IF NOT EXISTS B000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%CA' TEXT NOT NULL, '%%CB' TEXT NOT NULL,
            L TEXT NOT NULL, L_m TEXT NOT NULL,
            line1 TEXT NOT NULL, line2 TEXT NOT NULL, line3 TEXT NOT NULL,
            line4 TEXT NOT NULL, line5 TEXT NOT NULL, line6 TEXT NOT NULL,
            line7 TEXT NOT NULL, line8 TEXT NOT NULL, line9 TEXT NOT NULL,
            line10 TEXT NOT NULL, line11 TEXT NOT NULL, line12 TEXT NOT NULL,
            line13 TEXT NOT NULL, line14 TEXT NOT NULL, line15 TEXT NOT NULL,
            件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'C000': """CREATE TABLE IF NOT EXISTS C000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'J000': """CREATE TABLE IF NOT EXISTS J000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'E000': """CREATE TABLE IF NOT EXISTS E000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            B TEXT NOT NULL, D TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'I000': """CREATE TABLE IF NOT EXISTS I000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            B TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'H000': """CREATE TABLE IF NOT EXISTS H000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            B TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
    },
    'EC0121': {
        'B000': """CREATE TABLE IF NOT EXISTS B000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            '%%CD' TEXT NOT NULL, '%%CA' TEXT NOT NULL, '%%CB' TEXT NOT NULL,
            L TEXT NOT NULL, L_m TEXT NOT NULL,
            line1 TEXT NOT NULL, line2 TEXT NOT NULL, line3 TEXT NOT NULL,
            line4 TEXT NOT NULL, line5 TEXT NOT NULL, line6 TEXT NOT NULL,
            line7 TEXT NOT NULL, line8 TEXT NOT NULL, line9 TEXT NOT NULL,
            line10 TEXT NOT NULL, line11 TEXT NOT NULL, line12 TEXT NOT NULL,
            line13 TEXT NOT NULL, line14 TEXT NOT NULL, line15 TEXT NOT NULL,
            件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'C000': """CREATE TABLE IF NOT EXISTS C000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
        'J000': """CREATE TABLE IF NOT EXISTS J000 (
            图号 INTEGER PRIMARY KEY AUTOINCREMENT,
            A TEXT NOT NULL, 件数 TEXT NOT NULL, 日期 TEXT NOT NULL,
            模具名称 TEXT NOT NULL, 设计者 TEXT NOT NULL, 车种规格 TEXT NOT NULL)""",
    },
}


def _get_normal_col_map(machine_type: str, table_name: str) -> dict:
    """Get column map for Normal mold."""
    key = f"{machine_type}_{table_name}" if table_name in (
        'AD01', 'AD02', 'AD03', 'AD04', 'SS01', 'AD06_F', 'AD06_S', 'AD07'
    ) else table_name
    # SS01 for DC0125 includes L1
    if machine_type == 'DC0125' and table_name == 'SS01':
        key = 'SS01_DC0125'
    if machine_type == 'DC0124' and table_name == 'SS01':
        key = 'SS01'
    if machine_type == 'DC0121' and table_name == 'SS01':
        key = 'SS01'
    return _NORMAL_COLUMN_MAPS.get(key, {})


# ─── UserDB ────────────────────────────────────────────────────────────────

def _user_row_to_dict(row):
    """将 user 表查询行转为字典（列顺序与 SELECT 一致）。"""
    from core.common.workrole_access import normalize_workrole

    return {
        'id': row[0],
        'name': row[1],
        'password': row[2],
        'license': row[3],
        'authority': row[4],
        'workrole': normalize_workrole(row[5]),
        'created_at': row[6] if len(row) > 6 else None,
        'updated_at': row[7] if len(row) > 7 else None,
        'expires_at': row[8] if len(row) > 8 else None,
        'approval_status': row[9] if len(row) > 9 else 'approved',
        'device_token': row[10] if len(row) > 10 else None,
    }


_USER_SELECT = (
    'id, name, password, license, authority, workrole, '
    'created_at, updated_at, expires_at, approval_status, device_token'
)


def _migrate_user_schema(conn):
    """为已有 user 表补齐后续版本新增的列。"""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(user)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'created_at' not in columns:
        conn.execute("ALTER TABLE user ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    if 'updated_at' not in columns:
        conn.execute("ALTER TABLE user ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    if 'expires_at' not in columns:
        conn.execute("ALTER TABLE user ADD COLUMN expires_at TEXT")
    if 'approval_status' not in columns:
        conn.execute("ALTER TABLE user ADD COLUMN approval_status TEXT DEFAULT 'approved'")
        conn.execute("UPDATE user SET approval_status = 'approved' WHERE approval_status IS NULL")
    if 'device_token' not in columns:
        conn.execute("ALTER TABLE user ADD COLUMN device_token TEXT")
    conn.execute("UPDATE user SET workrole = '硬模成型' WHERE workrole = '硬膜成型'")
    conn.commit()


class UserDB:
    """Unified user database operations."""

    def __init__(self):
        os.makedirs(os.path.dirname(USER_DB), exist_ok=True)
        db_exists = os.path.exists(USER_DB)
        with get_connection(USER_DB) as conn:
            if not db_exists:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS user (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        password TEXT NOT NULL,
                        license TEXT NOT NULL,
                        authority TEXT NOT NULL DEFAULT 'user',
                        workrole TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        expires_at TEXT,
                        approval_status TEXT DEFAULT 'approved',
                        device_token TEXT
                    )
                """)
                conn.execute("""
                    INSERT INTO user (id, name, password, license, authority, workrole)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (10000, 'admin', 'Admin@2027', 'Admin2027LicenseKey!', 'admin', 'ALL'))
                conn.commit()
                print('用户数据库创建成功，默认管理员: admin / Admin@2027')
            _migrate_user_schema(conn)

    def query_user(self, user_id, password):
        with get_connection(USER_DB) as conn:
            cursor = conn.cursor()
            row = None
            try:
                uid = int(user_id)
                cursor.execute(f"SELECT {_USER_SELECT} FROM user WHERE id = ?", (uid,))
                row = cursor.fetchone()
            except (TypeError, ValueError):
                row = None
            if not row:
                cursor.execute(f"SELECT {_USER_SELECT} FROM user WHERE name = ?", (str(user_id),))
                row = cursor.fetchone()
            if not row:
                return False, '用户不存在'
            if password != row[2]:
                return False, '密码错误'
            return True, _user_row_to_dict(row)

    def register_user(self, data):
        with get_connection(USER_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM user WHERE id = ?", (int(data['id']),))
            if cursor.fetchone():
                return False, '用户已存在'
            cursor.execute(
                "INSERT INTO user (id, name, password, license, authority, workrole, expires_at, "
                "approval_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(data['id']), data['name'], data['password'],
                    data['license'], data.get('authority', 'user'), data['workrole'],
                    data.get('expires_at'),
                    data.get('approval_status', 'approved'),
                )
            )
            conn.commit()
            return True, '用户注册成功'

    def query_all_users(self):
        with get_connection(USER_DB) as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {_USER_SELECT} FROM user")
            rows = cursor.fetchall()
            if not rows:
                return False, '没有用户'
            return True, [_user_row_to_dict(r) for r in rows]

    def delete_user(self, user_id):
        with get_connection(USER_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM user WHERE id = ?", (int(user_id),))
            if not cursor.fetchone():
                return False, '用户不存在'
            cursor.execute("DELETE FROM user WHERE id = ?", (int(user_id),))
            conn.commit()
            return True, '用户删除成功'

    def update_user(self, data):
        user_id = int(data['id'])
        allowed_fields = {
            'name', 'password', 'license', 'authority', 'workrole', 'expires_at',
            'approval_status', 'device_token',
        }
        with get_connection(USER_DB) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM user WHERE id = ?", (user_id,))
            if not cursor.fetchone():
                return False, '用户不存在'
            fields = []
            values = []
            for key, value in data.items():
                if key == 'id':
                    continue
                if key not in allowed_fields:
                    return False, f'不允许更新字段: {key}'
                fields.append(f"{key} = ?")
                values.append(value)
            if not fields:
                return False, '没有可更新的信息'
            fields.append("updated_at = CURRENT_TIMESTAMP")
            values.append(user_id)
            cursor.execute(f"UPDATE user SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
            return True, '用户信息更新成功'

    def get_user(self, user_id):
        with get_connection(USER_DB) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT {_USER_SELECT} FROM user WHERE id = ?",
                (int(user_id),),
            )
            row = cursor.fetchone()
            if not row:
                return False, '用户不存在'
            return True, _user_row_to_dict(row)


# ─── MoldDB ────────────────────────────────────────────────────────────────

class MoldDB:
    """Mold database operations for Normal/TP/Taper modules.

    Uses fixed table schemas matching the old program's SQL structures.
    Inserts are mapped from mold.parameters dict to the correct SQL column names.
    """

    def __init__(self, mold_type):
        if mold_type == 'normal':
            self.db_dir = NORMAL_MOLD_DIR
            self._create_sql = _NORMAL_CREATE_SQL
        elif mold_type == 'tp':
            self.db_dir = TP_MOLD_DIR
            self._create_sql = _TP_CREATE_SQL
        elif mold_type == 'taper':
            self.db_dir = TAPER_MOLD_DIR
            self._create_sql = _TAPER_CREATE_SQL
        else:
            raise ValueError(f"Unknown mold type: {mold_type}")

    def _get_db_path(self, machine_type):
        return os.path.join(self.db_dir, f"{machine_type}.db")

    def _get_table_sql(self, machine_type, table_name) -> str | None:
        """Get CREATE TABLE SQL for a machine+table combo."""
        machines = self._create_sql.get(machine_type, {})
        return machines.get(table_name)

    def _get_col_map(self, machine_type, table_name) -> dict:
        """Get parameter key → SQL column name mapping."""
        if hasattr(self, '_create_sql') and self._create_sql is _NORMAL_CREATE_SQL:
            return _get_normal_col_map(machine_type, table_name)
        if hasattr(self, '_create_sql') and self._create_sql is _TP_CREATE_SQL:
            # mold 参数名 → 现有 SQLite 列名（兼容老库 ADIE 的 s_bore/b_bore/...）
            adie_map = {
                'T_s': 's_bore', 'T_D': 'b_bore', 'T_L': 'length', 'T_e': 'L', 'line': 'line',
                '件数': '件数', '日期': '日期', '模具名称': '模具名称',
                '设计者': '设计者', '车种规格': '车种规格',
            }
            adie_form_map = {
                'T_s': 's_bore', 'T_D': 'b_bore', 'T_L': 'length', 'line': 'line',
                '件数': '件数', '日期': '日期', '模具名称': '模具名称',
                '设计者': '设计者', '车种规格': '车种规格',
            }
            adbt_map = {
                'L1': 'L1', 'M1': 'M1', 'L2': 'L2', 'M2': 'M2', 'L3': 'L3',
                'IL': 'IL', 'L4': 'L4',
                '%%Cd1': '%%Cd1', '%%Cd2': '%%Cd2', '%%Cd3': '%%Cd3', '%%Cd4': '%%Cd4',
                'M': 'M', 'LT': 'LT', '件数': '件数', '日期': '日期',
                '模具名称': '模具名称', '设计者': '设计者', '车种规格': '车种规格',
            }
            return {
                'AD01': {'%%CD': '%%CD', '件数': '件数', '日期': '日期', '模具名称': '模具名称',
                         '设计者': '设计者', '车种规格': '车种规格'},
                'AD01_': {'%%CD': '%%CD', '件数': '件数', '日期': '日期', '模具名称': '模具名称',
                          '设计者': '设计者', '车种规格': '车种规格'},
                'ADIE': adie_map,
                'ADIE_': adie_form_map,
                'ADBT': {**adbt_map, '%%CA': '%%CA', 'L': 'L'},
                'ADBT_': adbt_map,
                'AD04': {'A': 'A', '件数': '件数', '日期': '日期', '模具名称': '模具名称',
                         '设计者': '设计者', '车种规格': '车种规格'},
            }.get(table_name, {})
        if hasattr(self, '_create_sql') and self._create_sql is _TAPER_CREATE_SQL:
            base = {'件数': '件数', '日期': '日期', '模具名称': '模具名称',
                    '设计者': '设计者', '车种规格': '车种规格'}
            if table_name == 'B000':
                return {'%%CD': '%%CD', '%%CA': '%%CA', '%%CB': '%%CB',
                        'L': 'L', 'L_m': 'L_m',
                        **{f'line{i}': f'line{i}' for i in range(1, 16)},
                        **base}
            if table_name in ('C000', 'J000'):
                return {'A': 'A', **base}
            if table_name == 'E000':
                return {'B': 'B', 'D': 'D', **base}
            if table_name in ('I000', 'H000'):
                return {'B': 'B', **base}
            return base
        return {}

    def query(self, machine_type, table_name):
        """Query mold data by machine type and table name."""
        db_path = self._get_db_path(machine_type)
        if not os.path.exists(db_path):
            return pd.DataFrame()
        table = _validate_table_name(table_name)
        with get_connection(db_path) as conn:
            try:
                df = pd.read_sql_query(f'SELECT * FROM [{table}]', conn)
                return df
            except Exception:
                return pd.DataFrame()

    def query_max_graph_number(self, machine_type, table_name):
        """Get the maximum serial number from the 图号 column."""
        db_path = self._get_db_path(machine_type)
        if not os.path.exists(db_path):
            return 0
        table = _validate_table_name(table_name)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"SELECT MAX(CAST(SUBSTR(图号, -4) AS INTEGER)) FROM [{table}]"
                )
                result = cursor.fetchone()[0]
                return result if result else 0
            except Exception:
                return 0

    @staticmethod
    def _ensure_columns(conn, table: str, columns):
        """为已存在的旧表补齐缺失列（TEXT），避免 CREATE IF NOT EXISTS 无法改 schema。"""
        cur = conn.execute(f"PRAGMA table_info([{table}])")
        existing = {row[1] for row in cur.fetchall()}
        for col in columns:
            if not col or col == '图号' or col in existing:
                continue
            conn.execute(f'ALTER TABLE [{table}] ADD COLUMN [{col}] TEXT')

    def insert_data(self, machine_type, table_name, params_dict: dict):
        """Insert mold parameters into the database with correct column mapping.

        Steps:
          1. Ensure DB dir and file exist.
          2. Create table with fixed schema (if not exists).
          3. Map params_dict keys → SQL column names.
          4. 对旧表补齐缺失列后插入（缺省填 '/'）。
        """
        db_path = self._get_db_path(machine_type)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        table = _validate_table_name(table_name)

        # Step 1: Create table if not exists
        create_sql = self._get_table_sql(machine_type, table_name)
        if create_sql:
            with get_connection(db_path) as conn:
                conn.execute(create_sql)
                conn.commit()

        # Step 2: Map parameter keys to SQL column names
        col_map = self._get_col_map(machine_type, table_name)
        if not col_map:
            # Fallback: 写入除「图号」外的全部参数（图号多为 AUTOINCREMENT）
            col_map = {
                k: k for k in params_dict.keys()
                if k and k != '图号'
            }

        if not col_map:
            raise ValueError(f"无法映射表 {machine_type}.{table_name} 的列，且无可用参数")

        # Build row: sql_col → value
        sql_cols = list(dict.fromkeys(col_map.values()))  # 保序去重
        row = {}
        for param_key, sql_col in col_map.items():
            val = params_dict.get(param_key, '/')
            if val is None or val == '':
                val = '/'
            row[sql_col] = val

        # Step 3: 补齐旧表缺失列；对旧表仍为 NOT NULL 但映射未覆盖的列填 '/'
        with get_connection(db_path) as conn:
            self._ensure_columns(conn, table, sql_cols)
            cur = conn.execute(f"PRAGMA table_info([{table}])")
            for _cid, name, _ctype, notnull, dflt, pk in cur.fetchall():
                if pk or name in row:
                    continue
                if int(notnull) == 1 and dflt is None:
                    row[name] = '/'
                    if name not in sql_cols:
                        sql_cols.append(name)
            placeholders = ', '.join(['?'] * len(sql_cols))
            col_names = ', '.join([f'[{c}]' for c in sql_cols])
            insert_sql = f"INSERT INTO [{table}] ({col_names}) VALUES ({placeholders})"
            conn.execute(insert_sql, [str(row.get(c, '/')) for c in sql_cols])
            conn.commit()
        return True
