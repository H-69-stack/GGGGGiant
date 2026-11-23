"""Compute TP/Taper profile lines from centerline-perimeter data (ported from old program)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _normalize_centerline_df(centerline_perimeter_data) -> pd.DataFrame:
    if centerline_perimeter_data is None:
        return pd.DataFrame()
    if isinstance(centerline_perimeter_data, list):
        data = pd.DataFrame(centerline_perimeter_data)
    else:
        data = centerline_perimeter_data.copy()

    if data.empty:
        return data

    if '中心线位置(mm)' in data.columns and '中心线长度(mm)' not in data.columns:
        data = data.rename(columns={'中心线位置(mm)': '中心线长度(mm)'})
    if '截面周长(mm)' in data.columns and '外径周长(mm)' not in data.columns:
        data = data.rename(columns={'截面周长(mm)': '外径周长(mm)'})
    return data


def _param_value(tube_params, key: str, default: float = 0.0) -> float:
    if isinstance(tube_params, list):
        for row in tube_params:
            name = row.get('参数') or row.get('Parameter')
            if name == key:
                try:
                    return float(row.get('值') if row.get('值') is not None else row.get('Value') or default)
                except (TypeError, ValueError):
                    return default
    elif isinstance(tube_params, dict):
        try:
            return float(tube_params.get(key) or default)
        except (TypeError, ValueError):
            return default
    return default


def _find_closest_length(perimeter: float, data: pd.DataFrame) -> float:
    distances = np.abs(data['外径周长(mm)'] - perimeter)
    closest_index = int(np.argmin(distances))
    return float(data.iloc[closest_index]['中心线长度(mm)'])


def _end_diameter_spread(stations, diameters, start, end) -> float:
    samples = np.interp(np.linspace(start, end, 8), stations, diameters)
    return float(np.ptp(samples))


def _tp_search_window(stations, diameters, l4: float, t_lr: float) -> tuple[float, float]:
    """Exclude L4 straight (and optional large-end T_LR) before locating TP ends.

    L4 is clipped from the end whose diameter is more constant over that length.
    T_LR (LL1) is clipped from the large-diameter end when present.
    """
    s0, s1 = float(stations[0]), float(stations[-1])
    lo, hi = s0, s1
    if l4 > 0.5 and (s1 - s0) > l4 + 0.5:
        spread_start = _end_diameter_spread(stations, diameters, s0, s0 + l4)
        spread_end = _end_diameter_spread(stations, diameters, s1 - l4, s1)
        if spread_start <= spread_end:
            lo = s0 + l4
        else:
            hi = s1 - l4
    if t_lr > 0.5 and (hi - lo) > t_lr + 0.5:
        if float(diameters[0]) >= float(diameters[-1]):
            lo = max(lo, s0 + t_lr)
        else:
            hi = min(hi, s1 - t_lr)
    if hi - lo < 0.5:
        return s0, s1
    return lo, hi


def compute_tp_profile(centerline_perimeter_data, tube_params):
    """Locate the requested endpoint diameters without changing design dimensions."""
    data = _normalize_centerline_df(centerline_perimeter_data)
    try:
        from core.tp.mold_generator import select_tp_segment
        from core.rfm.processors import perimeter_series
        rows, stations, perimeter = perimeter_series(data.to_dict(orient='records'))
        stations += float(rows[0]['中心线长度(mm)'])
        diameters = perimeter / np.pi
        large = _param_value(tube_params, 'T_D')
        small = _param_value(tube_params, 'D')
        expected_length = _param_value(tube_params, 'T_L')
        l4 = _param_value(tube_params, 'L4')
        t_lr = _param_value(tube_params, 'T_LR')
        if not 0 < small < large:
            raise ValueError('TP 大小径必须满足 0 < D < T_D')
        lo, hi = _tp_search_window(stations, diameters, l4, t_lr)
        d_lo = float(np.interp(lo, stations, diameters))
        d_hi = float(np.interp(hi, stations, diameters))
        small_snap = lo if d_lo <= d_hi else hi
        large_snap = hi if d_lo <= d_hi else lo
        clipped = abs(hi - lo) < abs(float(stations[-1]) - float(stations[0])) - 0.5

        def candidates(target, snap=None):
            points = []
            for i in range(len(stations)-1):
                d0, d1 = diameters[i:i+2]
                if abs(d0-target) < 1e-7:
                    points.append(stations[i])
                if (d0-target)*(d1-target) < 0:
                    points.append(stations[i]+(target-d0)/(d1-d0)*(stations[i+1]-stations[i]))
            if abs(diameters[-1]-target) < 1e-7:
                points.append(stations[-1])
            points = [p for p in points if lo - 1e-6 <= p <= hi + 1e-6]
            if not points:
                inside = np.where((stations >= lo - 1e-6) & (stations <= hi + 1e-6))[0]
                pool = inside if inside.size else np.arange(len(stations))
                nearest = int(pool[int(np.argmin(abs(diameters[pool] - target)))])
                if abs(diameters[nearest]-target) <= .1:
                    points.append(stations[nearest])
                elif clipped and snap is not None:
                    points.append(snap)
                else:
                    raise ValueError('工艺大小径不在测量型线范围内，请确认工艺参数与管件阶段')
            return np.unique(points)

        pairs = [(a, b) for a in candidates(large, large_snap)
                 for b in candidates(small, small_snap) if abs(a-b) > .01]
        if not pairs:
            raise ValueError('无法定位非零长度的 TP 段')
        if expected_length > 0:
            start, end = min(pairs, key=lambda ab: abs(abs(ab[1]-ab[0])-expected_length))
            if abs(abs(end-start)-expected_length) > max(2., expected_length*.02):
                raise ValueError('型线定位的 TP 段长与工艺 T_L 不一致，请确认端点或工艺参数')
        else:
            start, end = min(pairs, key=lambda ab: (min(ab), abs(ab[1]-ab[0])))
        segment = select_tp_segment(rows, start, end, large, small)
        length = float(abs(end-start))
        profile = sorted([{'X': length+9-row['中心线长度(mm)'], 'Y': row['外径周长(mm)']/np.pi}
                          for row in segment], key=lambda row: row['X'])
        return {'success': True, 'profile_data': profile, 'T_D_length': float(start),
                'D_length': float(end), 'T_D_diam': large, 'D_diam': small,
                'T_L': length, 'TP_segment_length': length, 'TP_segment_source': 'design_endpoints'}
    except (ValueError, KeyError, TypeError) as exc:
        return {'success': False, 'message': str(exc)}


def compute_taper_profile(centerline_perimeter_data, tube_params):
    """Return Taper line_data (X/Y) and endpoint lengths."""
    data = _normalize_centerline_df(centerline_perimeter_data)
    if data.empty or '中心线长度(mm)' not in data.columns or '外径周长(mm)' not in data.columns:
        return {'success': False, 'message': '中心线周长数据无效'}

    try:
        b_d = _param_value(tube_params, 'B_D')
        m_d = _param_value(tube_params, 'm_D')
        b_d_perimeter = b_d * np.pi
        m_d_perimeter = m_d * np.pi

        b_d_length = _find_closest_length(b_d_perimeter, data)
        m_d_length = _find_closest_length(m_d_perimeter, data)

        filtered = data[
            (data['外径周长(mm)'] >= min(b_d_perimeter, m_d_perimeter))
            & (data['外径周长(mm)'] <= max(b_d_perimeter, m_d_perimeter))
        ].copy()
        filtered.loc[:, '直径(mm)'] = filtered.loc[:, '外径周长(mm)'] / np.pi

        taper_result = filtered[['中心线长度(mm)', '直径(mm)']].copy()
        taper_result = taper_result.sort_values(by='中心线长度(mm)').reset_index(drop=True)
        min_length = taper_result['中心线长度(mm)'].min()
        taper_result['中心线长度(mm)'] = taper_result['中心线长度(mm)'] - min_length
        taper_result = taper_result.drop_duplicates(subset=['直径(mm)'], keep='first')
        taper_result = taper_result.rename(columns={'中心线长度(mm)': 'X', '直径(mm)': 'Y'})
        taper_result = taper_result.round({'X': 2, 'Y': 3}).sort_values(by='X').reset_index(drop=True)

        return {
            'success': True,
            'line_data': taper_result.to_dict(orient='records'),
            'm_D_length': m_d_length,
            'B_D_length': b_d_length,
        }
    except Exception as exc:
        return {'success': False, 'message': str(exc)}
