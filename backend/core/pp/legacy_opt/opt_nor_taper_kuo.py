# 普通抽+Taper+扩口
# 增加T0搜索范围（步长0.1），D0最大限制改为150
import numpy as np
import random
from typing import Dict, Any, List
import time
from core.pp.process_length import require_process_length
from core.pp.mandrel_d0 import MANDREL_INFEASIBLE_PENALTY, assign_draw_blank_d0


# 不用遗传算法，直接暴力求解D0

class OPT_Nor_Taper_Kuo:
    """【锥度管】管件成型工艺参数优化器，求解最优工艺参数"""

    def __init__(self, analysis_results: Dict[str, Any], d_precision=0, t_precision=1, l_precision=0, add3=20, L4=0,
                 weights=None, material_allowance=1.3):
        self.weights = dict(weights) if isinstance(weights, dict) else {}
        self.material_allowance = float(material_allowance)
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
        self.D3 = 0  # 最小直径
        self.L4 = L4  # 直线段长度
        self.L = 0  # 成品管件总长度
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
        self.add3 = add3

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
        self.area_change_radio1 = 0.0  # 截面积变化率
        self.area_change_radio2 = 0.0  # 截面积变化率
        self.area_change_radio3 = 0.0  # 截面积变化率

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

        # 直线段长度
        # self.L4 = self.segments.get('TL4', 0)  # 假设第4段为直线段 

    def _calculate_material_length(self, D0, T0) -> float:
        """计算素材长度，考虑加工余量"""
        # 基于体积守恒原则计算素材长度
        # 1. 计算素材的截面积（空心圆柱体的截面）
        material_cross_section_area = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # mm²
        # 2. 如果截面积为0或者体积为0，直接报错
        if material_cross_section_area <= 0:
            raise ValueError(
                f"素材截面积计算结果为零或负值: {material_cross_section_area}，无法计算素材长度。请检查D0={D0}和T0={T0}是否合理。")

        if self.tube_volume <= 0:
            raise ValueError(
                f"成品管件体积数据缺失或为零: {self.tube_volume}，无法计算素材长度。请确保成品管件分析完成且体积计算正确。")

        # 3. 基于体积不变原则计算理论长度
        # 体积 = 截面积 × 长度
        material_theoretical_length = self.tube_volume / material_cross_section_area * self.material_allowance  # 加工余量
        L0 = material_theoretical_length

        return float(L0)

    def _calculate_Ln_length(self, D0, T0, L0, T2, Dn,T1,L1) -> float:
        """计算pt抽段长度,减去扩口段长度后使用T2估算最大长度"""
        material_cross_section_area = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # mm²
        if material_cross_section_area <= 0:
            raise ValueError(
                f"素材截面积计算结果为零或负值: {material_cross_section_area}，无法计算素材长度。请检查D0={D0}和T0={T0}是否合理。")
        Ln_cross_section_area = np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - T2) ** 2) # pt抽段截面积
        flare_length = L1 + self.add3
        V_kuo = flare_length * np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - T1) ** 2) # 扩口段体积
        Ln = round((L0 * material_cross_section_area - V_kuo) / Ln_cross_section_area + flare_length, self.l_precision)
        return float(Ln)

    def _calculate_fitness(self, individual: Dict[str, float]) -> float:
        """计算个体适应度（工艺优化度TP）"""
        D0 = round(individual['D0'], self.d_precision)
        Dn = round(individual['Dn'], self.d_precision)
        self.D2 = Dn
        T0, L1 = 0, 0
        self.L1 = 0
        if Dn == self.D1:
            self.L1 = 0
        else:
            for index, length in enumerate(self.df['中心线长度(mm)']):
                if index < 5:
                    continue
                if Dn >= self.dD[index]:
                    self.L1 = length
                    break
        L1 = self.L1
        self.L3 = max(0, self.L - L1)
        L3 = self.L3
        if 'T0' in individual and individual['T0'] is not None:
            T0 = round(float(individual['T0']), self.t_precision)
            self.T0 = T0
        else:
            T0 = round(self.T1 * self.D1 / D0, 1)
            self.T0 = T0
        adjusted_d0 = assign_draw_blank_d0(
            self, individual, '普通抽+Taper+扩口', Dn, T0,
            d2=self.D2, l1=L1, l3=L3, max_d0=150, provisional_d0=D0,
        )
        mandrel_ok = adjusted_d0 is not None
        if mandrel_ok:
            D0 = adjusted_d0
        elif D0 <= 2 * T0:
            D0 = round(2 * T0 + 1, self.d_precision)
        # 计算L0（素材长度）
        L0 = round(self._calculate_material_length(D0, T0), self.l_precision)
        self.L0 = L0

        Ln = self._calculate_Ln_length(D0, T0, L0, self.T2, Dn,self.T1,L1)
        self.Ln = Ln


        # 适应度分数初始化为0
        fitness = 0
        max_up = 1
        max_penalty = 3  # 最大惩罚系数

        # 检查工艺流程是否合理 - 正确的工艺顺序：素材 -> TP抽/普通抽 -> 直线段 -> TAPER(管端)，最后做扩口

        # 各子项的权重 - 总和为1，理论上最低适应度为 1，最高为3
        expansion_weight = float(self.weights.get('expansion', 1/3))  # 扩口段权重
        pt_extract_weight = float(self.weights.get('pt_extract', 1/3))  # 普通抽段权重
        taper_weight = float(self.weights.get('taper', 1/3)) #taper权重

        self.area_change_ratio = 0  # 初始化截面积变化率
        self.area_change_ratio1 = 0.0
        self.area_change_ratio2 = 0.0
        self.area_change_ratio3 = 0.0
        self.outer_extension_ratio = 0.0

        # 1. 计算扩口优化系数 (50%)
        if L1 > 0 and Dn < self.D1:
            # 扩口率 = (D1 - D0) / D0 * 100%
            flaring_ratio = (self.D1 - Dn) / Dn
            # 扩口优化系数KKYHXS = (80%/扩口率) * 25%
            max_boundary = 0.8  # 最大边界值80%
            KKYHXS = min(max_up, (max_boundary / flaring_ratio) * expansion_weight)
            if flaring_ratio > 0.8:
                fitness -= max_penalty  # 惩罚扩口率超过80%的情况
                # print('不满足扩口率约束')
            else:
                fitness += KKYHXS
        else:
            #无扩口或不合理扩口
            fitness += 0


        # # 2. 计算普通抽优化系数
        # # 使用普通抽 (无锥度)
        if Ln > 0 and Dn > self.D3:
            # 计算截面积变化率 - 使用相同的改进方法
            # 计算素材(初始)空心管的截面积
            area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # 素材初始截面积

            # 获取该位置的壁厚值
            TT2 = self.T2
            area_1 = np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - self.T1) ** 2)  # TP抽段截面积
            area_2 = np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - self.T2) ** 2)  # TP抽段截面积
            area_3 = np.pi * ((Dn / 2) ** 2 - ((Dn / 2) - self.T3) ** 2)  # TP抽段截面积
            self.area_change_ratio1 = (area_0 - area_1) / area_0
            self.area_change_ratio2 = (area_0 - area_2) / area_0
            self.area_change_ratio3 = (area_0 - area_3) / area_0
            # 检查截面积变化率是否在所需的15%-50%范围内
            # 判断截面积变化率是否满足约束
            fitness_pt1 = 0
            fitness_pt2 = 0
            fitness_pt3 = 0
            min_boundary = 0.2
            max_boundary = 0.5  # 最大边界值50%
            if min_boundary <= self.area_change_ratio1 <= max_boundary:
                # PT抽优化系数TPCYHXS = (50%/截面积变化率) * 0.25
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

        # 3. 计算taper阶段的优化系数
        if L3 > 0 and self.D2 > self.D3: 
            # 计算外延伸率 - 外径变化率
            outer_extension_ratio = (self.D2 - self.D3) / self.D2
            self.outer_extension_ratio = outer_extension_ratio
            max_boundary = 0.3  # 30%的边界值
            if  outer_extension_ratio > max_boundary:
                TPYHXS = -max_penalty
                #print('不满足延伸率约束')
            else:
                TPYHXS = min(max_up, max_boundary/outer_extension_ratio*taper_weight)
            fitness += TPYHXS
        else:
            # 无TAPER或不合理TAPER
            fitness += 0

        # 惩罚项：工艺参数超出合理范围
        penalty = 0
        # 扩口段长度约束 - 减小惩罚
        if L1 > 0:
            if abs(L1 - 100) <= 30:  # 允许30mm的误差
                pass  # 不惩罚
            elif L1 > 150:
                penalty += max_penalty
                # print('不满足扩口段长度约束')
            else:
                penalty += (abs(L1 - 100) - 30) / 100 * 2

        fitness -= penalty

        # 4. D0 > D3（D0 可大于 D1，搜索上限 150）
        if D0 <= self.D3:
            fitness -= max_penalty
            # print('不满足素材直径约束')

        # 5. MAX(T1,T2,T3) <= T0 <= 2*MIN(T1,T2,T3)，降低惩罚力度
        if T0 < round(self.T1, 1) or T0 > 2 * self.T2:
            fitness -= max_penalty
            # print('不满足壁厚约束')

        # 6. 扩口段壁厚均匀为T1
        TL1 = self.segments.get('TL1', 0)
        if L1 > TL1:
            fitness -= max_penalty
            # print('不满足扩口段壁厚要求')

        #Taper长度约束
        if L3 >700:
            fitness -= max_penalty
            # print('不满足Taper长度约束')
        if not mandrel_ok:
            fitness -= MANDREL_INFEASIBLE_PENALTY

        return fitness

    def _calculate_fitness_change(self, individual: Dict[str, float]) -> float:
        """计算个体适应度变化（用于人工更改参数后适应度计算）"""
        return self._calculate_fitness(individual), self.T0, self.L1,self.L3, self.Ln, self.L0, self.area_change_ratio1,self.area_change_ratio2,self.area_change_ratio3,self.outer_extension_ratio

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
        """
        暴力求解 Dn、D0、T0。
        D0 上限 150；T0 按 0.1 步长搜索（同 opt_nor_kuo）。
        返回适应度由高到低的三个方案。
        """
        if self.D1 <= self.D3:
            raise ValueError("无效的直径范围：D1必须大于D3")

        min_Dn = int(self.D3) + 1
        max_Dn = int(self.D1) - 1
        max_T0 = 5.0
        if min_Dn >= max_Dn:
            raise ValueError("搜索范围太小：无法找到合适的D2值")

        all_solutions = []

        for Dn in range(min_Dn, max_Dn + 1):
            min_T0 = round(self.T1 * self.D1 / Dn, 1) + 0.1
            if min_T0 > max_T0:
                continue
            for T0 in np.arange(min_T0, max_T0 + 0.05, 0.1):
                T0 = round(float(T0), 1)
                individual = {'Dn': Dn, 'D0': Dn + 1, 'T0': T0}
                fitness = self._calculate_fitness(individual)
                all_solutions.append((Dn, individual['D0'], T0, fitness))

        all_solutions.sort(key=lambda x: x[3], reverse=True)
        top_solutions = all_solutions[:3]

        results = []
        for i, (Dn, D0, T0, fitness) in enumerate(top_solutions):
            individual = {'Dn': Dn, 'D0': D0, 'T0': T0}
            fitness, T0, L1, L3, Ln, L0, area_change_ratio1,area_change_ratio2,area_change_ratio3,outer_extension_ratio = self._calculate_fitness_change(individual)

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
                'Dn': round(Dn,2),
                'D1': round(self.D1, 2),  # 扩口大径(已知)
                'D2': round(self.D2,2),  # Taper大径
                'D3': round(self.D3, 2),  # 直管外径径(已知)

                'L1': round(L1, 2),  # 扩口段长度
                'L2': 0,
                'L3': round(L3, 2),  #taper长度
                'L4': round(self.L4, 2),  # 直线段长度
                'Ln': round(Ln, 2),  # 普通抽段长度

                'T1': round(self.T1, 2),  # 最大壁厚(已知)
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),

                # 优化度
                'fitness': round(fitness, 4),  # 优化度分数

                # 截面积变化率
                'area_change_rate': {
                    'area_change1': round(area_change_ratio1 * 100, 2),
                    'area_change2': round(area_change_ratio2 * 100, 2),
                    'area_change3': round(area_change_ratio3 * 100, 2)},
                'extension_ratio':{
                    'outer':round(outer_extension_ratio * 100,2)
                },
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
                'D2': 0,
                'D3': round(self.D3, 2),
                'L1': 0,
                'L2': 0,
                'L3': 0,
                'L4': 0,
                'Ln':0,
                'T1': round(self.T1, 2),
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),
                'fitness': 0,
                'area_change_rate': {'area_change1': 0, 'area_change2': 0, 'area_change3': 0},
                'extension_ratio':{'outer':0}
            }
            results.append(default_solution)

        return results


