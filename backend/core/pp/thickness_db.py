"""
PP module - Database operations for tube analysis.
Extracted from 2026-5-17/bin/pp_bin/thickness_analysis.py (lines 60-1700).
Streamlit removed.
"""
import os
import sqlite3
import pickle
import hashlib
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from config import PP_TUBE_DB as DEFAULT_DB_PATH

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    _APP_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _APP_TZ = timezone(timedelta(hours=8))

_CREATED_AT_LOCAL_META_KEY = "tube_created_at_local_plus8"


def _now_local_sql() -> str:
    """Wall-clock timestamp for 创建时间 (Asia/Shanghai, UTC+8)."""
    return datetime.now(_APP_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _ensure_created_at_local(conn: sqlite3.Connection) -> None:
    """Shift legacy UTC CURRENT_TIMESTAMP values to local time once."""
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS pp_schema_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    cursor.execute(
        "SELECT value FROM pp_schema_meta WHERE key = ?",
        (_CREATED_AT_LOCAL_META_KEY,),
    )
    row = cursor.fetchone()
    if row and str(row[0]) == "1":
        return
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tube_analysis'"
    )
    if cursor.fetchone():
        cursor.execute(
            "UPDATE tube_analysis "
            "SET created_at = datetime(created_at, '+8 hours') "
            "WHERE created_at IS NOT NULL AND TRIM(CAST(created_at AS TEXT)) != ''"
        )
    cursor.execute(
        "INSERT OR REPLACE INTO pp_schema_meta (key, value) VALUES (?, '1')",
        (_CREATED_AT_LOCAL_META_KEY,),
    )
    conn.commit()
    logger.info("tube_analysis.created_at 已校正为北京时间")


def check_and_upgrade_database(db_path=DEFAULT_DB_PATH):
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tube_analysis'")
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(tube_analysis)")
            columns = cursor.fetchall()
            column_names = [column[1] for column in columns]
            required_columns = [
                ('file_hash', 'TEXT'), ('centerline_points', 'BLOB'),
                ('smoothed_centerline', 'BLOB'), ('centerline_length', 'REAL'),
                ('actual_z_length', 'REAL'), ('wall_thickness_data', 'BLOB'),
                ('centerline_perimeter_data', 'BLOB'), ('segments', 'BLOB'),
                ('volume', 'REAL'), ('processed_mesh', 'BLOB'),
                # New columns introduced for the rebuilt "数据库加载" flow.
                ('display_name', 'TEXT'),
                ('source_file_path', 'TEXT'),
                ('source_file_type', 'TEXT'),
                ('analysis_payload', 'BLOB'),
                ('sections_count', 'INTEGER'),
                ('algorithm', 'TEXT'),
                # 来源：'pp' = 「管件模型分析」页保存的分析；'entity' = 一体化流水线
                # 「导入主流程」写进来的测量结果（工艺优化页不展示这一类）。
                ('origin', "TEXT DEFAULT 'pp'"),
            ]
            for column_name, column_type in required_columns:
                if column_name not in column_names:
                    try:
                        cursor.execute(f"ALTER TABLE tube_analysis ADD COLUMN {column_name} {column_type}")
                        conn.commit()
                    except sqlite3.Error as e:
                        logger.error(f"添加{column_name}字段时出错: {e}")
            if 'created_at' not in column_names:
                try:
                    cursor.execute("ALTER TABLE tube_analysis ADD COLUMN created_at TIMESTAMP")
                    conn.commit()
                except sqlite3.Error:
                    pass

            # Older schemas declared ``file_name TEXT UNIQUE``, which made
            # saving the same model twice silently overwrite the previous
            # row. The new contract is: every explicit user "save" creates
            # a brand-new record. If the constraint is still around we
            # rebuild the table without it (preserving existing rows).
            try:
                cursor.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='tube_analysis'"
                )
                create_sql_row = cursor.fetchone()
                create_sql = (create_sql_row[0] if create_sql_row else '') or ''
                if 'file_name' in create_sql and 'UNIQUE' in create_sql.upper():
                    cursor.executescript(
                        """
                        BEGIN;
                        ALTER TABLE tube_analysis RENAME TO _tube_analysis_old;
                        CREATE TABLE tube_analysis (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            file_name TEXT,
                            file_hash TEXT,
                            centerline_points TEXT,
                            smoothed_centerline TEXT,
                            centerline_length REAL,
                            actual_z_length REAL,
                            wall_thickness_data TEXT,
                            centerline_perimeter_data TEXT,
                            segments TEXT,
                            volume REAL,
                            processed_mesh TEXT,
                            display_name TEXT,
                            source_file_path TEXT,
                            source_file_type TEXT,
                            analysis_payload BLOB,
                            sections_count INTEGER,
                            algorithm TEXT,
                            origin TEXT DEFAULT 'pp',
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        INSERT INTO tube_analysis (
                            id, file_name, file_hash, centerline_points,
                            smoothed_centerline, centerline_length, actual_z_length,
                            wall_thickness_data, centerline_perimeter_data, segments,
                            volume, processed_mesh, display_name, source_file_path,
                            source_file_type, analysis_payload, sections_count,
                            algorithm, origin, created_at
                        )
                        SELECT
                            id, file_name, file_hash, centerline_points,
                            smoothed_centerline, centerline_length, actual_z_length,
                            wall_thickness_data, centerline_perimeter_data, segments,
                            volume, processed_mesh, display_name, source_file_path,
                            source_file_type, analysis_payload, sections_count,
                            algorithm, 'pp', created_at
                        FROM _tube_analysis_old;
                        DROP TABLE _tube_analysis_old;
                        COMMIT;
                        """
                    )
                    logger.info("已移除 tube_analysis.file_name 的 UNIQUE 约束")
            except sqlite3.Error as exc:
                logger.error("移除 file_name UNIQUE 约束失败: %s", exc)

            conn.close()
    except Exception as e:
        logger.error(f"数据库升级失败: {e}")


