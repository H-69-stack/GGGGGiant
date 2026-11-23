# 8-11
import numpy as np
import random
from typing import Dict, Any, List, Tuple, Callable
import time


class GeneticAlgorithm:
    """通用遗传算法优化类，可用于各种优化问题"""

    def __init__(self,
                 fitness_func: Callable,
                 initialize_func: Callable,
                 create_variant_func: Callable = None,
                 are_similar_func: Callable = None,
                 calculate_fitness_func: Callable = None,
                 crossover_func: Callable = None,
                 mutation_func: Callable = None,
                 selection_func: Callable = None,
                 population_size: int = 300,
                 generations: int = 500,
                 mutation_rate: float = 0.2,
                 elite_size: int = 10,
                 tournament_size: int = 5):
        """
        初始化遗传算法

        参数:
            fitness_func: 适应度计算函数，接收个体，返回适应度分数
            initialize_func: 种群初始化函数，返回初始种群列表
            create_variant_func: 创建变异个体函数，可选
            are_similar_func: 判断两个个体是否相似的函数，可选
            calculate_fitness_func: 计算适应度的函数，可选 (默认使用fitness_func)
            crossover_func: 交叉函数，可选 (默认使用内置交叉)
            mutation_func: 变异函数，可选 (默认使用内置变异)
            selection_func: 选择函数，可选 (默认使用锦标赛选择)
            population_size: 种群规模
            generations: 迭代次数
            mutation_rate: 变异率
            elite_size: 精英个体数量
            tournament_size: 锦标赛选择的参赛个体数
        """
        # 存储关键函数
        self.fitness_func = fitness_func
        self.initialize_func = initialize_func
        self.create_variant_func = create_variant_func
        self.are_similar_func = are_similar_func

        # 可选函数 - 如果未提供则使用默认实现
        self.calculate_fitness_func = calculate_fitness_func or self._calculate_fitness
        self.crossover_func = crossover_func or self._crossover
        self.mutation_func = mutation_func or self._mutation
        self.selection_func = selection_func or self._selection

        # 遗传算法参数
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.elite_size = elite_size
        self.tournament_size = tournament_size

        # 优化状态及结果
        self.current_population = None
        self.best_solution = None
        self.best_fitness = -1
        self.best_fitnesses = []
        self.avg_fitnesses = []

    def _calculate_fitness(self, individual):
        """默认的适应度计算函数（直接调用用户提供的fitness_func）"""
        return self.fitness_func(individual)

    def _selection(self, population: List[Dict], fitnesses: List[float]) -> List[Dict]:
        """默认的选择操作 - 使用锦标赛选择"""
        selected = []

        # 保留精英个体
        elite_indices = np.argsort(fitnesses)[-self.elite_size:]
        for idx in elite_indices:
            selected.append(population[idx].copy())

        # 锦标赛选择其余个体
        while len(selected) < self.population_size:
            # 随机选择tournament_size个个体
            tournament = random.sample(range(len(population)), self.tournament_size)
            # 选择适应度最高的个体
            best_idx = max(tournament, key=lambda idx: fitnesses[idx])
            selected.append(population[best_idx].copy())

        return selected

    def _crossover(self, parent1: Dict, parent2: Dict) -> Tuple[Dict, Dict]:
        """默认的交叉操作 - 算术交叉"""
        # 使用算术交叉
        alpha = random.random()

        child1 = {}
        child2 = {}

        if random.random() < 0.5:
            if 'D20' in parent1 and 'D20' in parent2:
                #key_lists = ['D0', 'T0','D2', 'D20']
                key_lists = ['D0', 'D2', 'D20']
            else:
                #key_lists = ['D0', 'T0','D2']
                key_lists = ['D0','D2',]

            for key in key_lists:
                # 两个子代分别继承父代的不同比例
                child1[key] = alpha * parent1[key] + (1 - alpha) * parent2[key]
                child2[key] = (1 - alpha) * parent1[key] + alpha * parent2[key]

        else:
                child1= parent1
                child2= parent2
        return child1, child2

    def _mutation(self, individual: Dict) -> Dict:
        """默认的变异操作 - 随机变异"""
        # 对每个参数以一定概率进行变异
        new_individual = individual.copy()
        if random.random() < self.mutation_rate:
            if 'D20' in new_individual:
                #key_lists = ['D0', 'T0','D2', 'D20']
                key_lists = ['D0', 'D2', 'D20']
            else:
                #key_lists = ['D0','D2']
                key_lists = ['D0', 'D2']

            for key in key_lists:
                if random.random() < self.mutation_rate:
                    # 随机变异，默认在±10%范围内
                    new_individual[key] *= random.uniform(0.9, 1.1)

        return new_individual

    def _are_individuals_similar(self, ind1, ind2, threshold=0.05):
        """默认的相似度判断函数"""
        if self.are_similar_func:
            return self.are_similar_func(ind1, ind2, threshold)

        # 检查关键参数的差异
        for key in ind1.keys():
            if key in ind2:
                # 避免除以零
                denominator = max(abs(ind1[key]), abs(ind2[key])) + 1e-6
                if abs(ind1[key] - ind2[key]) / denominator > threshold:
                    return False
        return True

    def _create_variant(self, individual, variation_factor=0.1):
        """默认的变异个体创建函数"""
        if self.create_variant_func:
            return self.create_variant_func(individual, variation_factor)

        # 默认实现：随机变异一些参数
        variant = individual.copy()
        if 'D20' in variant:
            #key_lists = ['D0', 'T0', 'D2', 'D20']
            key_lists = ['D0', 'D2', 'D20']
        else:
            key_lists = ['D0','D2']

        for key in key_lists:
            if random.random() < 0.8:  # 80%概率变异该参数
                # 在原值基础上增加随机变异
                variation = random.uniform(-variation_factor, variation_factor) * individual[key]
                variant[key] = individual[key] + variation

        return variant

    def optimize(self, progress_callback=None) -> Dict[str, Any]:

        start_time = time.time()

        # 显示初始化进度
        if progress_callback:
            try:
                progress_callback(0, {"status": "初始化种群..."})
            except Exception as e:
                print(f"进度回调函数发生错误: {str(e)}，继续执行...")

        # 初始化种群
        self.current_population = self.initialize_func(self.population_size)

        # 重置记录
        self.his_best_fitness = []
        self.his_best_individual = []
        self.his_avg_fitness = []
        current_best = None
        current_best_fitness = -1

        # 显示初始化完成进度
        if progress_callback:
            try:
                progress_callback(5, {"status": "开始优化..."})
            except Exception as e:
                print(f"进度回调函数发生错误: {str(e)}，继续执行...")

        try:
            for generation in range(self.generations):
                # 计算种群中每个个体的适应度
                fitnesses = [self.calculate_fitness_func(ind) for ind in self.current_population]

                # 记录当前代的最佳适应度和平均适应度
                best_fitness = max(fitnesses)
                avg_fitness = sum(fitnesses) / len(fitnesses)

                # 找出最佳个体
                best_idx = np.argmax(fitnesses)
                current_best = self.current_population[best_idx].copy()
                current_best_fitness = fitnesses[best_idx]
                # 记录历史最佳适应度和个体
                self.his_best_fitness.append(best_fitness)
                self.his_best_individual.append(current_best)
                self.his_avg_fitness.append(avg_fitness)

                # 更新全局最佳解
                if current_best_fitness > self.best_fitness:
                    self.best_solution = current_best.copy()
                    self.best_fitness = current_best_fitness

                # 计算并显示进度
                progress = 5 + 90 * (generation + 1) / self.generations  # 5%-95%的进度
                if progress_callback:
                    # 当前最佳个体的信息
                    best_info = {
                        "generation": generation + 1,
                        "total_generations": self.generations,
                        "best_fitness": best_fitness,
                        "avg_fitness": avg_fitness,
                        "best_individual": current_best,
                        "status": f"第{generation + 1}代: 最佳适应度={best_fitness:.4f}"
                    }
                    try:
                        progress_callback(progress, best_info)
                    except Exception as e:
                        print(f"进度回调函数发生错误: {str(e)}，继续执行...")

                # 选择操作
                selected = self.selection_func(self.current_population, fitnesses)

                # 新种群
                new_population = []

                # 精英保留
                new_population.extend(selected[:self.elite_size])

                # 交叉和变异
                while len(new_population) < self.population_size:
                    # 随机选择两个父代
                    parent1 = random.choice(selected)
                    parent2 = random.choice(selected)

                    # 交叉
                    child1, child2 = self.crossover_func(parent1, parent2)

                    # 变异
                    child1 = self.mutation_func(child1)
                    child2 = self.mutation_func(child2)

                    # 添加到新种群
                    new_population.append(child1)
                    if len(new_population) < self.population_size:
                        new_population.append(child2)

                # 替换旧种群
                self.current_population = new_population
        except Exception as e:
            print(f"优化过程中发生错误: {str(e)}")
            import traceback
            traceback.print_exc()

            # 如果已经有一些结果，使用当前最佳结果
            if current_best is None and len(self.current_population) > 0:
                # 如果没有评估过适应度，手动评估
                fitnesses = [self.calculate_fitness_func(ind) for ind in self.current_population]
                best_idx = np.argmax(fitnesses)
                current_best = self.current_population[best_idx].copy()
                current_best_fitness = fitnesses[best_idx]

                # 更新全局最佳解
                if self.best_solution is None or current_best_fitness > self.best_fitness:
                    self.best_solution = current_best.copy()
                    self.best_fitness = current_best_fitness
                    self.his_best_fitness.append(current_best_fitness)
                    self.his_best_individual.append(current_best)
                    self.his_avg_fitness.append(sum(fitnesses) / len(fitnesses))

            # 通知用户发生错误
            if progress_callback:
                try:
                    progress_callback(95, {"status": f"优化过程中发生错误: {str(e)}，尝试返回最佳结果"})
                except:
                    pass

        # 计算最终种群的适应度
        try:
            final_fitnesses = self.his_best_fitness

            # 找出前三个最佳个体
            top_indices = np.argsort(final_fitnesses)[-3:][::-1]  # 按降序排列，取前3个

            # 如果不足3个，则补充
            while len(top_indices) < 3:
                # 随机生成新个体补充
                additional_individual = self.initialize_func(1)[0]
                self.current_population.append(additional_individual)
                final_fitnesses.append(self.calculate_fitness_func(additional_individual))
                top_indices = np.argsort(final_fitnesses)[-3:][::-1]

            best_individuals = [self.his_best_individual[idx].copy() for idx in top_indices]
            best_fitnesses_values = [final_fitnesses[idx] for idx in top_indices]

            # 确保有3个不同的解，增加微小变异
            if len(best_individuals) >= 2 and self._are_individuals_similar(best_individuals[0], best_individuals[1]):
                best_individuals[1] = self._create_variant(best_individuals[0], 0.1)
                best_fitnesses_values[1] = self.calculate_fitness_func(best_individuals[1])

            if len(best_individuals) >= 3:
                if self._are_individuals_similar(best_individuals[0], best_individuals[2]) or \
                        self._are_individuals_similar(best_individuals[1], best_individuals[2]):
                    best_individuals[2] = self._create_variant(best_individuals[0], 0.2)
                    best_fitnesses_values[2] = self.calculate_fitness_func(best_individuals[2])

        except Exception as e:
            print(f"计算最终适应度时发生错误: {str(e)}")
            # 使用之前的最佳个体，手动创建3个稍有不同的解
            best_individuals = [self.best_solution.copy() if self.best_solution else None]
            best_fitnesses_values = [self.best_fitness if self.best_solution else 0]

            # 创建次优和次次优解
            if self.best_solution:
                var1 = self._create_variant(self.best_solution, 0.1)
                var2 = self._create_variant(self.best_solution, 0.2)
                best_individuals.extend([var1, var2])
                best_fitnesses_values.extend([
                    self.calculate_fitness_func(var1),
                    self.calculate_fitness_func(var2)
                ])

        # 创建优化结果
        optimization_result = {
            'best_solution': self.best_solution,
            'best_fitness': self.best_fitness,
            'all_solutions': [{'individual': ind, 'fitness': fit}
                              for ind, fit in zip(best_individuals, best_fitnesses_values)],
            'best_fitnesses': self.his_best_fitness,
            'avg_fitnesses': self.his_avg_fitness,
            'computation_time': round(time.time() - start_time, 2)
        }

        # 显示完成进度
        if progress_callback:
            try:
                progress_callback(100, {
                    "status": "优化完成",
                    "computation_time": optimization_result['computation_time'],
                    "best_fitness": self.best_fitness
                })
            except Exception as e:
                print(f"进度回调函数发生错误: {str(e)}，继续执行...")

        return optimization_result


