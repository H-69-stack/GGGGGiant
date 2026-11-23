# TP抽+加长Taper+扩口
import bisect

import numpy as np
import random
from typing import Dict, Any, List
import time
from core.pp.process_length import require_process_length
# 导入通用遗传算法类
from .ga_opt import run_multiple_optimizations

#5.10 21:40 修改外径计算直径方式，忽略前5mm
class OPT_Replenish:
    """管件成型工艺参数优化器，使用遗传算法求解最优工艺参数"""

    DEFAULT_WEIGHTS = {
        'expansion': 0.25,
        'tp_extract': 0.25,
        'pt_extract': 0.25,
        'taper': 0.25,
    }

    def __init__(self, analysis_results: Dict[str, Any],d_precision=0,t_precision=1,l_precision=0,add1=10,add2=10,
                 weights=None, material_allowance=1.3):

        self.analysis_results = analysis_results
        self.material_allowance = float(material_allowance)
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if isinstance(weights, dict):
            for k, v in weights.items():
                try:
                    self.weights[k] = float(v)
                except (TypeError, ValueError):
                    pass
        self.df = analysis_results['wall_thickness_data']
        self.segments = analysis_results['segments']
        self.dD = self.df['外径周长(mm)'] / np.pi

        # 提取已知参数
        self.D1 = 0  # 最大直径(扩口大径)
        self.D3 = 0  # 最小直径
        self.L4 = 0  # 直线段长度
        self.L = 0  # 成品管件总长度
        self.wall = [self.segments.get('T1', 0), self.segments.get('T2', 0),self.segments.get('T3', 0), self.segments.get('T4', 0)]
        self.T0 = 0
        
        # 检查是否有有效的壁厚数据
        valid_wall_values = [x for x in self.wall if x > 0]
        if not valid_wall_values:
            raise ValueError("所有壁厚值都无效（≤0），无法进行工艺优化。请检查输入的壁厚数据。")
        
        self.T1 = max(self.wall)  # 最大壁厚
        self.T2 = min(valid_wall_values)  # 最小壁厚
        self.T3 = 0
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
        self.add1 = add1
        self.add2 = add2


        self.tube_volume = analysis_results.get('volume', 0)  # 获取成品管件实际体积 (mm³)

        # 从数据中提取直径信息
        self._extract_diameters()

        # 计算总长度
        self._calculate_total_length()

        # 遗传算法参数
        self.population_size = 500  # 种群规模
        self.generations = 500  # 迭代次数
        self.mutation_rate = 0.2
        self.elite_size = 10
        self.tournament_size = 5

        # 优化求解结果
        self.best_solution = None
        self.optimization_results = None


    def _extract_diameters(self):
        """从分析结果中提取直径信息"""
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

        self.D3 = round(min_perimeter / np.pi, 2)  # 最小直径
        self.D1 = round(max_perimeter / np.pi, 2)  # 最大直径

        # 检查计算出的直径是否有效
        if self.D1 <= 0 or self.D3 <= 0 or np.isnan(self.D1) or np.isnan(self.D3):
            raise ValueError("无法提取有效的直径值：计算结果无效或为零。请检查外径周长数据是否正确。")

    def _calculate_total_length(self):
        """计算管件总长度：修正后的中心线/图纸长度，不用 actual_z_length。"""
        self.L = require_process_length(self.analysis_results)
        
        # 直线段长度用工艺规划确认的 L4，不用壁厚段 TL4
        self.L4 = float(self.analysis_results.get('L4') or 0)



    def _initialize_population(self, population_size = None) -> List[Dict[str, float]]:
        """初始化遗传算法种群"""
        population = []

        # 如果未提供种群大小，使用默认值
        if population_size is None:
            population_size = self.population_size

        for _ in range(population_size):
            # 随机生成D0, T0, D2满足约束条件
            # D0 < D1, D0 > D3
            D0 = random.uniform(max(self.D3 + 0.5,self.D1/1.8), self.D1 - 0.5)
            D2 = random.uniform(self.D3 + 1, min(self.D3/0.7,D0 - 1))
            D20 = random.uniform(D2 + 1, D0 - 1)
            # 添加到种群
            individual = {
                'D0': D0,
                'D2': D2,
                'D20': D20,
            }
            population.append(individual)

        return population

    def _calculate_material_length(self, D0, T0,D3,T3,add1,add2) -> float:

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
        taper_margin = np.pi * ((D3 / 2) ** 2 - ((D3 / 2) - T3) ** 2) * add2
        material_theoretical_length = self.tube_volume / material_cross_section_area * self.material_allowance  # 加工余量
        L0 = material_theoretical_length + taper_margin/material_cross_section_area + add1

        return float(L0)

    def find_position(self,arr, target):
        # 使用bisect_left找到插入点
        position = bisect.bisect_left(arr, target)

        # 检查是否找到确切的匹配
        if position < len(arr) and arr[position] == target:
            return position
        else:
            return position

    def _calculate_fitness(self, individual: Dict[str, float]) -> float:
        """计算个体适应度（工艺优化度TP）"""
        D0 = round(individual['D0'], self.d_precision)
        D2 = individual['D2']
        D20 = individual['D20']
        T0,L1,L2=0,0,0
        for index, D in enumerate(self.dD):
            if index < 5:
                continue
            if D <= D0:
                L1 = self.df['中心线长度(mm)'][index]
                break

        for index, D in enumerate(self.dD):
            if index < 5:
                continue
            if D <= D2:
                L2 = self.df['中心线长度(mm)'][index] - L1
                break
        # 计算L0（素材长度）
        if 'T0' in individual:
            self.T0 = individual['T0']
            T0 = self.T0
        else:
            T0 = round(self.T1 * self.D1 / D0, 1)
            self.T0 = T0
        L0 = round(self._calculate_material_length(D0, T0,self.D3,self.T3,self.add1,self.add2), self.l_precision)

        L3 = self.L - L1 - self.L4
        L4 = self.L4  # 获取直线段长度

        self.L1 = L1
        self.L2 = L2
        self.L3 = L3
        self.L0 = L0
        # 适应度分数初始化为0
        fitness = 0
        max_up = 1
        max_penalty = 3


        # 确定是否有锥度，决定使用TP抽还是普通抽
        has_taper = abs(D0 - D2) > 0.5  # 锥度阈值可根据实际情况调整

        # 各子项的权重 - 默认总和为1（可由前端自定义）
        # 注意：TP抽和普通抽是互斥关系，只能选择其中一个，
        # 因此理论上最高适应度为 0.75 (0.25+0.25+0.25)，而非1.0
        expansion_weight = self.weights.get('expansion', 0.25)
        tp_extract_weight = self.weights.get('tp_extract', 0.25)
        pt_extract_weight = self.weights.get('pt_extract', 0.25)
        taper_weight = self.weights.get('taper', 0.25)

        # 存储内外延伸率，用于前端显示和评估
        self.outer_extension_ratio1 = 0
        self.outer_extension_ratio2 = 0

        # 1. 计算扩口优化系数 (25%)
        if L1 > 0 and D0 < self.D1:
            # 扩口率 = (D1 - D0) / D0 * 100%
            flaring_ratio = (self.D1 - D0) / D0
            # 扩口优化系数KKYHXS = (80%/扩口率) * 25%
            max_boundary = 0.8  # 最大边界值80%
            KKYHXS = min(max_up, (max_boundary / flaring_ratio) * expansion_weight)
            if flaring_ratio > 0.8:
                fitness -= max_penalty  # 惩罚扩口率超过80%的情况
            else:
                fitness += KKYHXS
        else:
            # 无扩口或不合理扩口
            fitness += 0

        # 2. 计算TP抽/普通抽优化系数 (25%)
        if has_taper:
            # 使用TP抽
            if L2 > 0 and D0 > D2:
                # 计算截面积变化率 - 使用壁厚分段分析结果
                # 计算素材(初始)空心管的截面积
                area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # 素材初始截面积
                #area_2 = np.pi * ((D20 / 2) ** 2 - ((D20 / 2) - self.T2) ** 2)  # TP抽段初始截面积
                position = L1+L2
                position_index = self.find_position(self.df['中心线长度(mm)'], position)
                TT2 = self.df['壁厚(mm)'][position_index-1]
                area_2 = np.pi * ((D20/2)**2 - ((D20/2) - TT2)**2)  # TP抽段初始截面积
                self.area_change_ratio = (area_0 - area_2) / area_0

                fitness_tp = 0
                min_boundary = 0.2
                max_boundary = 0.5  # 最大边界值50%
                if min_boundary <= self.area_change_ratio <= max_boundary:
                    # PT抽优化系数TPCYHXS = (50%/截面积变化率) * 0.25
                    TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio) * tp_extract_weight)
                    fitness_tp += TPCYHXS
                elif self.area_change_ratio < min_boundary:
                    penalty_factor = (min_boundary - self.area_change_ratio) / min_boundary  # 归一化惩罚因子
                    penalty = 5 * penalty_factor
                    fitness_tp += - penalty
                elif self.area_change_ratio > max_boundary:
                    penalty_factor = (self.area_change_ratio - max_boundary) / max_boundary  # 归一化惩罚因子
                    penalty = 5 * penalty_factor
                    fitness_tp += - penalty

                fitness += fitness_tp
            else:
                # 无TP抽或不合理TP抽
                fitness += 0
        else:
            # 使用普通抽 (无锥度)
            if L2 > 0 and D0 > D2:
                # 计算截面积变化率 - 使用相同的改进方法
                # 计算素材(初始)空心管的截面积
                area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)  # 素材初始截面积
                #area_2 = np.pi * ((D20 / 2) ** 2 - ((D20 / 2) - self.T2) ** 2)  # TP抽段初始截面积
                position = L1+L2
                position_index = self.find_position(self.df['中心线长度(mm)'], position)
                TT2 = self.df['壁厚(mm)'][position_index-1]
                area_2 = np.pi * ((D20/2)**2 - ((D20/2) - TT2)**2)  # TP抽段初始截面积
                self.area_change_ratio = (area_0 - area_2) / area_0
                fitness_pt = 0
                min_boundary = 0.2
                max_boundary = 0.5  # 最大边界值50%
                if min_boundary <= self.area_change_ratio <= max_boundary:
                    # PT抽优化系数TPCYHXS = (50%/截面积变化率) * 0.25
                    TPCYHXS = min(max_up, (max_boundary / self.area_change_ratio) * pt_extract_weight)
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

        # 3. 计算taper阶段的优化系数 (25%)
        if L3 > 0 and D2 > self.D3:
            # 计算外延伸率 - 外径变化率
            outer_extension_ratio1 = (D20 - D2) / D20
            outer_extension_ratio2 = (D2 - self.D3) / D2


            # 存储计算的外延伸率，用于前端显示和约束评估
            self.outer_extension_ratio1 = outer_extension_ratio1
            self.outer_extension_ratio2 = outer_extension_ratio2

            # 设置硬约束：如果内延伸率或外延伸率超过30%，大幅度降低适应度
            max_boundary = 0.3  # 30%的边界值

            if  outer_extension_ratio1 > max_boundary or outer_extension_ratio2 > max_boundary:
                # 应用强烈惩罚，几乎使该项适应度为0
                #TPYHXS = 10 * (1 - min(0.9, total_penalty))
                TPYHXS = -max_penalty
            else:
                # 外延伸率都在30%以内，给予正常奖励
                # 与边界的距离越近，奖励越低
                outer_score1 = min(max_up, max_boundary/outer_extension_ratio1*taper_weight)
                outer_score2 = min(max_up, max_boundary/outer_extension_ratio2*taper_weight)

                # taper的优化系数，内外延伸率都较小时得分最高
                TPYHXS = (outer_score1 + outer_score2) / 2

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
            else:
                penalty += (abs(L1 - 100) - 30) / 100 * 2

        # TP抽/普通抽段长度约束 - 减小惩罚
        if L2 > 0:
            if abs(L2 - 100) <= 30:  # 允许30mm的误差
                pass  # 不惩罚
            elif L2 > 150:
                penalty += max_penalty
            else:
                penalty += (abs(L2 - 100) - 30) / 100 * 2

        # 应用惩罚，降低惩罚的影响
        fitness -= penalty  # 最多扣除20%


        # 4. D3 <D0 < D1
        if D0 >= self.D1 or D0 <= self.D3:
            fitness -= max_penalty

        if D2 > D0 or D2 <= self.D3:
            fitness -= max_penalty

        # 5. MAX(T1,T2,T3) <= T0 <= 2*MIN(T1,T2,T3)，降低惩罚力度
        if T0 < self.T1 or T0 > 2 * self.T2:
            fitness -= max_penalty

        # 6. 扩口段壁厚均匀为T1
        TL1 = self.segments.get('TL1', 0)
        if L1 > TL1:
            fitness -= max_penalty

        #Taper长度约束
        if L3 >700:
            fitness -= max_penalty
            print('不满足Taper长度约束')

        return fitness

    def _calculate_fitness_change(self, individual: Dict[str, float]) -> float:
        """计算个体适应度变化（用于人工更改参数后适应度计算）"""
        return self._calculate_fitness(individual),self.T0,self.L1, self.L2, self.L3, self.L0,self.area_change_ratio,self.outer_extension_ratio1,self.outer_extension_ratio2

    def _create_variant(self, individual, variation_factor=0.1):
        """创建个体的变异版本"""
        variant = individual.copy()

        # 随机变异一些参数
        for key in ['D0','D2', 'D20']:
            if random.random() < 0.8:  # 80%概率变异该参数
                # 在原值基础上增加随机变异
                variation = random.uniform(-variation_factor, variation_factor) * individual[key]
                variant[key] = individual[key] + variation

        # 确保参数满足约束
        # D0 < D1, D0 > D3
        variant['D0'] = max(self.D3 + 1, min(self.D1 - 1, variant['D0']))

        # D0 > D2 > D3
        variant['D2'] = max(self.D3 + 1, min(variant['D0'] - 1, variant['D2']))
        variant['D20'] = max(variant['D2'] + 1, min(variant['D0'] - 1, variant['D20']))

        return variant

    # def optimize(self, progress_callback=None) -> Dict[str, Any]:
    #     """
    #     运行遗传算法优化工艺参数
    #
    #     参数:
    #         progress_callback: 进度回调函数，接收进度百分比(0-100)和当前最佳个体信息
    #
    #     返回:
    #         优化参数结果字典，包含前三个最优解
    #     """
    #     # 计时
    #     start_time = time.time()
    #
    #     # 使用GeneticAlgorithm类进行优化
    #     optimizer = GeneticAlgorithm(
    #         fitness_func=self._calculate_fitness,
    #         initialize_func=lambda size: self._initialize_population(size),
    #         create_variant_func=self._create_variant,
    #         population_size=self.population_size,
    #         generations=self.generations,
    #         mutation_rate=self.mutation_rate,
    #         elite_size=self.elite_size,
    #         tournament_size=self.tournament_size
    #     )
    #
    #     # 运行优化
    #     ga_result = optimizer.optimize(progress_callback)
    #
    #     # 转换结果格式
    #     all_solutions = []
    #
    #     for i, solution_data in enumerate(ga_result['all_solutions']):
    #         individual = solution_data['individual']
    #         fitness = solution_data['fitness']
    #
    #         # 计算当前解的截面积变化率 - 通过调用_calculate_fitness确保计算并保存截面积变化率
    #         self._calculate_fitness(individual)
    #
    #         solution = {
    #             # 方案名称
    #             'name': f"方案{i + 1}",
    #             'description': self._get_solution_description(i, fitness),
    #
    #             # 素材参数
    #             'D0': round(individual['D0'], 2),  # 素材外径
    #             'T0': round(individual['T0'], 2),  # 素材壁厚
    #             'L0': round(self.L0, 2),  # 素材长度
    #
    #             # 工艺参数
    #             'D1': self.D1,  # 扩口大径(已知)
    #             'D2': round(individual['D2'], 2),  # Taper抽小径
    #             'D20': round(individual['D20'], 2),  # Taper抽小径放大版
    #
    #             #'tp_diameter_data': individual['tp_diameter_data'],  # 形线直径数据
    #             'D3': self.D3,  # Taper小径(已知)
    #
    #             'L1': round(self.L1, 2),  # 扩口段长度
    #             'L2': round(self.L2, 2),  # Taper抽段长度
    #             'L3': round(self.L3, 2),  # Taper带形线段长度
    #             'L4': round(self.L4, 2),  # 直线段长度
    #
    #             'T1': round(self.T1, 2),  # 最大壁厚(已知)
    #             'T2': round(self.T2, 2),  # Taper抽段壁厚
    #             'T3': round(self.T3, 2),  # Taper带形线段壁厚(已知)
    #
    #             # 优化度
    #             'fitness': round(fitness, 4),  # 优化度分数
    #
    #             # 截面积变化率数据
    #             'area_change_rate': {
    #                 'area_change':round(self.area_change_ratio*100,2)
    #             },
    #
    #             # 内外延伸率数据
    #             'extension_ratio': {
    #                 'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
    #                 'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
    #             }
    #         }
    #         all_solutions.append(solution)
    #
    #     # 保存最佳解
    #     self.best_solution = ga_result['best_solution']
    #
    #     # 创建优化结果
    #     optimization_result = {
    #         # 三个解
    #         'solutions': all_solutions,
    #
    #         # 优化过程
    #         'best_fitnesses': ga_result['best_fitnesses'],  # 每代最佳适应度
    #         'avg_fitnesses': ga_result['avg_fitnesses'],  # 每代平均适应度
    #
    #         # 原始管件参数(仅作参考)
    #         'original_parameters': {
    #             'D1': round(self.D1, 2),
    #             'D3': round(self.D3, 2),
    #             'L4': round(self.L4, 0)
    #         },
    #
    #         # 计算时间
    #         'computation_time': round(time.time() - start_time, 2),  # 计算时长(秒)
    #
    #         # 截面积变化率
    #         'area_change_rate': {
    #             'area_change': round(self.area_change_ratio * 100, 2)
    #         },
    #
    #         # 内外延伸率
    #         'extension_ratio': {
    #             'outer1': round(self.outer_extension_ratio1 * 100, 2), # 外延伸率，百分比
    #             'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
    #         }
    #     }
    #
    #     # 显示完成进度
    #     if progress_callback:
    #         try:
    #             progress_callback(100, {
    #                 "status": "优化完成，已生成三个方案",
    #                 "computation_time": optimization_result['computation_time'],
    #                 "best_fitness": all_solutions[0]['fitness'] if all_solutions else 0
    #             })
    #         except Exception as e:
    #             print(f"进度回调函数发生错误: {str(e)}，继续执行...")
    #
    #     self.optimization_results = optimization_result
    #
    #     return optimization_result

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



    # def _initialize_with_params(self, params):
    #     """
    #     手动设置初始参数，主要用于测试
    #
    #     参数:
    #         params: 包含初始参数的字典
    #     """
    #     for key, value in params.items():
    #         setattr(self, key, value)
    #     return self

    def _convert_ga_result_to_optimization_result(self, ga_result):
        """将遗传算法结果转换为流程优化结果格式"""
        if not ga_result or 'all_solutions' not in ga_result:
            # 如果没有有效的GA结果，返回一个包含默认方案的结果而不是None
            default_solution = self._initialize_population(1)[0]
            default_fitness = self._calculate_fitness(default_solution)

            results = {
                'status': 'warning',
                'computation_time': 0,
                'error': '无法获取优化结果，返回随机初始解',
                'best_fitness': default_fitness,
                'best_solution': self._format_solution(default_solution, default_fitness, 0),
                'solutions': [self._format_solution(default_solution, default_fitness, 1)],
                'fitness_history': {
                    'best': [default_fitness],
                    'average': [default_fitness],
                },
                'area_change_rate': {
                    'area_change':round(self.area_change_ratio*100,2)
                },
                'extension_ratio': {
                    'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                    'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
                }
            }
            return results

        # 提取结果
        all_solutions = ga_result['all_solutions']
        best_solution = ga_result.get('best_solution')
        best_fitness = ga_result.get('best_fitness', 0)

        # 如果best_solution为None但all_solutions不为空，使用第一个solution
        if best_solution is None and all_solutions and 'individual' in all_solutions[0]:
            best_solution = all_solutions[0]['individual']
            best_fitness = all_solutions[0]['fitness']

        # 如果best_solution仍为None，生成一个默认solution
        if best_solution is None:
            best_solution = self._initialize_population(1)[0]
            best_fitness = self._calculate_fitness(best_solution)

        # 记录当前最佳解
        self.best_fitness = best_fitness
        self.best_individual = best_solution

        # 创建返回结果对象
        results = {
            'status': 'success',
            'computation_time': ga_result.get('computation_time', 0),
            'best_fitness': best_fitness,
            'best_solution': self._format_solution(best_solution, best_fitness, 0),
            'all_solutions': [],
            'solutions': [],  # 添加'solutions'键，与app.py中的期望一致
            'fitness_history': {
                'best': ga_result.get('best_fitnesses', []),
                'average': ga_result.get('avg_fitnesses', []),
            },
            'area_change_rate': {
                'area_change': round(self.area_change_ratio * 100, 2)
            },
            'extension_ratio': {
                'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
            }
        }

        # 格式化所有解决方案
        try:
            # 如果all_solutions为空，使用最佳解创建一个解决方案
            if not all_solutions:
                formatted_solution = self._format_solution(best_solution, best_fitness, 1)
                formatted_solution['area_change_rate'] = {
                    'area_change':round(self.area_change_ratio*100,2)
                }
                formatted_solution['extension_ratio'] = {
                    'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                    'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
                }
                results['all_solutions'].append(formatted_solution)
                results['solutions'].append(formatted_solution)  # 同时添加到solutions
                results['status'] = 'warning'
                results['warning'] = '优化结果中无解决方案，已创建备用方案'
            else:
                for i, solution_data in enumerate(all_solutions):
                    individual = solution_data.get('individual')
                    fitness = solution_data.get('fitness')
                    if individual and fitness is not None:
                        # 计算面积变化率和内外延伸率 (通过调用_calculate_fitness确保计算和存储)
                        self._calculate_fitness(individual)

                        formatted_solution = self._format_solution(individual, fitness, i + 1)
                        formatted_solution['area_change_rate'] = {
                            'area_change': round(self.area_change_ratio * 100, 2)
                        }
                        formatted_solution['extension_ratio'] = {
                            'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                            'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
                        }

                        results['all_solutions'].append(formatted_solution)
                        results['solutions'].append(formatted_solution)  # 同时添加到solutions

            # 如果没有提取到任何解决方案，确保至少有一个默认方案
            if not results['solutions']:
                # 使用最佳解作为默认方案
                if best_solution and best_fitness is not None:
                    default_solution = self._format_solution(best_solution, best_fitness, 1)
                    default_solution['area_change_rate'] = {
                        'area_change': round(self.area_change_ratio * 100, 2)
                    }
                    default_solution['extension_ratio'] = {
                        'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                        'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
                    }
                    results['all_solutions'].append(default_solution)
                    results['solutions'].append(default_solution)  # 同时添加到solutions
                else:
                    # 创建一个随机初始解
                    random_solution = self._initialize_population(1)[0]
                    random_fitness = self._calculate_fitness(random_solution)
                    formatted_solution = self._format_solution(random_solution, random_fitness, 1)
                    formatted_solution['area_change_rate'] = {
                        'area_change': round(self.area_change_ratio * 100, 2)
                    }
                    formatted_solution['extension_ratio'] = {
                        'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                        'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
                    }
                    results['all_solutions'].append(formatted_solution)
                    results['solutions'].append(formatted_solution)  # 同时添加到solutions

                # 添加警告信息
                results['status'] = 'warning'
                results['warning'] = '无法从优化结果中提取方案，已创建默认方案'

        except Exception as e:
            import traceback
            print(f"格式化解决方案时发生错误: {str(e)}")
            print(traceback.format_exc())

            # 确保即使遇到异常也能返回至少一个解决方案
            if not results['solutions']:
                # 创建一个随机初始解
                random_solution = self._initialize_population(1)[0]
                random_fitness = self._calculate_fitness(random_solution)
                formatted_solution = self._format_solution(random_solution, random_fitness, 1)
                formatted_solution['area_change_rate'] = {
                    'area_change':round(self.area_change_ratio*100,2)
                }
                formatted_solution['extension_ratio'] = {
                    'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                    'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
                }
                results['all_solutions'].append(formatted_solution)
                results['solutions'].append(formatted_solution)  # 同时添加到solutions

                # 添加错误信息
                results['status'] = 'error'
                results['error'] = f'格式化解决方案时发生错误: {str(e)}'

        return results

    def _format_solution(self, individual, fitness, index):
        """格式化个体解决方案为可读格式"""
        # 确保有精度设置
        if not hasattr(self, 'precision_settings'):
            self.precision_settings = {
                'd_precision': 1,  # 默认外径精度为1位小数
                't_precision': 1,  # 默认壁厚精度为1位小数
                'l_precision': 0  # 默认长度精度为整数
            }

        # 获取精度设置
        d_precision = self.precision_settings.get('d_precision', 1)
        t_precision = self.precision_settings.get('t_precision', 1)
        l_precision = self.precision_settings.get('l_precision', 0)



        # 如果已经计算了内外延伸率，使用已计算的值；否则计算
        # if not hasattr(self, 'outer1') or not hasattr(self, 'outer2'):
        #     # 计算TAPER段内外延伸率
        #     if individual['L3'] > 0 and individual['D2'] > self.D3:
        #         # 计算外延伸率 - 外径变化率
        #         self.outer_extension_ratio1 = (individual['D20'] - individual['D2']) / individual['D20']
        #         self.outer_extension_ratio2 = (individual['D2'] - self.D3) / individual['D2']
        #     else:
        #         self.outer_extension_ratio1 = 0
        #         self.outer_extension_ratio2 = 0

        solution = {
            # 方案名称
            'name': f"方案{index}" if index > 0 else "最佳方案",
            'description': self._get_solution_description(index, fitness),

            # 素材参数 - 使用传入的精度参数
            'D0': round(individual['D0'], d_precision),  # 素材外径
            'T0': round(self.T0, t_precision),  # 素材壁厚
            'L0': round(self.L0, l_precision),  # 素材长度

            # 工艺参数 - 固定为小数点后两位精度
            'D1': round(self.D1, 2),  # 扩口大径(已知)
            'D2': round(individual['D2'], 2),  # Taper抽小径
            'D20': round(individual['D20'], 2),  # Taper抽小径放大版
            'D3': round(self.D3, 2),  # Taper小径(已知)


            'L1': round(self.L1, 2),  # 扩口段长度
            'L2': round(self.L2, 2),  # Taper抽段长度
            'L3': round(self.L3, 2),  # Taper带形线段长度
            'L4': round(self.L4, 2),  # 直线段长度

            'T1': round(self.T1, 2),  # 最大壁厚(已知)
            'T2': round(self.T2, 2),  # Taper抽段壁厚
            'T3': round(self.T3, 2),  # Taper带形线段壁厚(已知)

            # 优化度
            'fitness': round(fitness, 4),  # 优化度分数

            # 截面积变化率
            'area_change_rate': {
                'area_change': round(self.area_change_ratio * 100, 2)
            },

            # TAPER段内外延伸率
            'extension_ratio': {
                'outer1': round(self.outer_extension_ratio1 * 100, 2),  # 外延伸率，百分比
                'outer2': round(self.outer_extension_ratio2 * 100, 2)  # 外延伸率，百分比
            }
        }

        return solution


