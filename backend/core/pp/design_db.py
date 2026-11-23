"""
工艺设计数据库（pp_design.db）。

与「上传分析数据库」(tube_analysis.db) 严格区分：
- tube_analysis.db：保存原始管件分析结果（中心线、外径周长、壁厚曲线…）。
- pp_design.db：保存工艺优化输出的最终设计（D0/T0/L0/D1.../L1.../抽管方式…），
  供后续的「普通抽 / TP抽 / Taper / 硬模成型」模块直接调用。

数据按老项目 ``convert_Alldata_format`` 的扁平字典约定保存（params_json），
同时保留原始 solution + 中心线-周长数据，确保下游模块和未来再次编辑都不会失真。
"""
import os
import json
import sqlite3
import pickle
import logging
import traceback
from typing import Any, Optional

from config import PP_DESIGN_DB as DEFAULT_DB_PATH

logger = logging.getLogger(__name__)


_PP_DESIGN_COLUMNS = {
    'display_name': 'TEXT',
    'vehicle_spec': 'TEXT',
    'tube_number': 'TEXT',
    'pp_method': 'TEXT',
    'source_tube_id': 'INTEGER',
    'source_file_name': 'TEXT',
    'source_file_path': 'TEXT',
    'fitness': 'REAL',
    'area_change_rate': 'TEXT',
    'extension_ratio': 'TEXT',
    'params_json': 'TEXT',
    'centerline_perimeter_json': 'TEXT',
    'raw_payload': 'BLOB',
    'created_at': 'TEXT',
}


# 老项目 All_data 的所有参数（中文含义保持完全一致）。
# 顺序影响下游 DataFrame 排版，故按老项目逐字保留。
DESIGN_PARAM_META = [
    {"参数": "D0", "含义": "素材外径"},
    {"参数": "T0", "含义": "素材壁厚"},
    {"参数": "L0", "含义": "素材长度"},
    {"参数": "Dn", "含义": "普通抽外径（扩口段小径、TP抽段大径）"},
    {"参数": "D1", "含义": "扩口段大径"},
    {"参数": "D2", "含义": "TP抽段小径/Taper大径"},
    {"参数": "D3", "含义": "Taper小径/直线段直径"},
    {"参数": "T1", "含义": "壁厚(普通抽/TP抽)"},
    {"参数": "T2", "含义": "壁厚(普通抽/TP抽)"},
    {"参数": "T3", "含义": "壁厚(普通抽/TP抽)"},
    {"参数": "T4", "含义": "壁厚(普通抽/TP抽)"},
    {"参数": "TL1", "含义": "每段壁厚对应长度(普通抽/TP抽)"},
    {"参数": "TL2", "含义": "每段壁厚对应长度(普通抽/TP抽)"},
    {"参数": "TL3", "含义": "每段壁厚对应长度(普通抽/TP抽)"},
    {"参数": "TL4", "含义": "每段壁厚对应长度(普通抽/TP抽)"},
    {"参数": "TL5", "含义": "每段壁厚对应长度(普通抽/TP抽)"},
    {"参数": "TLM1", "含义": "每段过渡段对应长度(普通抽/TP抽)"},
    {"参数": "TLM2", "含义": "每段过渡段对应长度(普通抽/TP抽)"},
    {"参数": "TLM3", "含义": "每段过渡段对应长度(普通抽/TP抽)"},
    {"参数": "TLM4", "含义": "每段过渡段对应长度(普通抽/TP抽)"},
    {"参数": "LL", "含义": "总长度"},
    {"参数": "LL1", "含义": "TP抽大径端直管长度"},
    {"参数": "L1", "含义": "扩口后保留长度"},
    {"参数": "L2", "含义": "TP抽长度"},
    {"参数": "L3", "含义": "Taper长度"},
    {"参数": "L4", "含义": "直线段长度"},
    {"参数": "Ln", "含义": "普通抽长度"},
    {"参数": "D20", "含义": "补充方案2的TP抽段直径"},
    {"参数": "BL", "含义": "弯管长度"},
    {"参数": "B_D", "含义": "弯管直径"},
]

# 用于下游 DataFrame 还原顺序。
DESIGN_PARAM_ORDER = [m["参数"] for m in DESIGN_PARAM_META]
DESIGN_PARAM_DESC = {m["参数"]: m["含义"] for m in DESIGN_PARAM_META}


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(DEFAULT_DB_PATH), exist_ok=True)


