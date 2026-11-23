# 仅扩口
# 素材外径、扩口大径、素材壁厚直接赋值，不搜索、不反推芯轴。
import numpy as np
from typing import Dict, Any

from core.pp.process_length import require_process_length


class OPT_Kuo:
    """仅扩口：D0 = 直线段小径 D3，扩口大径 = D1，T0 = 传入壁厚最大值。"""

    def __init__(self, analysis_results: Dict[str, Any], d_precision=0, t_precision=1, l_precision=0, add3=0, L4=0,
                 weights=None, material_allowance=1.3):
        self.weights = dict(weights) if isinstance(weights, dict) else {}
        self.material_allowance = float(material_allowance)
        self.analysis_results = analysis_results
        self.df = analysis_results['wall_thickness_data']
        self.segments = analysis_results.get('segments') or {}

        required_columns = ['中心线长度(mm)', '壁厚(mm)', '外径周长(mm)']
        for col in required_columns:
            if col not in self.df.columns:
                raise ValueError(f"传入的壁厚数据缺少必要的列：{col}")

        self.df = self.df.sort_values(by='中心线长度(mm)').reset_index(drop=True)
        self.d_precision = d_precision
        self.t_precision = t_precision
        self.l_precision = l_precision
        self.add3 = add3
        self.L4 = float(L4 or 0)
        self.tube_volume = float(analysis_results.get('volume') or 0)

        self.D1 = 0.0
        self.D3 = 0.0
        self.D0 = 0.0
        self.T0 = 0.0
        self.L = 0.0
        self.L0 = 0.0
        self.L1 = 0.0
        self.Ln = 0.0

        self._extract_diameters()
        self._assign_fixed_values()
        self._calculate_total_length()
        self.L0 = round(self._calculate_material_length(self.D0, self.T0), self.l_precision)
        self.L1 = round(max(0.0, self.L - self.L4), self.l_precision)

    def _positive_walls(self):
        walls = []
        for key in ('T1', 'T2', 'T3', 'T4'):
            try:
                value = float(self.segments.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                walls.append(value)
        if walls:
            return walls
        if '壁厚(mm)' not in self.df.columns:
            return []
        for value in self.df['壁厚(mm)'].tolist():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                walls.append(number)
        return walls

    def _extract_diameters(self):
        if 'D1' in self.segments and 'D3' in self.segments:
            try:
                d1 = float(self.segments['D1'])
                d3 = float(self.segments['D3'])
            except (TypeError, ValueError):
                d1 = d3 = 0.0
            if d1 > 0 and d3 > 0:
                self.D1 = d1
                self.D3 = d3
                return

        if self.df is None or self.df.empty:
            raise ValueError("无法提取直径：壁厚数据为空。请确保管件分析成功完成。")
        if '外径周长(mm)' not in self.df.columns:
            raise ValueError("无法提取直径：数据中缺少'外径周长(mm)'列。")

        perimeters = self.df['外径周长(mm)'].values
        valid_perimeters = perimeters[perimeters > 0]
        if len(valid_perimeters) == 0:
            raise ValueError("无法提取直径：没有检测到有效的外径周长数据。")

        self.D3 = float(np.min(valid_perimeters) / np.pi)
        self.D1 = float(np.max(valid_perimeters) / np.pi)
        if self.D1 <= 0 or self.D3 <= 0 or np.isnan(self.D1) or np.isnan(self.D3):
            raise ValueError("无法提取有效的直径值。请检查外径周长数据是否正确。")

    def _assign_fixed_values(self):
        walls = self._positive_walls()
        if not walls:
            raise ValueError("所有壁厚值都无效（≤0），无法进行仅扩口。请检查输入的壁厚数据。")
        self.D3 = round(float(self.D3), self.d_precision)
        self.D1 = round(float(self.D1), self.d_precision)
        self.D0 = self.D3
        self.T0 = round(float(max(walls)), self.t_precision)
        self.Dn = self.D3

    def _calculate_total_length(self):
        self.L = require_process_length(self.analysis_results)

    def _calculate_material_length(self, D0, T0) -> float:
        material_cross_section_area = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)
        if material_cross_section_area <= 0:
            raise ValueError(
                f"素材截面积计算结果为零或负值: {material_cross_section_area}，无法计算素材长度。请检查D0={D0}和T0={T0}是否合理。")
        if self.tube_volume <= 0:
            raise ValueError(
                f"成品管件体积数据缺失或为零: {self.tube_volume}，无法计算素材长度。请确保成品管件分析完成且体积计算正确。")
        return float(self.tube_volume / material_cross_section_area * self.material_allowance)

    def _solution(self, index=0):
        return {
            'name': f"方案{index + 1}",
            'description': '仅扩口',
            'D0': self.D0,
            'T0': self.T0,
            'L0': self.L0,
            'Dn': round(self.D3, 2),
            'D1': round(self.D1, 2),
            'D2': 0,
            'D3': round(self.D3, 2),
            'L1': self.L1,
            'L2': 0,
            'L4': round(self.L4, 2),
            'Ln': 0,
            'T1': round(self.T0, 2),
            'fitness': 1.0,
            'area_change_rate': {},
        }

    def _calculate_fitness_change(self, individual: Dict[str, float]):
        """重算仍按固定规则写回，不采用手工改过的 D0、T0。"""
        return 1.0, self.D0, self.T0, self.L1, self.L0

    def brute_force_optimize(self):
        return [self._solution(0)]