def init_database(db_path=DEFAULT_DB_PATH):
    db_dir = os.path.dirname(db_path)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    # Run schema upgrade for older DBs *before* opening our own connection.
    try:
        check_and_upgrade_database(db_path)
    except Exception as _exc:  # pragma: no cover
        logger.warning("数据库升级检查失败: %s", _exc)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS tube_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT,
            file_hash TEXT,
            centerline_points TEXT,
            smoothed_centerline TEXT,
            centerline_length REAL,
            actual_z_length REAL,
            wall_thickness_data TEXT,
            centerline_perimeter_data TEXT,
            segments TEXT,
            volume REAL,
            processed_mesh TEXT,
            display_name TEXT,
            source_file_path TEXT,
            source_file_type TEXT,
            analysis_payload BLOB,
            sections_count INTEGER,
            algorithm TEXT,
            origin TEXT DEFAULT 'pp',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        conn.commit()
        _ensure_created_at_local(conn)
        return conn
    except Exception as e:
        logger.error(f"初始化数据库失败: {e}")
        raise


def get_file_hash(file_obj):
    try:
        if hasattr(file_obj, 'read') and hasattr(file_obj, 'seek') and hasattr(file_obj, 'tell'):
            current_pos = file_obj.tell()
            file_obj.seek(0)
            file_content = file_obj.read()
            file_obj.seek(current_pos)
        elif isinstance(file_obj, bytes):
            file_content = file_obj
        elif isinstance(file_obj, str) and os.path.exists(file_obj):
            with open(file_obj, 'rb') as f:
                file_content = f.read()
        else:
            return None
        return hashlib.sha256(file_content).hexdigest()
    except Exception as e:
        logger.error(f"计算文件哈希值失败: {e}")
        return None


def query_tube_data(file_name, file_obj=None, db_path=DEFAULT_DB_PATH):
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tube_analysis WHERE file_name = ?", (file_name,))
        record = cursor.fetchone()
        if record:
            if file_obj:
                file_hash = get_file_hash(file_obj)
                if file_hash and record[2] != file_hash:
                    conn.close()
                    return None
            result = {
                'file_name': record[1],
                'file_hash': record[2] if len(record) > 2 else '',
                'centerline_length': record[5] if len(record) > 5 else 0.0,
                'actual_z_length': record[6] if len(record) > 6 else (record[5] if len(record) > 5 else 0.0),
                'volume': record[10] if len(record) > 10 else 0.0,
            }
            def safe_deserialize(data, default_value=None):
                if data is None:
                    return default_value
                if isinstance(data, (int, float)):
                    return default_value
                try:
                    return pickle.loads(data)
                except Exception:
                    return default_value
            if len(record) > 3:
                result['centerline_points'] = safe_deserialize(record[3])
            if len(record) > 4:
                result['smoothed_centerline'] = safe_deserialize(record[4])
            if len(record) > 7:
                result['wall_thickness_data'] = safe_deserialize(record[7])
            if len(record) > 8:
                result['centerline_perimeter_data'] = safe_deserialize(record[8])
            if len(record) > 9:
                result['segments'] = safe_deserialize(record[9], {})
            if len(record) > 11:
                result['processed_mesh'] = safe_deserialize(record[11])
            conn.close()
            return result
        conn.close()
        return None
    except Exception as e:
        logger.error(f"查询管件数据失败: {e}")
        logger.error(traceback.format_exc())
        return None


