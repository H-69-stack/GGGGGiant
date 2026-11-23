"""Helpers for normalizing parameter tables for API/frontend display."""
from __future__ import annotations


def normalize_params_table(records) -> list[dict]:
    """Normalize Parameter/Value rows to 参数/值."""
    if not records:
        return []
    normalized = []
    for row in records:
        if not isinstance(row, dict):
            continue
        key = row.get('参数')
        if key is None:
            key = row.get('Parameter', '')
        value = row.get('值')
        if value is None:
            value = row.get('Value', '')
        item = {'参数': key, '值': str(value)}
        if '描述' in row:
            item['描述'] = row['描述']
        if '计算方法' in row:
            item['计算方法'] = row['计算方法']
        normalized.append(item)
    return normalized


def _extract_image_key(records) -> str:
    """Extract image key (图片号前缀) from mold records.

    Mirrors the old program:
      mold_params.loc[mold_params['参数'] == '图号', '值'].iloc[0][:-4]
      -> for 'DC0128-ADIE0001' gives 'DC0128-ADIE'
    """
    if not records:
        return ''
    # First: look for Parameter=图号 or 参数=图号
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        key = rec.get('Parameter') or rec.get('参数') or ''
        if key == '图号':
            val = rec.get('Value') or rec.get('值') or ''
            # Old program: str(val)[:-4] -> removes the 4-digit serial suffix
            # e.g. 'DC0128-ADIE0001'[:-4] = 'DC0128-ADIE'
            return str(val)[:-4] if val else ''
    return ''


def _lookup_desc(maps: dict | None, image_key: str, param_key: str) -> str:
    """Look up description for a parameter key using image_key (e.g., 'DC0124-DIEO')."""
    if not maps or not image_key:
        return ''
    # Try exact key first (e.g., 'DC0124-DIEO', 'DC0128-ADIE', 'EC0120-B000')
    bucket = maps.get(image_key)
    if bucket and isinstance(bucket, dict):
        return str(bucket.get(param_key, ''))
    return ''


def _lookup_desc_fallback(maps: dict | None, short_code: str, param_key: str) -> str:
    """Fall back to searching all keys that contain short_code (e.g., 'ADIE')."""
    if not maps or not short_code:
        return ''
    for key, bucket in (maps or {}).items():
        if isinstance(bucket, dict) and short_code in key:
            val = bucket.get(param_key)
            if val:
                return str(val)
    return ''


def format_mold_summary(summary: dict, param_description: dict | None = None,
                        param_calc_method: dict | None = None) -> dict:
    """Convert mold summary records to 参数/值/描述 display rows.

    Image key follows the old program format: '{machine}-{short_code}' like 'DC0124-DIEO'.
    The description JSON keys use this format for per-mold entries.
    """
    formatted = {}
    for mold_name, records in (summary or {}).items():
        rows = []
        # Extract image key once per mold (e.g., 'DC0124-DIEO')
        image_key = _extract_image_key(records)
        # Short code = part after '-', e.g. 'DIEO' from 'DC0124-DIEO'
        short_code = image_key.split('-')[-1] if image_key else ''

        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            key = rec.get('Parameter') or rec.get('参数') or ''
            val = rec.get('Value') if 'Value' in rec else (rec.get('值') if '值' in rec else '')
            # Build display row
            row = {
                '参数': key,
                '值': str(val) if val is not None else '',
                '描述': '',
                '计算方法': '',
            }
            # Look up descriptions using exact image_key, then short_code fallback
            if image_key or short_code:
                row['描述'] = _lookup_desc(param_description, image_key, key)
                if not row['描述'] and short_code:
                    row['描述'] = _lookup_desc_fallback(param_description, short_code, key)
                row['计算方法'] = _lookup_desc(param_calc_method, image_key, key)
                if not row['计算方法'] and short_code:
                    row['计算方法'] = _lookup_desc_fallback(param_calc_method, short_code, key)
            rows.append(row)

        formatted[mold_name] = rows
    return formatted
