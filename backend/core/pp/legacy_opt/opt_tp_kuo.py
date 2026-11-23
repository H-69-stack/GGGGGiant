#tp抽+扩口
import numpy as np
import random
from typing import Dict, Any, List
import time
from core.pp.process_length import require_process_length
#不用遗传算法，直接暴力求解D0,D2

class OPT_TP_Kuo:
    """【直管】管件成型工艺参数优化器，使用遗传算法求解最优工艺参数"""

    def __init__(self, analysis_results: Dict[str, Any], d_precision=0, t_precision=1, l_precision=0, add3 = 20,L4 = 0,
                 weights=None, material_allowance=1.3):
        self.weights = dict(weights) if isinstance(weights, dict) else {}
        self.material_allowance = float(material_allowance)
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
        self.L4 = L4 + add3  # 直线段长度
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

        # 直线段长度
        #self.L4 = self.segments.get('TL4', 0)  # 假设第4段为直线段    #?


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

        # 4. 增加30%的加工余量
        return float(L0)



    def _calculate_fitness(self, individual: Dict[str, float]) -> float:
        """计算个体适应度（工艺优化度TP）"""
        D0 = round(individual['D0'], self.d_precision)
        #D2 = round(individual['D2'], self.d_precision)
        self.D2 = self.D3
        D2 = self.D2
        T0,L1, L2 = 0, 0,0
        for index, length in enumerate(self.df['中心线长度(mm)']):
            if index < 5:
                continue
            if D0 >= self.dD[index]:
                self.L1 = length
                break

        L1 = self.L1
        # for index, length in enumerate(self.df['中心线长度(mm)']):
        #     if D2 >= self.dD[index]:
        #         self.L2 = length-L1
        #         break
        L2 = max(self.L - L1 - self.L4 + self.add3,0)
        self.L2 = L2
        # 确保L1 + L2 + L4  = L

        if 'T0' in individual:
            self.T0 = individual['T0']
            T0 = self.T0
        else:
            T0 = round(self.T1 * self.D1 / D0, 1)
            self.T0 = T0
        # 计算L0（素材长度）
        L0 = round(self._calculate_material_length(D0, T0), self.l_precision)
        self.L0 = L0

        # 适应度分数初始化为0
        fitness = 0
        max_up = 1
        max_penalty = 3  # 最大惩罚系数

        # 检查工艺流程是否合理 - 正确的工艺顺序：素材 -> TP抽/普通抽 -> 直线段 -> TAPER(管端)，最后做扩口

        # 各子项的权重 - 总和为1，理论上最低适应度为 1，最高为3
        expansion_weight = float(self.weights.get('expansion', 1/3))  # 扩口段权重
        tp_extract_weight = float(self.weights.get('tp_extract', 1/2))   # TP抽权重

        self.area_change_ratio = 0  # 初始化截面积变化率

        # 1. 计算扩口优化系数 (50%)
        if L1 > 0 and D0 < self.D1:
            # 扩口率 = (D1 - D0) / D0 * 100%
            flaring_ratio = (self.D1 - D0) / D0
            # 扩口优化系数KKYHXS = (80%/扩口率) * 25%
            max_boundary = 0.8  # 最大边界值80%
            KKYHXS = min(max_up, (max_boundary / flaring_ratio) * expansion_weight)
            if flaring_ratio > 0.8:
                fitness -= max_penalty  # 惩罚扩口率超过80%的情况
                print('不满足扩口率约束')
            else:
                fitness += KKYHXS
        else:
            # 无扩口或不合理扩口
            fitness += 0

        # 2. 计算TP抽
        if L2 > 0 and D0 > self.D2:
            # 计算截面积变化率 - 使用相同的改进方法
            # 计算素材(初始)空心管的截面积
            area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # 素材初始截面积

            position = L1 + L2
            position_index = -1

            # 找到最接近position的中心线长度点
            for i, length in enumerate(self.df['中心线长度(mm)']):
                if length >= position:
                    position_index = i
                    break

            # 如果没有找到大于等于position的点，使用最后一个点
            if position_index == -1:
                position_index = len(self.df) - 1

            # 确保索引有效
            if position_index >= len(self.df):
                position_index = len(self.df) - 1

            # 获取该位置的壁厚值
            TT2 = self.df['壁厚(mm)'][position_index]
            area_2 = np.pi * ((self.D2 / 2) ** 2 - ((self.D2 / 2) - TT2) ** 2)  # TP抽段截面积
            self.area_change_ratio = (area_0 - area_2) / area_0

            fitness_pt = 0
            min_boundary = 0.2
            max_boundary = 0.5  # 最大边界值50%
            if min_boundary <= self.area_change_ratio <= max_boundary:
                TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio) * tp_extract_weight)
                fitness_pt += TPCYHXS
            elif self.area_change_ratio < min_boundary:
                penalty_factor = (min_boundary - self.area_change_ratio) / min_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt += - penalty
            elif self.area_change_ratio > max_boundary:
                penalty_factor = (self.area_change_ratio - max_boundary) / max_boundary  # 归一化惩罚因子
                penalty = 5 * penalty_factor
                fitness_pt += - penalty
            fitness += fitness_pt


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

        if L2 >150:
            fitness -= max_penalty

        # 4. D0 < D1, D0 > D3，降低惩罚力度
        if D0 >= self.D1 or D0 <= self.D3:
            fitness -= max_penalty
            # print('不满足素材直径约束')

        # 5. MAX(T1,T2,T3) <= T0 <= 2*MIN(T1,T2,T3)，降低惩罚力度
        if T0 < round(self.T1, 1) or T0 > 2 * self.T2:
            fitness -= max_penalty
            # print(T0,self.T1,self.T2)
            print('不满足壁厚约束')

        # 6. 扩口段壁厚均匀为T1
        TL1 = self.segments.get('TL1', 0)
        if L1 > TL1:
            fitness -= max_penalty
            print('不满足扩口段壁厚要求')

        return fitness

    def _calculate_fitness_change(self, individual: Dict[str, float]) -> float:
        """计算个体适应度变化（用于人工更改参数后适应度计算）"""
        return self._calculate_fitness(individual), self.T0, self.L1, self.L2,self.L4,self.L0, self.area_change_ratio



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
        暴力求解D0,D2，在D1和D3之间寻找最优解
        返回适应度由高到低的三个方案
        """
        if self.D1 <= self.D3:
            raise ValueError("无效的直径范围：D1必须大于D3")

        # 确定D0的搜索范围，取整数
        min_D0 = max(int(self.D3) + 1,int(self.D1/1.8))
        max_D0 = int(self.D1) - 1

        # 确保搜索范围有效
        if min_D0 >= max_D0:
            raise ValueError("搜索范围太小：无法找到合适的D0值")

        # 存储所有方案
        all_solutions = []

        # 遍历所有可能的D0值
        for D0 in range(min_D0, max_D0 + 1):
            individual = {'D0': D0}

            # 计算适应度
            fitness = self._calculate_fitness(individual)

            # 保存当前方案
            all_solutions.append((D0,fitness))

        # 按适应度从高到低排序
        all_solutions.sort(key=lambda x: x[1], reverse=True)

        # 提取前三个最佳方案
        top_solutions = all_solutions[:3]

        # 构建最终结果
        results = []
        for i, (D0,fitness) in enumerate(top_solutions):
            # 使用当前D0 D2重新计算所有参数
            individual = {'D0': D0}
            fitness, T0, L1, L2, L4, L0, area_change_ratio = self._calculate_fitness_change(individual)

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
                'D2': round(self.D2, 2),
                'D3': round(self.D3, 2),  # 直管外径径(已知)

                'L1': round(L1, 2),  # 扩口段长度
                'L2': round(L2, 2),  # tp抽段长度
                'L4': round(L4, 2),


                'T1': round(self.T1, 2),  # 最大壁厚(已知)
                'T2': round(self.T2, 2),
                'T3': round(self.T3, 2),

                # 优化度
                'fitness': round(fitness, 4),  # 优化度分数

                # 截面积变化率
                'area_change_rate': {
                    'area_change': round(area_change_ratio * 100, 2)
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
            }
            results.append(default_solution)

        return results