def save_tube_data(file_name, file_obj, results, db_path=DEFAULT_DB_PATH):
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        file_hash = get_file_hash(file_obj) if file_obj else ''
        centerline_points = results.get('centerline_points')
        smoothed_centerline = results.get('smoothed_centerline')
        centerline_length = results.get('centerline_length', 0.0)
        actual_z_length = results.get('actual_z_length', 0.0)
        wall_thickness_data = results.get('wall_thickness_data')
        centerline_perimeter_data = results.get('centerline_perimeter_data')
        segments = results.get('segments', {})
        volume = results.get('volume', 0.0)
        processed_mesh = results.get('processed_mesh')

        def safe_serialize(data):
            if data is None:
                return None
            try:
                return pickle.dumps(data)
            except Exception:
                return None

        try:
            cursor.execute('''
                INSERT OR REPLACE INTO tube_analysis
                (file_name, file_hash, centerline_points, smoothed_centerline,
                 centerline_length, actual_z_length, wall_thickness_data,
                 centerline_perimeter_data, segments, volume, processed_mesh)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                file_name, file_hash,
                safe_serialize(centerline_points), safe_serialize(smoothed_centerline),
                centerline_length, actual_z_length,
                safe_serialize(wall_thickness_data), safe_serialize(centerline_perimeter_data),
                json.dumps(segments, ensure_ascii=False) if segments else None,
                volume, safe_serialize(processed_mesh)
            ))
            conn.commit()
            logger.info(f"管件数据已保存: {file_name}")
        except sqlite3.IntegrityError:
            logger.error(f"保存数据时发生冲突: {file_name}")
        conn.close()
        return True
    except Exception as e:
        logger.error(f"保存管件数据失败: {e}")
        logger.error(traceback.format_exc())
        return False


def delete_tube_data(tube_id, db_path=DEFAULT_DB_PATH):
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT file_name FROM tube_analysis WHERE id = ?", (tube_id,))
        record = cursor.fetchone()
        if record:
            cursor.execute("DELETE FROM tube_analysis WHERE id = ?", (tube_id,))
            conn.commit()
            logger.info(f"已删除管件数据: {record[0]} (ID: {tube_id})")
            conn.close()
            return True
        conn.close()
        return False
    except Exception as e:
        logger.error(f"删除管件数据失败: {e}")
        return False


def delete_multiple_tube_data(tube_ids, db_path=DEFAULT_DB_PATH):
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        deleted_count = 0
        paths_to_remove: list[str] = []
        for tube_id in tube_ids:
            cursor.execute(
                "SELECT source_file_path FROM tube_analysis WHERE id = ?", (tube_id,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                paths_to_remove.append(row[0])
            cursor.execute("DELETE FROM tube_analysis WHERE id = ?", (tube_id,))
            if cursor.rowcount > 0:
                deleted_count += 1
        conn.commit()
        conn.close()
        # Best-effort cleanup of saved source files
        for p in paths_to_remove:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        logger.info(f"批量删除完成: {deleted_count}/{len(tube_ids)} 条记录")
        return deleted_count
    except Exception as e:
        logger.error(f"批量删除管件数据失败: {e}")
        return 0


def get_tube_by_id(tube_id, db_path=DEFAULT_DB_PATH):
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tube_analysis WHERE id = ?", (tube_id,))
        record = cursor.fetchone()
        if not record:
            conn.close()
            return None
        result = {
            'id': record[0],
            'file_name': record[1],
            'file_hash': record[2] if len(record) > 2 else '',
            'centerline_length': record[5] if len(record) > 5 else 0.0,
            'actual_z_length': record[6] if len(record) > 6 else 0.0,
            'volume': record[10] if len(record) > 10 else 0.0,
        }
        def pickle_loads(data):
            """Deserialize pickled data back to original object."""
            if data is None:
                return None
            try:
                return pickle.loads(data)
            except Exception:
                return None

        if len(record) > 3:
            result['centerline_points'] = pickle_loads(record[3])
        if len(record) > 4:
            result['smoothed_centerline'] = pickle_loads(record[4])
        if len(record) > 7:
            result['wall_thickness_data'] = pickle_loads(record[7])
        if len(record) > 8:
            result['centerline_perimeter_data'] = pickle_loads(record[8])
        if len(record) > 9:
            result['segments'] = pickle_loads(record[9]) or {}
        if len(record) > 11:
            result['processed_mesh'] = pickle_loads(record[11])
        conn.close()
        return result
    except Exception as e:
        logger.error(f"查询管件数据失败: {e}")
        logger.error(traceback.format_exc())
        return None


def list_database_tubes(db_path=DEFAULT_DB_PATH, origin=None):
    """List saved tube analyses with display-friendly columns.

    Returns a DataFrame with columns: ID, 文件名, 显示名称,
    中心线长度(mm), 截面数, 算法, 文件类型, 来源, 创建时间.

    ``origin`` 过滤来源：``'pp'`` = 只在「管件模型分析」页保存的（工艺优化页要的就是这一类），
    ``'entity'`` = 一体化流水线「导入主流程」写进来的，``None`` = 全部（管件模型分析的历史列表）。
    """
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        where = ''
        params: tuple = ()
        if origin:
            where = "WHERE COALESCE(origin, 'pp') = ? "
            params = (str(origin),)
        cursor.execute(
            "SELECT id, file_name, COALESCE(display_name, file_name), "
            "COALESCE(centerline_length, 0), COALESCE(sections_count, 0), "
            "COALESCE(algorithm, ''), COALESCE(source_file_type, ''), "
            "COALESCE(origin, 'pp'), created_at "
            "FROM tube_analysis " + where + "ORDER BY created_at DESC",
            params,
        )
        rows = cursor.fetchall()
        import pandas as pd
        df = pd.DataFrame(
            rows,
            columns=[
                'ID', '文件名', '显示名称',
                '中心线长度(mm)', '截面数', '算法', '文件类型', '来源', '创建时间',
            ],
        )
        if not df.empty:
            df['创建时间'] = df['创建时间'].map(
                lambda v: '' if v is None else str(v).replace('T', ' ')[:19]
            )
        conn.close()
        return df
    except Exception as e:
        logger.error(f"查询数据库记录失败: {e}")
        import pandas as pd
        return pd.DataFrame()


def find_tubes_by_display_name(display_name, db_path=DEFAULT_DB_PATH):
    """按显示名称（件号）查记录，最新在前。

    一体化流水线「导入主流程」用它做幂等判定：数据库里已有同名记录就不再插入，
    历史遗留的重复行也能顺手清理。返回 ``[{'id', 'file_name', 'created_at',
    'centerline_length', 'sections_count'}, ...]``；出错返回空列表。
    """
    name = str(display_name or '').strip()
    if not name:
        return []
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, file_name, created_at, "
            "COALESCE(centerline_length, 0), COALESCE(sections_count, 0) "
            "FROM tube_analysis "
            "WHERE COALESCE(display_name, file_name) = ? "
            "ORDER BY id DESC",
            (name,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                'id': int(row[0]),
                'file_name': row[1],
                'created_at': row[2],
                'centerline_length': float(row[3] or 0.0),
                'sections_count': int(row[4] or 0),
            }
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.error("按显示名称查询管件失败: %s", exc)
        return []


def save_full_analysis(
    file_name: str,
    display_name: str,
    source_file_path: str,
    source_file_type: str,
    file_hash: str,
    payload: dict,
    db_path: str = DEFAULT_DB_PATH,
    origin: str = 'pp',
):
    """Persist a full analysis session payload.

    ``payload`` is the entire ``sess['results']`` dict produced by the
    analysis pipeline. The whole dict is pickled into ``analysis_payload``
    so the front-end can be restored bit-for-bit on load. The few flat
    columns we still write (centerline_length, sections_count, algorithm,
    …) are there to power the list view without unpickling each record.

    Returns a tuple ``(new_id, error_message)``:
      - ``(row_id, None)`` on success (row_id > 0)
      - ``(-1, str)`` on failure with a concise, user-facing error message
    """
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()

        try:
            blob = pickle.dumps(payload)
        except Exception as exc:
            logger.exception("序列化 payload 失败")
            return -1, f'序列化数据失败：{type(exc).__name__}: {exc}'

        centerline_length = float(
            payload.get('centerline_length')
            or payload.get('center_line_length')
            or (payload.get('center_line') or {}).get('total_length')
            or 0.0
        )
        actual_z_length = float(payload.get('actual_z_length', centerline_length))
        sections_count = int(len(payload.get('sections_full') or []))
        algorithm = str(payload.get('algorithm', '') or '')
        volume = float(payload.get('volume', 0.0))
        segments = payload.get('segments') or {}

        cursor.execute(
            '''
            INSERT INTO tube_analysis
                (file_name, file_hash, centerline_length, actual_z_length,
                 segments, volume, display_name, source_file_path,
                 source_file_type, analysis_payload, sections_count, algorithm,
                 origin, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                file_name, file_hash, centerline_length, actual_z_length,
                json.dumps(segments, ensure_ascii=False) if segments else None,
                volume, display_name, source_file_path, source_file_type,
                blob, sections_count, algorithm,
                str(origin or 'pp'), _now_local_sql(),
            ),
        )
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return int(new_id), None
    except sqlite3.OperationalError as exc:
        # 权限/只读/磁盘满等最常见错误，明确提示 db 路径。
        logger.exception("save_full_analysis SQLite operational error")
        return -1, (
            f'数据库无法写入：{exc}. 路径: {db_path}. '
            f'常见原因：安装目录只读、磁盘写保护、被杀软锁定。'
        )
    except Exception as exc:
        logger.exception("save_full_analysis 失败")
        return -1, f'{type(exc).__name__}: {exc}'