def run_multiple_optimizations(fitness_func, initialize_func,
                               population_size=300, generations=500,
                               repetitions=5, progress_callback=None,
                               **kwargs):
    """
    多次运行遗传算法并返回最佳结果

    参数:
        fitness_func: 适应度函数
        initialize_func: 初始化函数
        population_size: 种群大小
        generations: 迭代次数
        repetitions: 重复次数
        progress_callback: 进度回调函数
        **kwargs: 其他传递给GeneticAlgorithm的参数

    返回:
        最佳优化结果
    """
    # 存储所有运行的结果
    all_solutions = []
    best_optimization_result = None
    best_fitness = -1

    # 多次运行优化算法
    for run in range(repetitions):
        # 计算当前进度的基础百分比
        base_progress = (run / repetitions) * 100

        # 创建一个包装的回调函数，调整进度范围
        def wrapped_callback(progress, info):
            if progress_callback:
                # 调整进度，使其在当前运行的范围内
                adjusted_progress = base_progress + (progress / 100) * (100 / repetitions)
                # 更新状态信息
                adjusted_info = info.copy() if isinstance(info, dict) else {}
                adjusted_info["status"] = f"第{run + 1}次运行（共{repetitions}次）: {info.get('status', '')}"
                try:
                    progress_callback(adjusted_progress, adjusted_info)
                except Exception as e:
                    print(f"进度回调函数发生错误: {str(e)}，继续执行...")

        # 创建优化器实例
        optimizer = GeneticAlgorithm(
            fitness_func=fitness_func,
            initialize_func=initialize_func,
            population_size=population_size,
            generations=generations,
            **kwargs
        )

        # 运行优化，传入包装的进度回调
        current_result = optimizer.optimize(wrapped_callback)

        # 记录本次运行的结果
        if current_result and 'all_solutions' in current_result:
            # 获取本次运行的所有解
            for solution_data in current_result['all_solutions']:
                all_solutions.append({
                    'individual': solution_data['individual'],
                    'fitness': solution_data['fitness']
                })

            # 检查是否是最高适应度的结果
            if current_result['best_fitness'] > best_fitness:
                best_fitness = current_result['best_fitness']
                best_optimization_result = current_result

    # 如果没有找到任何有效解决方案，返回最后一次运行的结果
    if not all_solutions:
        if best_optimization_result:
            return best_optimization_result
        else:
            raise ValueError("多次优化运行都未能找到有效解决方案")

    # 按适应度降序排序所有解决方案
    all_solutions.sort(key=lambda x: x['fitness'], reverse=True)

    # 取前三个最优解
    top_solutions = all_solutions[:3]

    # 为防止解决方案不足3个，复制最佳解决方案
    while len(top_solutions) < 3:
        top_solutions.append(top_solutions[0].copy() if top_solutions else {})

    # 创建最终结果
    if best_optimization_result:
        # 使用最佳运行的结果作为基础，但替换方案
        final_result = best_optimization_result.copy()
        final_result['all_solutions'] = top_solutions

        # 添加可行性判断 - 使用0.74作为阈值
        # 注意：适应度的理论最大值为0.75，但实际判断中使用0.74即可视为可行解
        # 添加极小容差(1e-6)避免浮点精度问题
        final_result['is_feasible'] = best_fitness >= (0.74 - 1e-6)
        final_result['feasibility_threshold'] = 0.74

        return final_result
    else:
        # 创建一个基本结果
        return {
            'all_solutions': top_solutions,
            'best_solution': top_solutions[0]['individual'],
            'best_fitness': top_solutions[0]['fitness'],
            'computation_time': 0,
            # 添加极小容差(1e-6)避免浮点精度问题
            'is_feasible': top_solutions[0]['fitness'] >= (0.74 - 1e-6),
            'feasibility_threshold': 0.74
        }


# # 导出主要的类和函数
# __all__ = ['GeneticAlgorithm', 'run_multiple_optimizations']