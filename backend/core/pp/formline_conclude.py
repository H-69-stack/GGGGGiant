#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
形线分析模块
用于分析中心线长度-周长数据中的直线段
"""

import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class FormlineAnalyzer:
    """形线分析器，用于识别中心线长度-周长数据中的直线段（简化版）"""
    
    def __init__(self, tolerance=2.5, min_segment_length=20.0, trend_tolerance=0.01, min_point_count=100):
        self.tolerance = tolerance
        self.min_segment_length = min_segment_length
        self.trend_tolerance = trend_tolerance
        self.min_point_count = min_point_count
        
    def analyze_straight_segments(self, data):
        if data is None or data.empty:
            return []
        required_columns = ['中心线长度(mm)', '外径周长(mm)']
        for col in required_columns:
            if col not in data.columns:
                return []
        data_sorted = data.sort_values('中心线长度(mm)').reset_index(drop=True)
        if len(data_sorted) < 20:
            return []
        window_size = 30
        perimeters = data_sorted['外径周长(mm)'].values
        is_stable = np.zeros(len(data_sorted), dtype=bool)
        for i in range(len(data_sorted)):
            start = max(0, i - window_size // 2)
            end = min(len(data_sorted), i + window_size // 2)
            window_perimeters = perimeters[start:end]
            std = np.std(window_perimeters)
            ptp = np.ptp(window_perimeters)
            if std <= self.tolerance and ptp <= self.tolerance:
                is_stable[i] = True
        segments = []
        in_segment = False
        segment_start = 0
        for i in range(len(is_stable)):
            if is_stable[i] and not in_segment:
                segment_start = i
                in_segment = True
            elif not is_stable[i] and in_segment:
                segment_end = i - 1
                segments.append((segment_start, segment_end))
                in_segment = False
        if in_segment:
            segments.append((segment_start, len(is_stable) - 1))
        straight_segments = []
        for start_idx, end_idx in segments:
            start_length = data_sorted.iloc[start_idx]['中心线长度(mm)']
            end_length = data_sorted.iloc[end_idx]['中心线长度(mm)']
            length = end_length - start_length
            if length >= self.min_segment_length:
                segment_data = data_sorted.iloc[start_idx:end_idx+1]
                segment_perimeters = segment_data['外径周长(mm)'].values
                avg_perimeter = segment_perimeters.mean()
                std_perimeter = segment_perimeters.std()
                min_perimeter = segment_perimeters.min()
                max_perimeter = segment_perimeters.max()
                ptp_perimeter = max_perimeter - min_perimeter
                if ptp_perimeter <= self.tolerance:
                    straight_segments.append({
                        'start_index': start_idx, 'end_index': end_idx,
                        'start_length': start_length, 'end_length': end_length,
                        'length': length, 'perimeter': avg_perimeter,
                        'perimeter_std': std_perimeter, 'perimeter_min': min_perimeter,
                        'perimeter_max': max_perimeter, 'perimeter_range': ptp_perimeter,
                        'point_count': end_idx - start_idx + 1
                    })
        return straight_segments
    
    def get_analysis_summary(self, straight_segments, data_info=None):
        if not straight_segments:
            return {
                'has_segments': False, 'message': '未发现符合条件的直线段',
                'definition': f'直线段定义：周长变化 ≤ ±{self.tolerance} mm，长度 ≥ {self.min_segment_length} mm，数据点数 ≥ {self.min_point_count} 个',
                'segment_count': 0, 'segments': [], 'statistics': {}, 'data_info': data_info or {}
            }
        total_length = sum(seg['length'] for seg in straight_segments)
        statistics = {
            'total_length': total_length,
            'average_length': total_length / len(straight_segments),
            'max_length': max(seg['length'] for seg in straight_segments),
            'min_length': min(seg['length'] for seg in straight_segments),
            'segment_count': len(straight_segments)
        }
        return {
            'has_segments': True, 'message': f'发现 {len(straight_segments)} 个直线段',
            'definition': f'直线段定义：周长变化 ≤ ±{self.tolerance} mm，长度 ≥ {self.min_segment_length} mm，数据点数 ≥ {self.min_point_count} 个',
            'segment_count': len(straight_segments), 'segments': straight_segments,
            'statistics': statistics, 'data_info': data_info or {}
        }


def analyze_formline_from_data(centerline_perimeter_records, tolerance=2.5,
                                min_segment_length=20.0, trend_tolerance=0.01,
                                min_point_count=100):
    """
    从 list[dict] 或 DataFrame 数据中分析直线段（Flask 后端入口）
    """
    if not centerline_perimeter_records:
        return {
            'success': False, 'error_message': '数据为空',
            'has_segments': False, 'segment_count': 0, 'segments': [],
            'statistics': {}, 'data_info': {}
        }
    if isinstance(centerline_perimeter_records, list):
        data = pd.DataFrame(centerline_perimeter_records)
    else:
        data = centerline_perimeter_records
    if data.empty or '中心线长度(mm)' not in data.columns or '外径周长(mm)' not in data.columns:
        return {
            'success': False, 'error_message': '数据缺少必要列',
            'has_segments': False, 'segment_count': 0, 'segments': [],
            'statistics': {}, 'data_info': {}
        }
    data_info = {
        'point_count': len(data),
        'length_range': {'min': float(data['中心线长度(mm)'].min()), 'max': float(data['中心线长度(mm)'].max())},
        'perimeter_range': {'min': float(data['外径周长(mm)'].min()), 'max': float(data['外径周长(mm)'].max())}
    }
    analyzer = FormlineAnalyzer(tolerance=tolerance, min_segment_length=min_segment_length,
                                trend_tolerance=trend_tolerance, min_point_count=min_point_count)
    straight_segments = analyzer.analyze_straight_segments(data)
    analysis_summary = analyzer.get_analysis_summary(straight_segments, data_info)
    analysis_summary['success'] = True
    return analysis_summary


def analyze_formline_for_streamlit(tolerance=2.5, min_segment_length=20.0, trend_tolerance=0.01, min_point_count=100):
    """Streamlit 兼容壳：从 st.session_state 取数据，内部调用 analyze_formline_from_data"""
    try:
        import streamlit as st
        if 'analysis_results' not in st.session_state:
            return {'success': False, 'error_message': '无数据', 'has_segments': False, 'segment_count': 0, 'segments': [], 'statistics': {}, 'data_info': {}}
        analysis_results = st.session_state.analysis_results
        if analysis_results is None or 'wall_thickness_data' not in analysis_results:
            return {'success': False, 'error_message': '无数据', 'has_segments': False, 'segment_count': 0, 'segments': [], 'statistics': {}, 'data_info': {}}
        data = analysis_results['wall_thickness_data']
        if data is None or data.empty:
            return {'success': False, 'error_message': '无数据', 'has_segments': False, 'segment_count': 0, 'segments': [], 'statistics': {}, 'data_info': {}}
        return analyze_formline_from_data(data, tolerance, min_segment_length, trend_tolerance, min_point_count)
    except ImportError:
        return {'success': False, 'error_message': '不在 Streamlit 环境中', 'has_segments': False, 'segment_count': 0, 'segments': [], 'statistics': {}, 'data_info': {}}