def get_full_analysis(tube_id: int, db_path: str = DEFAULT_DB_PATH) -> dict | None:
    """Return the full saved payload + metadata for a single record."""
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, file_name, COALESCE(display_name, file_name), "
            "source_file_path, source_file_type, analysis_payload, "
            "centerline_length, actual_z_length, volume, segments, created_at "
            "FROM tube_analysis WHERE id = ?",
            (tube_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None

        (id_, file_name, display_name, source_path, source_type, payload_blob,
         centerline_length, actual_z_length, volume, segments_json, created_at) = row

        results = None
        if payload_blob is not None:
            try:
                results = pickle.loads(payload_blob)
            except Exception as exc:
                logger.error("解 pickle payload 失败: %s", exc)
                results = None

        return {
            'id': int(id_),
            'file_name': file_name,
            'display_name': display_name,
            'source_file_path': source_path,
            'source_file_type': source_type,
            'centerline_length': centerline_length or 0.0,
            'actual_z_length': actual_z_length or 0.0,
            'volume': volume or 0.0,
            'segments': json.loads(segments_json) if segments_json else {},
            'created_at': created_at,
            'results': results,
        }
    except Exception as exc:
        logger.error("get_full_analysis 失败: %s", exc)
        return None


def acknowledge_review(tube_id: int, *, note: str = "", db_path: str = DEFAULT_DB_PATH) -> bool:
    """Mark a stored measurement's minor review flags as human-acknowledged.

    Only the ``analysis_quality`` block is touched; section records keep their
    measured values and their ``needs_review`` status so the UI can still show
    what was flagged. The TP/RFM gate treats ``review_acknowledged`` as an
    explicit operator override.
    """
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT analysis_payload FROM tube_analysis WHERE id = ?", (tube_id,))
        row = cursor.fetchone()
        if not row or row[0] is None:
            conn.close()
            return False
        payload = pickle.loads(row[0])
        quality = dict(payload.get('analysis_quality') or {})
        quality['review_acknowledged'] = True
        quality['review_acknowledged_at'] = _now_local_sql()
        if note:
            quality['review_acknowledged_note'] = str(note)[:500]
        payload['analysis_quality'] = quality
        cursor.execute(
            "UPDATE tube_analysis SET analysis_payload = ? WHERE id = ?",
            (pickle.dumps(payload), tube_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        logger.exception("acknowledge_review 失败 tube_id=%s", tube_id)
        return False


def rename_tube(tube_id: int, new_display_name: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Update the display_name column for a saved tube."""
    if not new_display_name:
        return False
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tube_analysis SET display_name = ? WHERE id = ?",
            (new_display_name.strip(), tube_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except Exception as exc:
        logger.error("rename_tube 失败: %s", exc)
        return False


def update_centerline_length(
    tube_id: int, length: float, db_path: str = DEFAULT_DB_PATH
) -> bool:
    """Backfill ``centerline_length`` when it was persisted as 0.

    老的入库链路（一体化流水线上传）只写了 ``center_line_length``，列表页
    因此显示「L 0.0 mm」。加载时算出了正确值就顺手写回，避免每次都要重算。
    """
    try:
        value = float(length or 0.0)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tube_analysis SET centerline_length = ? "
            "WHERE id = ? AND (centerline_length IS NULL OR centerline_length <= 0)",
            (value, tube_id),
        )
        ok = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except Exception as exc:
        logger.error("update_centerline_length 失败: %s", exc)
        return False


def get_source_file_path(tube_id: int, db_path: str = DEFAULT_DB_PATH) -> str | None:
    try:
        conn = init_database(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT source_file_path FROM tube_analysis WHERE id = ?", (tube_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None
