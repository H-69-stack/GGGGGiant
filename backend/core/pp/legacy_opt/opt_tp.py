# 仅tp抽，根据需要判断是否需要增加普通抽
# 2026-07-14 增加T0搜索范围,增加T0搜索步长为0.1
import numpy as np
import random
from typing import Dict, Any, List
import time
import math
from core.pp.process_length import require_process_length
from core.pp.mandrel_d0 import MANDREL_INFEASIBLE_PENALTY, assign_draw_blank_d0
#7.14仅taper抽，因为只能使外径变小，所以D0=D1，不用求解D0，仅求解适应度
#7.20判断是否需要增加普通抽，普通抽改变外径和壁厚

class OPT_TP:

    def __init__(self, analysis_results: Dict[str, Any], d_precision=0, t_precision=1, l_precision=0,L4 = 500,
                 weights=None, material_allowance=1.3, add3=0):
        self.weights = dict(weights) if isinstance(weights, dict) else {}
        self.material_allowance = float(material_allowance)
        self.add3 = add3
        """
        初始化工艺参数优化器
        参数:
            analysis_results: 管件分析结果字典，包含壁厚数据、分段信息等
        """
        self.analysis_results = analysis_results
        self.df = analysis_results['wall_thickness_data']
        self.segments = analysis_results['segments']

        # 检查df是否包含必要的列
        required_columns = ['中心线长度(mm)', '壁厚(mm)', '外径周长(mm)']
        for col in required_columns:
            if col not in self.df.columns:
                raise ValueError(f"传入的壁厚数据缺少必要的列：{col}")

        # 确保数据按中心线长度排序
        self.df = self.df.sort_values(by='中心线长度(mm)').reset_index(drop=True)

        # 从外径周长计算外径
        self.dD = self.df['外径周长(mm)'] / np.pi

        # 提取已知参数
        self.D1 = 0  # 最大直径(扩口大径)
        self.D2 = 0  # 素材直径
        self.D3 = 0  # 最小直径
        self.L4 = L4  # 直线段长度
        self.L = 0  # 成品管件总长度
        self.Ln = 0 #普通抽长度
        self.wall = [self.segments.get('T1', 0), self.segments.get('T2', 0), self.segments.get('T3', 0),
                     self.segments.get('T4', 0)]
        self.T0 = 0
        
        # 检查是否有有效的壁厚数据
        valid_wall_values = [x for x in self.wall if x > 0]
        if not valid_wall_values:
            raise ValueError("所有壁厚值都无效（≤0），无法进行工艺优化。请检查输入的壁厚数据。")
        
        self.T1 = max(self.wall)  # 最大壁厚
        self.T2 = min(valid_wall_values)  # 最小壁厚
        self.T3 = 0  # 中间壁厚
        for i in self.wall:
            if i < self.T1 and i > self.T2:
                self.T3 = i
            elif self.T1 == self.T2:
                self.T3 = self.T1
        if not self.T3:
            self.T3 = self.T2

        self.d_precision = d_precision
        self.t_precision = t_precision
        self.l_precision = l_precision


        # 获取管件体积
        self.tube_volume = analysis_results.get('volume', 0)  # 获取成品管件实际体积 (mm³)

        # 从数据中提取直径信息
        self._extract_diameters()

        # 计算总长度
        self._calculate_total_length()

        # 优化求解结果
        self.best_solution = None
        self.optimization_results = None

        # 初始化截面积变化率相关属性
        self.area_change_radio = 0.0  # 截面积变化率

    def _extract_diameters(self):
        """从分析结果中提取直径信息"""
        # 首先尝试从segments字典中直接获取D1和D3
        if 'D1' in self.segments and 'D3' in self.segments and self.segments['D1'] > 0 and self.segments['D3'] > 0:
            self.D1 = self.segments['D1']
            self.D3 = self.segments['D3']
            return

        # 检查数据框是否存在
        if self.df is None or self.df.empty:
            raise ValueError("无法提取直径：壁厚数据为空。请确保管件分析成功完成。")

        # 检查是否存在外径周长列
        if '外径周长(mm)' not in self.df.columns:
            raise ValueError("无法提取直径：数据中缺少'外径周长(mm)'列。请检查分析过程是否正确计算了外径数据。")

        # 提取周长数据
        perimeters = self.df['外径周长(mm)'].values
        valid_perimeters = perimeters[perimeters > 0]

        # 检查是否有有效的周长数据
        if len(valid_perimeters) == 0:
            raise ValueError("无法提取直径：没有检测到有效的外径周长数据（所有值均为0或负值）。请调整分析参数后重新分析。")

        # 计算最小和最大周长，并转换为直径
        min_perimeter = np.min(valid_perimeters)
        max_perimeter = np.max(valid_perimeters)

        # 周长 = π * 直径，所以直径 = 周长 / π
        self.D3 = round(min_perimeter / np.pi, 2)  # 最小直径
        self.D1 = round(max_perimeter / np.pi, 2)  # 最大直径

        # 检查计算出的直径是否有效
        if self.D1 <= 0 or self.D3 <= 0 or np.isnan(self.D1) or np.isnan(self.D3):
            raise ValueError("无法提取有效的直径值：计算结果无效或为零。请检查外径周长数据是否正确。")

    def _calculate_total_length(self):
        """计算管件总长度：修正后的中心线/图纸长度，不用 actual_z_length。"""
        self.L = require_process_length(self.analysis_results)



    def _calculate_material_length(self, D0, T0) -> float:
        """计算素材长度，考虑加工余量"""
        material_cross_section_area = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # mm²
        if material_cross_section_area <= 0:
            raise ValueError(
                f"素材截面积计算结果为零或负值: {material_cross_section_area}，无法计算素材长度。请检查D0={D0}和T0={T0}是否合理。")
        if self.tube_volume <= 0:
            raise ValueError(
                f"成品管件体积数据缺失或为零: {self.tube_volume}，无法计算素材长度。请确保成品管件分析完成且体积计算正确。")
        material_theoretical_length = self.tube_volume / material_cross_section_area * self.material_allowance  # 加工余量
        L0 = material_theoretical_length

        return float(L0)

    def _calculate_Ln_length(self, D0, T0, L0, T2, Dn, T1, L1) -> float:
        """裁剪前普通抽长度：素材体积减去扩口体积后，除以普通抽截面积，再加回扩口段长度。"""
        material_cross_section_area = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)
        if material_cross_section_area <= 0:
            raise ValueError(
                f"素材截面积计算结果为零或负值: {material_cross_section_area}，无法计算素材长度。请检查D0={D0}和T0={T0}是否合理。")
        Ln_cross_section_area = np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - T2) ** 2)
        flare_length = L1 + self.add3
        V_kuo = flare_length * np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - T1) ** 2)
        Ln = round((L0 * material_cross_section_area - V_kuo) / Ln_cross_section_area + flare_length, self.l_precision)
        return float(Ln)

    def calculate_new_length(
            self,
            original_outer_diameter: float,  # 原外径 (mm)
            original_wall_thickness: float,  # 原壁厚 (mm)
            original_length: float,  # 原长度 (mm)
            new_outer_diameter: float,  # 修改后外径 (mm)
            new_wall_thickness: float  # 修改后壁厚 (mm)
    ) -> float:

        # 1. 输入参数校验（避免计算错误/除以0）
        param_checks = [
            (original_outer_diameter <= 0, "原外径必须大于0"),
            (original_wall_thickness <= 0, "原壁厚必须大于0"),
            (original_length <= 0, "原长度必须大于0"),
            (new_outer_diameter <= 0, "修改后外径必须大于0"),
            (new_wall_thickness <= 0, "修改后壁厚必须大于0"),
            (original_outer_diameter < 2 * original_wall_thickness, "原外径必须≥2倍原壁厚（内径不能为负）"),
            (new_outer_diameter < 2 * new_wall_thickness, "修改后外径必须≥2倍修改后壁厚（内径不能为负）")
        ]

        for condition, msg in param_checks:
            if condition:
                raise ValueError(f"参数错误：{msg}")

        # 2. 计算原始截面面积
        r_outer_original = original_outer_diameter / 2  # 原外半径
        r_inner_original = (original_outer_diameter - 2 * original_wall_thickness) / 2  # 原内半径
        area_original = math.pi * (r_outer_original ** 2 - r_inner_original ** 2)  # 原截面面积

        # 3. 计算修改后截面面积
        r_outer_new = new_outer_diameter / 2  # 新外半径
        r_inner_new = (new_outer_diameter - 2 * new_wall_thickness) / 2  # 新内半径
        area_new = math.pi * (r_outer_new ** 2 - r_inner_new ** 2)  # 新截面面积

        # 4. 体积守恒计算新长度（避免除以0）
        if area_new == 0:
            raise ValueError("修改后截面面积为0，无法计算长度")

        new_length = original_length * (area_original / area_new)

        return round(new_length, 2)


    def _calculate_fitness(self, individual: Dict[str, float]) -> float:
        """计算个体适应度（工艺优化度TP）"""
        D0 = round(individual['D0'], self.d_precision)
        T0 = round(individual['T0'], self.t_precision)
        self.D2 = self.D3
        L1, L2 = 0, 0
        self.L1 = L1
        L4 = self.L4  # 获取直线段长度
        L2 = max(0,self.L - L4)
        self.L2 = L2
        #print(self.L,self.L1,self.L2,self.L4)
        # 确保L2 + L4  = L

        # 计算L0（素材长度）
        L0 = round(self._calculate_material_length(D0, T0), self.l_precision)
        self.L0 = L0

        # 适应度分数初始化为0
        fitness = 0
        max_up = 2
        max_penalty = 3  # 最大惩罚系数

        # 各子项的权重 - 默认总和为1（"仅TP抽"工艺只有 1 项，原默认 1.0）
        tp_extract_weight = float(self.weights.get('tp_extract', 1))

        self.area_change_ratio1 = 0  # 初始化截面积变化率
        self.area_change_ratio2 = 0


        # 1. 计算TP抽
        if L2 > 0 and D0 > self.D3:
            area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # 素材初始截面积
            TT2 = self.T3
            area_2 = np.pi * ((self.D3 / 2) ** 2 - ((self.D3 / 2) - TT2) ** 2)  # TP抽段截面积
            self.area_change_ratio2 = (area_0 - area_2) / area_0
            fitness_tp = 0
            min_boundary = 0.2
            max_boundary = 0.5  # 最大边界值50%
            if min_boundary <= self.area_change_ratio2 <= max_boundary:
                # PT抽优化系数TPCYHXS = (50%/截面积变化率) * 0.25
                TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio2) * tp_extract_weight)
                fitness_tp += TPCYHXS
            elif self.area_change_ratio2 < min_boundary:
                penalty_factor = (min_boundary - self.area_change_ratio2) / min_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_tp += - penalty
            elif self.area_change_ratio2 > max_boundary:
                penalty_factor = (self.area_change_ratio2 - max_boundary) / max_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_tp += - penalty
            fitness += fitness_tp


        # 惩罚项：工艺参数超出合理范围
        penalty = 0

        # 4. D0 < D1, D0 > D3，降低惩罚力度
        if  D0 <= self.D3:
            fitness -= max_penalty
            # print('不满足素材直径约束')

        if  L2 > 150:
            fitness -= max_penalty

        # 5. MAX(T1,T2,T3) <= T0 <= 2*MIN(T1,T2,T3)，降低惩罚力度
        if  T0 > 2 * self.T2:
            fitness -= max_penalty
            print('不满足壁厚约束')

        return fitness



    #使用普通抽将壁厚抽到T3
    def _calculate_fitness2(self, individual: Dict[str, float]) -> float:
        """计算个体适应度（工艺优化度TP）"""
        D0 = round(individual['D0'], self.d_precision)
        T0 = round(individual['T0'], self.t_precision)
        Dn = self.D1
        self.Dn = Dn
        self.T0 = T0
        self.D2 = self.D3
        L1, L2 = 0, 0
        self.L1 = L1
        L4 = self.L4  # 获取直线段长度
        L2 = max(0, self.L - L4)
        self.L2 = L2
        adjusted_d0 = assign_draw_blank_d0(
            self, individual, '普通抽+TP抽', Dn, T0,
            d2=self.D2, l1=L1, l2=L2, max_d0=150, provisional_d0=D0,
        )
        mandrel_ok = adjusted_d0 is not None
        if mandrel_ok:
            D0 = adjusted_d0
        elif D0 <= 2 * T0:
            D0 = round(2 * T0 + 1, self.d_precision)
        # 计算L0（素材长度）
        L0 = round(self._calculate_material_length(D0, T0), self.l_precision)
        self.Ln = self._calculate_Ln_length(D0, T0, L0, self.T2, self.Dn, self.T1, L1)
        self.L0 = L0

        # 适应度分数初始化为0
        fitness = 0
        max_up = 2
        max_penalty = 3  # 最大惩罚系数


        # 各子项的权重 - 默认各 0.5（"普通抽+TP抽"工艺的 2 项）
        pt_extract_weight = float(self.weights.get('pt_extract', 1/2))
        tp_extract_weight = float(self.weights.get('tp_extract', 1/2))

        self.area_change_ratio1 = 0  # 初始化截面积变化率
        self.area_change_ratio2 = 0  # 初始化截面积变化率
        self.area_change_ratio3 = 0  # 初始化截面积变化率
        self.area_change_ratio4 = 0  # 初始化截面积变化率

        #TP抽优化度
        if L2 > 0 and D0 > self.D3:
            area_0 = np.pi * ((self.Dn / 2) ** 2 - ((self.Dn / 2) - self.T3) ** 2)  # 素材初始截面积
            TT2 = self.T2
            area_2 = np.pi * ((self.D3 / 2) ** 2 - ((self.D3 / 2) - TT2) ** 2)  # TP抽段截面积
            self.area_change_ratio4 = (area_0 - area_2) / area_0
            #print(self.area_change_ratio2)
            fitness_tp = 0
            min_boundary = 0.2
            max_boundary = 0.5  # 最大边界值50%
            if min_boundary <= self.area_change_ratio4 <= max_boundary:
                TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio4) * tp_extract_weight)
                fitness_tp += TPCYHXS
            elif self.area_change_ratio4 < min_boundary:
                penalty_factor = (min_boundary - self.area_change_ratio4) / min_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_tp += - penalty
            elif self.area_change_ratio4 > max_boundary:
                penalty_factor = (self.area_change_ratio4 - max_boundary) / max_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_tp += - penalty
            fitness += fitness_tp

        if L2 > 0 and D0 > self.Dn:
            area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # 素材初始截面积
            area_2 = np.pi * ((self.Dn/2)**2 - ((self.Dn/2) - self.T1)**2)  # pt抽段初始截面积
            area_3 = np.pi * ((self.Dn/2)**2 - ((self.Dn/2) - self.T2)**2)  # pt抽段初始截面积
            area_4 = np.pi * ((self.Dn/2)**2 - ((self.Dn/2) - self.T3)**2)  # pt抽段初始截面积
            self.area_change_ratio1 = (area_0 - area_2) / area_0
            self.area_change_ratio2 = (area_0 - area_3) / area_0
            self.area_change_ratio3 = (area_0 - area_4) / area_0
            fitness_pt1 = 0
            fitness_pt2 = 0
            fitness_pt3 = 0
            min_boundary = 0.2
            max_boundary = 0.5
            if min_boundary <= self.area_change_ratio1 <= max_boundary:
                TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio1) * pt_extract_weight)
                fitness_pt1 += TPCYHXS
            elif self.area_change_ratio1 < min_boundary:
                penalty_factor = (min_boundary - self.area_change_ratio1) / min_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt1 += - penalty
            elif self.area_change_ratio1 > max_boundary:
                penalty_factor = (self.area_change_ratio1 - max_boundary) / max_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt1 += - penalty

            if min_boundary <= self.area_change_ratio2 <= max_boundary:
                TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio2) * pt_extract_weight)
                fitness_pt2 += TPCYHXS
            elif self.area_change_ratio2 < min_boundary:
                penalty_factor = (min_boundary - self.area_change_ratio2) / min_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt2 += - penalty
            elif self.area_change_ratio2 > max_boundary:
                penalty_factor = (self.area_change_ratio2 - max_boundary) / max_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt2 += - penalty

            if min_boundary <= self.area_change_ratio3 <= max_boundary:
                TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio3) * pt_extract_weight)
                fitness_pt3 += TPCYHXS
            elif self.area_change_ratio3 < min_boundary:
                penalty_factor = (min_boundary - self.area_change_ratio3) / min_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt3 += - penalty
            elif self.area_change_ratio3 > max_boundary:
                penalty_factor = (self.area_change_ratio3 - max_boundary) / max_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt3 += - penalty

            fitness += (fitness_pt1/3 + fitness_pt2/3 + fitness_pt3/3)

        # 惩罚项：工艺参数超出合理范围
        penalty = 0

        # 4. D0 < D1, D0 > D3，降低惩罚力度
        if D0 <= self.D3:
            fitness -= max_penalty
            # print('不满足素材直径约束')

        if  L2 > 150:
            fitness -= max_penalty

        # 5. MAX(T1,T2,T3) <= T0 <= 2*MIN(T1,T2,T3)，降低惩罚力度
        if T0 > 2 * self.T2:
            fitness -= max_penalty
            # print(T0,self.T1,self.T2)
            #print('不满足壁厚约束')

        if not mandrel_ok:
            fitness -= MANDREL_INFEASIBLE_PENALTY

        return fitness



    def _calculate_fitness_change(self, individual: Dict[str, float]) -> float:
        """计算个体适应度变化（用于人工更改参数后适应度计算）"""
        return self._calculate_fitness(individual), self.L1, self.L2,self.L0,self.area_change_ratio2

    def _calculate_fitness_change2(self, individual: Dict[str, float]) -> float:
        """计算个体适应度变化（用于人工更改参数后适应度计算）"""
        return self._calculate_fitness2(individual), self.T0,self.Ln,self.L1,self.L2,self.L0, self.area_change_ratio1,self.area_change_ratio2,self.area_change_ratio3,self.area_change_ratio4


    def _get_solution_description(self, index, fitness):
        """根据方案索引和适应度生成描述"""
        descriptions = [
            "方案一",  # 对应索引0
            "方案二",  # 对应索引1
            "方案三"  # 对应索引2
        ]
        # 确保索引与下拉框匹配
        # 下拉框显示的是方案1，2，3（对应实际索引值1，2，3）
        # 而descriptions数组索引是0，1，2
        actual_index = 0
        if index == 0:
            actual_index = 0  # 最佳方案 - 显示为"方案一"
        elif index == 1:
            actual_index = 0  # 方案1 - 显示为"方案一"
        elif index == 2:
            actual_index = 1  # 方案2 - 显示为"方案二"
        elif index == 3:
            actual_index = 2  # 方案3 - 显示为"方案三"
        else:
            # 防止索引越界，默认使用最后一个描述
            actual_index = min(index - 1, len(descriptions) - 1)
            if actual_index < 0:
                actual_index = 0

        return descriptions[actual_index]

    def brute_force_optimize(self):
        if self.D1 <= self.D3:
            raise ValueError("无效的直径范围：D1必须大于D3")

        # 确定D0的搜索范围，取整数
        D0 = round(self.D1,self.d_precision) #D0=D1
        min_T0 = round(self.T1, 1) + 0.1
        max_T0 = 5.0
        all_solutions = []
        for T0 in np.arange(min_T0, max_T0 + 0.05, 0.1):
            T0 = round(float(T0), 1)
            individual = {'D0': D0, 'T0': T0}
            # 计算适应度
            fitness = self._calculate_fitness(individual)
            # 保存当前方案
            all_solutions.append((D0, T0, fitness))
        # 按适应度从高到低排序
        all_solutions.sort(key=lambda x: x[2], reverse=True)
        # 构建最终结果
        results = []
        for i, (D0, T0, fitness) in enumerate(all_solutions):
            # 使用当前D0重新计算所有参数
            individual = {'D0': D0, 'T0': T0}
            fitness,L1, L2, L0, area_change_ratio2 = self._calculate_fitness_change(individual)
            # 构建方案
            solution = {
                # 方案名称
                'name': f"方案{i + 1}",
                'description': self._get_solution_description(i + 1, fitness),

                # 素材参数 - 使用传入的精度参数
                'D0': round(D0, self.d_precision),  # 素材外径
                'T0': round(T0, self.t_precision),  # 素材壁厚
                'L0': round(L0, self.l_precision),  # 素材长度

                # 工艺参数 - 固定为小数点后两位精度
                'D1': round(self.D1, 2),  # 扩口大径(已知)
                'D2': round(self.D2, 2),  # 抽管段小径(已知)
                'D3': round(self.D3, 2),  # 直管外径径(已知)

                'L1': round(L1, 2),  # 扩口段长度
                'L2': round(L2, 2),  # tp抽段长度
                'L4': round(self.L4, 2),

                'T1': round(self.T1, 2),  # 最大壁厚(已知)
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),

                # 优化度
                'fitness':round(fitness, 4),
                #'is_feasible':is_feasible,

                # 截面积变化率
                'area_change_rate': {'area_change': round(area_change_ratio2 * 100, 2)},
            }

            results.append(solution)

        # 如果没有找到有效方案，添加一个默认方案
        if not results:
            default_solution = {
                'name': "默认方案",
                'description': "未找到有效方案，请调整参数范围",
                'D0': round((self.D1 + self.D3) / 2, self.d_precision),
                'T0': round(self.T1, self.t_precision),
                'L0': 0,
                'D1': round(self.D1, 2),
                'D2': round(self.D2, 2),
                'D3': round(self.D3, 2),
                'L1': 0,
                'L2': 0,
                'L4': 0,
                'T1': round(self.T1, 2),
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),
                'fitness': 0,
                'area_change_rate': {'area_change': 0},
                #'is_feasible': False,
            }
            results.append(default_solution)

        return results

    def brute_force_optimize2(self):
        if self.D1 <= self.D3:
            raise ValueError("无效的直径范围：D1必须大于D3")

        self.Dn = self.D1
        min_D0 = int(self.Dn) + 1
        min_T0 = round(self.T1, 1) + 0.1
        max_T0 = 5.0
        all_solutions = []
        for T0 in np.arange(min_T0, max_T0 + 0.05, 0.1):
            T0 = round(float(T0), 1)
            individual = {'D0': min_D0, 'T0': T0}
            fitness = self._calculate_fitness2(individual)
            all_solutions.append((individual['D0'], T0, fitness))
        all_solutions.sort(key=lambda x: x[2], reverse=True)

        # 提取前三个最佳方案
        top_solutions = all_solutions[:3]
        results=[]
        # 构建最终结果
        for i, (D0, T0, fitness) in enumerate(top_solutions):
            # 使用当前D0重新计算所有参数
            individual = {'D0': D0, 'T0': T0}
            fitness, T0, Ln, L1, L2, L0, area_change_ratio1,area_change_ratio2,area_change_ratio3,area_change_ratio4 = self._calculate_fitness_change2(individual)

            # 构建方案
            solution = {
                # 方案名称
                'name': f"方案{i + 1}",
                'description': self._get_solution_description(i + 1, fitness),

                # 素材参数 - 使用传入的精度参数
                'D0': round(individual['D0'], self.d_precision),  # 素材外径
                'T0': round(T0, self.t_precision),  # 素材壁厚
                'L0': round(L0, self.l_precision),  # 素材长度

                # 工艺参数 - 固定为小数点后两位精度
                'Dn': round(self.Dn, 2),
                'D1': round(self.D1, 2),  # 扩口大径(已知)
                'D2': round(self.D2, 2),
                'D3': round(self.D3, 2),  # 直管外径径(已知)
                'Ln': round(Ln, 2),
                'L1': round(L1, 2),  # 扩口段长度
                'L2': round(L2, 2),  # tp抽段长度
                'L4': round(self.L4, 2),

                'T1': round(self.T1, 2),  # 最大壁厚(已知)
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),
                'fitness': round(fitness, 4),  # 优化度分数
                'area_change_rate': {
                    'area_change1': round(area_change_ratio1 * 100, 2),
                    'area_change2': round(area_change_ratio2 * 100, 2),
                    'area_change3': round(area_change_ratio3 * 100, 2),
                    'area_change4': round(area_change_ratio4 * 100, 2),
                },
                'is_feasible': False
            }

            results.append(solution)

        # 如果没有找到有效方案，添加一个默认方案
        if not results:
            default_solution = {
                'name': "默认方案",
                'description': "未找到有效方案，请调整参数范围",
                'D0': round((self.D1 + self.D3) / 2, self.d_precision),
                'T0': round(self.T1, self.t_precision),
                'L0': 0,
                'Dn': 0,
                'D1': round(self.D1, 2),
                'D2': round(self.D2, 2),
                'D3': round(self.D3, 2),
                'Ln': 0,
                'L1': 0,
                'L2': 0,
                'L4': 0,
                'T1': round(self.T1, 2),
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),
                'fitness': 0,
                'area_change_rate': {
                    'area_change1': 0,
                    'area_change2': 0,
                    'area_change3': 0,
                    'area_change4': 0,
                },
                'is_feasible': False
            }
            results.append(default_solution)

        return results