def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(pp_design)")
    existing = {row[1] for row in cursor.fetchall()}
    for column, column_type in _PP_DESIGN_COLUMNS.items():
        if column not in existing:
            cursor.execute(f"ALTER TABLE pp_design ADD COLUMN {column} {column_type}")
    conn.commit()


def init_database(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """初始化工艺设计数据库（不存在则创建）。"""
    _ensure_dir()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pp_design (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            display_name TEXT,
            vehicle_spec TEXT,
            tube_number TEXT,
            pp_method TEXT,
            source_tube_id INTEGER,
            source_file_name TEXT,
            source_file_path TEXT,
            fitness REAL,
            area_change_rate TEXT,
            extension_ratio TEXT,
            params_json TEXT,
            centerline_perimeter_json TEXT,
            raw_payload BLOB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    _ensure_schema_columns(conn)
    return conn


def _to_jsonable(value: Any) -> Any:
    """把 numpy / pandas 标量等转成 JSON 可序列化对象。"""
    try:
        import numpy as np  # noqa: WPS433
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            return float(value)
        if isinstance(value, (np.ndarray,)):
            return value.tolist()
    except Exception:
        pass
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def build_params_dict(
    solution: dict,
    pp_method: str,
    vehicle_spec: str = '',
    tube_number: str = '',
    source_file_path: str = '',
    extra: Optional[dict] = None,
) -> dict:
    """把单个 solution 转成「老项目 Alldata 扁平字典」格式。

    主要为下游模块（普通抽 / TP抽 / Taper / 硬模成型）服务，
    这些模块通过 ``Parameter / Value`` 列读取参数，键名沿用老项目
    All_data 的命名（D0, T0, L0, D1..., T1..., TL1..., L1...）。
    """
    params: dict = {}
    for key in DESIGN_PARAM_ORDER:
        val = solution.get(key) if solution else None
        if val is None or val == '':
            params[key] = ''
        else:
            try:
                fv = float(val)
                params[key] = round(fv, 4)
            except (TypeError, ValueError):
                params[key] = str(val)

    # 业务元数据（与老项目字段名严格一致）
    params['抽管方式'] = pp_method or ''
    params['车种规格'] = vehicle_spec or ''
    params['管件号'] = tube_number or ''
    params['管件模型存放位置'] = source_file_path or ''

    if isinstance(extra, dict):
        for k, v in extra.items():
            params[k] = _to_jsonable(v)

    return params


def save_design(
    *,
    display_name: str,
    vehicle_spec: str,
    tube_number: str,
    pp_method: str,
    source_tube_id: Optional[int],
    source_file_name: str,
    source_file_path: str,
    solution: dict,
    fitness: float,
    area_change_rate: Any,
    extension_ratio: Any,
    centerline_perimeter_data: Any = None,
    extra_params: Optional[dict] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> int:
    """落库保存一条工艺设计。返回新行 id（失败返回 -1）。"""
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()

        params_dict = build_params_dict(
            solution=solution or {},
            pp_method=pp_method,
            vehicle_spec=vehicle_spec,
            tube_number=tube_number,
            source_file_path=source_file_path,
            extra=extra_params,
        )

        try:
            params_json = json.dumps(_to_jsonable(params_dict), ensure_ascii=False)
        except Exception:
            params_json = '{}'

        try:
            cp_json = json.dumps(_to_jsonable(centerline_perimeter_data), ensure_ascii=False) \
                if centerline_perimeter_data is not None else None
        except Exception:
            cp_json = None

        try:
            raw_blob = pickle.dumps({
                'solution': solution,
                'extra_params': extra_params or {},
                'centerline_perimeter_data': centerline_perimeter_data,
            })
        except Exception:
            raw_blob = None

        cursor.execute(
            '''
            INSERT INTO pp_design (
                display_name, vehicle_spec, tube_number, pp_method,
                source_tube_id, source_file_name, source_file_path,
                fitness, area_change_rate, extension_ratio,
                params_json, centerline_perimeter_json, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                display_name or '',
                vehicle_spec or '',
                tube_number or '',
                pp_method or '',
                int(source_tube_id) if source_tube_id else None,
                source_file_name or '',
                source_file_path or '',
                float(fitness or 0),
                json.dumps(_to_jsonable(area_change_rate), ensure_ascii=False) if area_change_rate is not None else None,
                json.dumps(_to_jsonable(extension_ratio), ensure_ascii=False) if extension_ratio is not None else None,
                params_json,
                cp_json,
                raw_blob,
            ),
        )
        conn.commit()
        new_id = int(cursor.lastrowid)
        conn.close()
        logger.info("工艺设计已保存: id=%s, name=%s", new_id, display_name)
        return new_id, None
    except sqlite3.OperationalError as exc:
        logger.exception("save_design SQLite operational error")
        return -1, (
            f'数据库无法写入：{exc}. 路径: {db_path}. '
            f'常见原因：安装目录只读、磁盘写保护、被杀软锁定。'
        )
    except Exception as exc:
        logger.exception("save_design 失败")
        return -1, f'{type(exc).__name__}: {exc}'


def list_designs(db_path: str = DEFAULT_DB_PATH) -> list:
    """返回工艺设计列表（轻量摘要）。"""
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT id, display_name, vehicle_spec, tube_number, pp_method,
                   source_tube_id, source_file_name, fitness, created_at
            FROM pp_design
            ORDER BY created_at DESC
            '''
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                'id': int(r[0]),
                'display_name': r[1] or '',
                'vehicle_spec': r[2] or '',
                'tube_number': r[3] or '',
                'pp_method': r[4] or '',
                'source_tube_id': r[5],
                'source_file_name': r[6] or '',
                'fitness': float(r[7] or 0),
                'created_at': r[8],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("list_designs 失败: %s", exc)
        return []


def get_design(design_id: int, db_path: str = DEFAULT_DB_PATH) -> Optional[dict]:
    """读取一条完整的工艺设计。

    返回的 ``tube_params_df_compatible`` 字段是给下游模块直接使用的
    DataFrame-friendly 表格（Parameter/Value/描述 三列）。
    """
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT id, display_name, vehicle_spec, tube_number, pp_method,
                   source_tube_id, source_file_name, source_file_path,
                   fitness, area_change_rate, extension_ratio,
                   params_json, centerline_perimeter_json, raw_payload, created_at
            FROM pp_design WHERE id = ?
            ''',
            (int(design_id),),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None

        (id_, name, vehicle, tube_no, method, src_tube_id, src_name, src_path,
         fitness, acr, ext_ratio, params_json, cp_json, raw_blob, created_at) = row

        params_dict = {}
        if params_json:
            try:
                params_dict = json.loads(params_json)
            except Exception:
                params_dict = {}

        cp_records = []
        if cp_json:
            try:
                cp_records = json.loads(cp_json)
            except Exception:
                cp_records = []

        raw = {}
        if raw_blob:
            try:
                raw = pickle.loads(raw_blob)
            except Exception:
                raw = {}

        tube_params_df_compatible = []
        for key in DESIGN_PARAM_ORDER:
            tube_params_df_compatible.append({
                'Parameter': key,
                'Value': params_dict.get(key, ''),
                '描述': DESIGN_PARAM_DESC.get(key, ''),
            })

        return {
            'id': int(id_),
            'display_name': name or '',
            'vehicle_spec': vehicle or '',
            'tube_number': tube_no or '',
            'pp_method': method or '',
            'source_tube_id': src_tube_id,
            'source_file_name': src_name or '',
            'source_file_path': src_path or '',
            'fitness': float(fitness or 0),
            'area_change_rate': json.loads(acr) if acr else None,
            'extension_ratio': json.loads(ext_ratio) if ext_ratio else None,
            'params': params_dict,
            'tube_params_df_compatible': tube_params_df_compatible,
            'centerline_perimeter_data': cp_records,
            'raw_payload': raw,
            'created_at': created_at,
        }
    except Exception as exc:
        logger.error("get_design 失败: %s", exc)
        return None


def rename_design(design_id: int, new_name: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    if not new_name:
        return False
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE pp_design SET display_name = ? WHERE id = ?",
            (new_name.strip(), int(design_id)),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except Exception as exc:
        logger.error("rename_design 失败: %s", exc)
        return False


def delete_designs(design_ids: list, db_path: str = DEFAULT_DB_PATH) -> int:
    if not design_ids:
        return 0
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        count = 0
        for did in design_ids:
            cursor.execute("DELETE FROM pp_design WHERE id = ?", (int(did),))
            if cursor.rowcount > 0:
                count += 1
        conn.commit()
        conn.close()
        return count
    except Exception as exc:
        logger.error("delete_designs 失败: %s", exc)
        return 0