def opt_replenish_parameters(analysis_results: Dict[str, Any], progress_callback=None,
                                population_size=300, generations=200, repetitions=1,
                                d_precision=1, t_precision=1, l_precision=0,add1=10,add2=10,
                                weights=None, material_allowance=1.3) -> Dict[str, Any]:
    """优化加工工艺参数

    参数:
        analysis_results: 管件分析结果字典
        progress_callback: 进度回调函数
        population_size: 种群大小
        generations: 迭代代数
        repetitions: 重复运行次数
        d_precision: 外径(D)参数的小数点精度
        t_precision: 壁厚(T)参数的小数点精度
        l_precision: 长度(L)参数的小数点精度

    返回:
        Dict[str, Any]: 优化结果
    """
    try:
        # 创建优化器实例
        optimizer = OPT_Replenish(analysis_results=analysis_results,d_precision=0,t_precision=1,l_precision=0,add1=10,add2=10, weights=weights, material_allowance=material_allowance)

        # 为了防止过高精度导致界面显示问题，限制精度范围
        d_precision = min(max(0, d_precision), 3)
        t_precision = min(max(0, t_precision), 3)
        l_precision = min(max(0, l_precision), 3)

        # 保存精度设置
        optimizer.precision_settings = {
            'd_precision': d_precision,
            't_precision': t_precision,
            'l_precision': l_precision
        }

        # 创建包装回调函数
        def wrapper_callback(progress, info):
            if progress_callback:
                try:
                    # 转发进度和信息
                    progress_callback(progress, info)
                except Exception as e:
                    # 忽略回调错误
                    pass

        # 运行优化
        start_time = time.time()

        # 从遗传算法获取结果
        ga_result = run_multiple_optimizations(
            fitness_func=optimizer._calculate_fitness,
            initialize_func=optimizer._initialize_population,
            create_variant_func=optimizer._create_variant,
            population_size=population_size,
            generations=generations,
            repetitions=repetitions,
            progress_callback=wrapper_callback
        )

        # 计算总计算时间
        computation_time = time.time() - start_time
        ga_result['computation_time'] = computation_time

        # 转换GA结果为最终优化结果
        optimization_result = optimizer._convert_ga_result_to_optimization_result(ga_result)

        # 传递可行性信息
        if 'is_feasible' in ga_result:
            optimization_result['is_feasible'] = ga_result['is_feasible']
            optimization_result['feasibility_threshold'] = ga_result['feasibility_threshold']

            # 如果方案不可行，添加警告信息
            if not ga_result['is_feasible']:
                optimization_result['status'] = 'warning'
                optimization_result['warning'] = '方案不能够求出满足所有约束条件的可行解'

        # 检查返回的结果是否有效
        if optimization_result is None or 'solutions' not in optimization_result or not optimization_result[
            'solutions']:
            # 创建一个基本的结果，包含至少一个随机解
            random_solution = optimizer._initialize_population(1)[0]
            random_fitness = optimizer._calculate_fitness(random_solution)
            formatted_solution = optimizer._format_solution(random_solution, random_fitness, 1)

            # 如果optimization_result为None，创建一个新的结果
            if optimization_result is None:
                optimization_result = {
                    'status': 'error',
                    'error': '转换优化结果失败，返回随机初始解',
                    'computation_time': computation_time,
                    'solutions': [formatted_solution],
                    'best_fitness': random_fitness,
                    'is_feasible': False,  # 设置为不可行
                    'area_change_rate': {
                        'area_change': optimizer.area_change_ratio * 100
                    }
                }
            # 如果缺少solutions，添加一个解决方案
            elif 'solutions' not in optimization_result or not optimization_result['solutions']:
                optimization_result['solutions'] = [formatted_solution]
                optimization_result['status'] = 'warning'
                optimization_result['warning'] = '没有找到有效的优化方案，返回随机初始解'
                optimization_result['is_feasible'] = False  # 设置为不可行

        return optimization_result

    except Exception as e:
        # 记录错误
        print(f"优化过程出错: {str(e)}")
        import traceback
        traceback.print_exc()

        # 创建包含随机解决方案的结果
        try:
            # 尝试创建一个随机初始解
            optimizer = OPT_Replenish(analysis_results)
            random_solution = optimizer._initialize_population(1)[0]
            random_fitness = optimizer._calculate_fitness(random_solution)
            formatted_solution = optimizer._format_solution(random_solution, random_fitness, 1)

            return {
                'status': 'error',
                'error': str(e),
                'traceback': traceback.format_exc(),
                'solutions': [formatted_solution],
                'best_fitness': random_fitness,
                'is_feasible': False,  # 设置为不可行
                'area_change_rate': {
                    'area_change':optimizer.area_change_ratio*100
                }
            }
        except:
            # 如果无法创建随机解，返回空结果
            return {
                'error': str(e),
                'traceback': traceback.format_exc(),
                'solutions': [],
                'status': 'error',
                'is_feasible': False  # 设置为不可行
            }


# 导出主要函数
# __all__ = ['opt_replenish_parameters', 'OPT_Replenish']
# 10-12