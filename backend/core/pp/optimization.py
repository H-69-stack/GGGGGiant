"""
Process Planning - Genetic Algorithm Optimization.
Ported from bin/pp_bin/opt/ — consolidates process_optimization.py and ga_opt.py.
Supports multiple scenarios: basic (Normal+Taper), Normal+TP, replenish, etc.
"""
import numpy as np
import random
import time
from typing import Dict, Any, List, Callable, Tuple

from .process_length import require_process_length
from .legacy_opt import (
    opt_kuo,
    opt_nor_kuo,
    opt_nor_taper,
    opt_nor_taper_kuo,
    opt_normal_tp,
    opt_replenish,
    opt_tp,
    opt_tp_kuo,
)


class _WallThicknessSeries(list):
    @property
    def iloc(self):
        return self

    @property
    def values(self):
        return np.array(self)

    def __truediv__(self, other):
        return _WallThicknessSeries([value / other for value in self])


class _WallThicknessTable:
    def __init__(self, rows):
        self._rows = [dict(row) for row in (rows or []) if isinstance(row, dict)]
        columns = []
        for row in self._rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
        self.columns = columns

    @property
    def empty(self):
        return len(self._rows) == 0

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, column):
        return _WallThicknessSeries([row.get(column) for row in self._rows])

    def sort_values(self, by):
        return _WallThicknessTable(
            sorted(self._rows, key=lambda row: float(row.get(by) or 0))
        )

    def reset_index(self, drop=True):
        return self


def _coerce_wall_thickness_table(wall_thickness_data):
    if not isinstance(wall_thickness_data, list):
        return wall_thickness_data
    try:
        import pandas as pd
        return pd.DataFrame(wall_thickness_data)
    except Exception:
        return _WallThicknessTable(wall_thickness_data)


# ═══════════════════════════════════════════════════════════════════════════════
# Genetic Algorithm Engine (ported from ga_opt.py)
# ═══════════════════════════════════════════════════════════════════════════════

class GeneticAlgorithm:
    """General-purpose GA optimizer."""

    def __init__(self, fitness_func, initialize_func, create_variant_func=None,
                 population_size=300, generations=500, mutation_rate=0.2,
                 elite_size=10, tournament_size=5):
        self.fitness_func = fitness_func
        self.initialize_func = initialize_func
        self.create_variant_func = create_variant_func
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.elite_size = elite_size
        self.tournament_size = tournament_size
        self.best_solution = None
        self.best_fitness = -float('inf')

    def optimize(self, progress_callback=None):
        start_time = time.time()
        if progress_callback:
            try:
                progress_callback(0, {"status": "初始化种群..."})
            except Exception:
                pass

        population = self.initialize_func(self.population_size)
        self.current_population = population

        his_best_fitness = []
        his_best_individual = []
        his_avg_fitness = []

        if progress_callback:
            try:
                progress_callback(5, {"status": "开始优化..."})
            except Exception:
                pass

        current_best = None
        current_best_fitness = -float('inf')

        try:
            for generation in range(self.generations):
                fitnesses = [self.fitness_func(ind) for ind in population]
                best_fitness = max(fitnesses)
                avg_fitness = sum(fitnesses) / len(fitnesses)
                best_idx = int(np.argmax(fitnesses))
                current_best = population[best_idx].copy()
                current_best_fitness = fitnesses[best_idx]

                his_best_fitness.append(best_fitness)
                his_best_individual.append(current_best)
                his_avg_fitness.append(avg_fitness)

                if current_best_fitness > self.best_fitness:
                    self.best_solution = current_best.copy()
                    self.best_fitness = current_best_fitness

                if progress_callback and generation % 20 == 0:
                    progress = 5 + 90 * (generation + 1) / self.generations
                    try:
                        progress_callback(progress, {
                            "generation": generation + 1,
                            "best_fitness": best_fitness,
                            "avg_fitness": avg_fitness,
                            "status": f"第{generation+1}代: 最佳适应度={best_fitness:.4f}"
                        })
                    except Exception:
                        pass

                # Selection (tournament + elite) — FIX: same as old code
                selected = self._selection(population, fitnesses)
                new_population = selected[:self.elite_size]

                while len(new_population) < self.population_size:
                    p1 = random.choice(selected)
                    p2 = random.choice(selected)
                    c1, c2 = self._crossover(p1, p2)
                    c1 = self._mutation(c1)
                    c2 = self._mutation(c2)
                    new_population.append(c1)
                    if len(new_population) < self.population_size:
                        new_population.append(c2)

                population = new_population

        except Exception as e:
            print(f"GA optimization error: {e}")
            # FIX Bug 2: error recovery (from old code)
            if current_best is None and len(population) > 0:
                fitnesses = [self.fitness_func(ind) for ind in population]
                best_idx = np.argmax(fitnesses)
                current_best = population[best_idx].copy()
                current_best_fitness = fitnesses[best_idx]
                if self.best_solution is None or current_best_fitness > self.best_fitness:
                    self.best_solution = current_best.copy()
                    self.best_fitness = current_best_fitness
                    his_best_fitness.append(current_best_fitness)
                    his_best_individual.append(current_best)
                    his_avg_fitness.append(avg_fitness)

        # FIX Bug 1: collect all solutions from ALL generations (same as old code)
        all_solutions = []
        for ind, fit in zip(his_best_individual, his_best_fitness):
            all_solutions.append({'individual': ind, 'fitness': fit})

        if not all_solutions:
            all_solutions = [{'individual': self.best_solution, 'fitness': self.best_fitness}] \
                if self.best_solution else [{'individual': self.initialize_func(1)[0], 'fitness': 0}]

        all_solutions.sort(key=lambda x: x['fitness'], reverse=True)
        top_solutions = all_solutions[:3]
        while len(top_solutions) < 3:
            top_solutions.append(top_solutions[0].copy() if top_solutions else {})

        # Ensure diversity — same as old code
        if len(top_solutions) >= 2 and self._are_similar(top_solutions[0].get('individual', top_solutions[0]),
                                                           top_solutions[1].get('individual', top_solutions[1])):
            raw_ind = top_solutions[0].get('individual', top_solutions[0])
            varied_ind = self._create_variant(raw_ind, 0.1)
            varied_ind['fitness'] = self.fitness_func(varied_ind)
            top_solutions[1] = varied_ind

        # numpy 类型全部转成 Python 原生类型，避免 JSON 序列化失败
        result = {
            'best_solution': self.best_solution,
            'best_fitness': float(self.best_fitness) if self.best_fitness is not None else 0.0,
            'all_solutions': top_solutions,
            'best_fitnesses': [float(x) for x in his_best_fitness],
            'avg_fitnesses': [float(x) for x in his_avg_fitness],
            'computation_time': round(time.time() - start_time, 2),
        }

        if progress_callback:
            try:
                progress_callback(100, {"status": "优化完成", "best_fitness": self.best_fitness})
            except Exception:
                pass

        return result

    def _selection(self, population, fitnesses):
        selected = []
        elite_indices = np.argsort(fitnesses)[-self.elite_size:]
        for idx in elite_indices:
            selected.append(population[idx].copy())
        while len(selected) < self.population_size:
            tournament = random.sample(range(len(population)), min(self.tournament_size, len(population)))
            best_idx = max(tournament, key=lambda i: fitnesses[i])
            selected.append(population[best_idx].copy())
        return selected

    def _crossover(self, p1, p2):
        alpha = random.random()
        c1, c2 = {}, {}
        keys = [k for k in p1.keys() if k in p2]
        for key in keys:
            v1, v2 = p1[key], p2[key]
            # 防御：跳过非数值字段，避免交叉时把 dict 类型的字段（如 area_change_rate）混入
            if not isinstance(v1, (int, float)) or not isinstance(v2, (int, float)):
                continue
            if random.random() < 0.5:
                c1[key] = alpha * v1 + (1 - alpha) * v2
                c2[key] = (1 - alpha) * v1 + alpha * v2
            else:
                c1[key] = v1
                c2[key] = v2
        return c1, c2

    def _mutation(self, individual):
        new_ind = individual.copy()
        if random.random() < self.mutation_rate:
            keys = list(new_ind.keys())
            for key in keys:
                val = new_ind[key]
                # 防御：只对数值字段做变异
                if not isinstance(val, (int, float)):
                    continue
                if random.random() < self.mutation_rate:
                    new_ind[key] = val * random.uniform(0.9, 1.1)
        return new_ind

    def _are_similar(self, ind1, ind2, threshold=0.05):
        for key in ind1:
            if key in ind2:
                v1, v2 = ind1[key], ind2[key]
                # 防御：跳过非数值字段
                if not isinstance(v1, (int, float)) or not isinstance(v2, (int, float)):
                    continue
                denom = max(abs(v1), abs(v2)) + 1e-6
                if abs(v1 - v2) / denom > threshold:
                    return False
        return True

    def _create_variant(self, individual, factor=0.1):
        if self.create_variant_func:
            return self.create_variant_func(individual, factor)
        variant = individual.copy()
        for key in variant:
            val = variant[key]
            # 防御：只对数值字段做扰动
            if not isinstance(val, (int, float)):
                continue
            if random.random() < 0.8:
                variant[key] = val + random.uniform(-factor, factor) * val
        return variant


def run_multiple_optimizations(fitness_func, initialize_func, create_variant_func=None,
                               population_size=300, generations=500,
                               repetitions=3, progress_callback=None):
    """Run GA multiple times and return the best overall result."""
    all_solutions = []
    best_result = None
    best_fitness = -float('inf')

    for run in range(repetitions):
        base_progress = (run / repetitions) * 100

        def wrapped_cb(progress, info, _base=base_progress, _reps=repetitions, _run=run):
            if progress_callback:
                adj_progress = _base + (progress / 100) * (100 / _reps)
                adj_info = info.copy() if isinstance(info, dict) else {}
                adj_info["status"] = f"第{_run+1}次运行（共{_reps}次）: {info.get('status', '')}"
                try:
                    progress_callback(adj_progress, adj_info)
                except Exception:
                    pass

        ga = GeneticAlgorithm(
            fitness_func=fitness_func,
            initialize_func=initialize_func,
            create_variant_func=create_variant_func,
            population_size=population_size,
            generations=generations,
        )
        result = ga.optimize(wrapped_cb)

        if result and 'all_solutions' in result:
            all_solutions.extend(result['all_solutions'])
            if result['best_fitness'] > best_fitness:
                best_fitness = result['best_fitness']
                best_result = result

    if not all_solutions:
        raise ValueError("多次优化运行都未能找到有效解决方案")

    all_solutions.sort(key=lambda x: x['fitness'], reverse=True)
    top_solutions = all_solutions[:3]
    while len(top_solutions) < 3:
        top_solutions.append(top_solutions[0].copy() if top_solutions else {})

    final = best_result.copy() if best_result else {}
    final['all_solutions'] = top_solutions
    final['is_feasible'] = best_fitness >= 0.74 - 1e-6
    final['feasibility_threshold'] = 0.74
    return final


# ═══════════════════════════════════════════════════════════════════════════════
# Process Optimizer (ported from process_optimization.py)
# ═══════════════════════════════════════════════════════════════════════════════

class ProcessOptimizer:
    """管件成型工艺参数优化器 — GA-based optimizer for tube forming parameters."""

    # 默认子目标权重（对应 fitness 计算中原本硬编码的值）。
    # 工艺路线 "Taper+TP抽+扩口" 共 3 段，原合计 0.75。
    DEFAULT_WEIGHTS = {
        'expansion': 0.25,
        'tp_extract': 0.25,
        'taper': 0.25,
    }

    def __init__(self, analysis_results: Dict[str, Any],
                 d_precision=1, t_precision=1, l_precision=0, add1=10, add2=10,
                 weights=None, material_allowance=1.3):
        self.analysis_results = analysis_results
        self.weights = dict(self.DEFAULT_WEIGHTS)
        if isinstance(weights, dict):
            for k, v in weights.items():
                try:
                    self.weights[k] = float(v)
                except (TypeError, ValueError):
                    pass

        wt_data = analysis_results.get('wall_thickness_data', [])
        self.df = _coerce_wall_thickness_table(wt_data)

        self.segments = analysis_results.get('segments', {})

        required_columns = ['中心线长度(mm)', '壁厚(mm)', '外径周长(mm)']
        for col in required_columns:
            if col not in self.df.columns:
                raise ValueError(f"数据缺少必要的列: {col}")

        self.df = self.df.sort_values(by='中心线长度(mm)').reset_index(drop=True)
        self.dD = self.df['外径周长(mm)'] / np.pi

        self.D1 = 0
        self.D3 = 0
        self.L4 = 0
        self.L = 0
        self.wall = [self.segments.get('T1', 0), self.segments.get('T2', 0),
                     self.segments.get('T3', 0), self.segments.get('T4', 0)]
        self.T0 = 0
        self.L0 = 0
        self.L1 = 0
        self.L2 = 0
        self.L3 = 0

        valid_wall = [x for x in self.wall if x > 0]
        if not valid_wall:
            raise ValueError("所有壁厚值都无效（≤0），无法进行工艺优化。")

        self.T1 = max(self.wall)
        self.T2 = min(valid_wall)
        self.T3 = 0
        for t in self.wall:
            if self.T2 < t < self.T1:
                self.T3 = t
            elif self.T1 == self.T2:
                self.T3 = self.T1
        if not self.T3:
            self.T3 = self.T2

        self.d_precision = d_precision
        self.t_precision = t_precision
        self.l_precision = l_precision
        self.add1 = add1
        self.add2 = add2
        self.material_allowance = float(material_allowance)
        self.tube_volume = analysis_results.get('volume', 0)
        self.area_change_ratio = 0.0
        self.outer_extension_ratio = 0.0

        self._extract_diameters()
        self._calculate_total_length()

        self.population_size = 500
        self.generations = 500
        self.mutation_rate = 0.2
        self.elite_size = 10
        self.tournament_size = 5
        self.precision_settings = {
            'd_precision': d_precision,
            't_precision': t_precision,
            'l_precision': l_precision,
        }

    def _extract_diameters(self):
        if 'D1' in self.segments and 'D3' in self.segments and self.segments['D1'] > 0 and self.segments['D3'] > 0:
            self.D1 = self.segments['D1']
            self.D3 = self.segments['D3']
            return
        if self.df is None or self.df.empty:
            raise ValueError("无法提取直径：壁厚数据为空。")
        perimeters = self.df['外径周长(mm)'].values
        valid = perimeters[perimeters > 0]
        if len(valid) == 0:
            raise ValueError("无有效外径周长数据。")
        self.D3 = round(float(np.min(valid)) / np.pi, 2)
        self.D1 = round(float(np.max(valid)) / np.pi, 2)

    def _calculate_total_length(self):
        self.L = require_process_length(self.analysis_results)
        self.L4 = float(self.analysis_results.get('L4') or 0)

    def _initialize_population(self, population_size=None):
        pop_size = population_size or self.population_size
        population = []
        for _ in range(pop_size):
            D0 = random.uniform(max(self.D3 + 0.5, self.D1 / 1.8), self.D1 - 0.5)
            D2 = random.uniform(self.D3 + 0.5, min(self.D3 / 0.7, D0 - 0.5))
            population.append({'D0': D0, 'D2': D2})
        return population

    def _calculate_material_length(self, D0, T0, D3, T3, add1, add2):
        area = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)
        if area <= 0:
            raise ValueError(f"素材截面积≤0: D0={D0}, T0={T0}")
        if self.tube_volume <= 0:
            raise ValueError(f"管件体积数据缺失: {self.tube_volume}")
        taper_margin = np.pi * ((D3 / 2) ** 2 - ((D3 / 2) - T3) ** 2) * add2
        L0 = self.tube_volume / area * self.material_allowance + taper_margin / area + add1
        return float(L0)

    def _calculate_fitness(self, individual):
        # 防御：确保所有参与运算的字段都是数值，避免 abs(dict) / '-' 等运行时错误。
        try:
            D0 = round(float(individual.get('D0', 0)), self.d_precision)
            D2 = float(individual.get('D2', 0))
        except (TypeError, ValueError):
            return -10
        T0 = 0
        L4 = self.L4

        for index, length in enumerate(self.df['中心线长度(mm)']):
            if index < 5:
                continue
            if D0 >= self.dD.iloc[index]:
                self.L1 = length
                break
        L1 = self.L1

        for index, length in enumerate(self.df['中心线长度(mm)']):
            if index < 5:
                continue
            if D2 >= self.dD.iloc[index]:
                self.L2 = length - L1
                break
        L2 = self.L2
        L3 = max(0, self.L - L1 - L2 - L4)
        self.L3 = L3

        raw_t0 = individual.get('T0')
        if raw_t0 is not None and raw_t0 != '':
            try:
                T0 = round(float(raw_t0), self.t_precision)
            except (TypeError, ValueError):
                T0 = round(self.T1 * self.D1 / D0, 1)
        else:
            T0 = round(self.T1 * self.D1 / D0, 1)
        self.T0 = T0

        try:
            L0 = round(self._calculate_material_length(D0, T0, self.D3, self.T3, self.add1, self.add2), self.l_precision)
        except ValueError:
            return -10
        self.L0 = L0

        fitness = 0
        max_penalty = 3

        has_taper = abs(D0 - D2) > 0.5
        self.area_change_ratio = 0
        self.outer_extension_ratio = 0

        expansion_weight = self.weights.get('expansion', 0.25)
        tp_extract_weight = self.weights.get('tp_extract', 0.25)
        taper_weight = self.weights.get('taper', 0.25)

        # 1. Expansion coefficient
        if L1 > 0 and D0 < self.D1:
            flaring_ratio = (self.D1 - D0) / D0
            if flaring_ratio > 0.8:
                fitness -= max_penalty
            else:
                fitness += min(1.0, (0.8 / flaring_ratio) * expansion_weight)

        # 2. TP/Normal draw coefficient
        if L2 > 0 and D0 > D2:
            area_0 = np.pi * ((D0 / 2) ** 2 - ((D0 / 2) - T0) ** 2)
            position = L1 + L2
            position_index = len(self.df) - 1
            for i, length in enumerate(self.df['中心线长度(mm)']):
                if length >= position:
                    position_index = i
                    break
            TT2 = self.df['壁厚(mm)'].iloc[position_index]
            area_2 = np.pi * ((D2 / 2) ** 2 - ((D2 / 2) - TT2) ** 2)
            if area_0 > 0:
                self.area_change_ratio = (area_0 - area_2) / area_0

            min_b, max_b = 0.20, 0.50
            if min_b <= self.area_change_ratio <= max_b:
                fitness += min(1.0, (max_b / self.area_change_ratio) * tp_extract_weight)
            elif self.area_change_ratio < min_b:
                fitness -= 5 * (min_b - self.area_change_ratio) / min_b
            else:
                fitness -= 5 * (self.area_change_ratio - max_b) / max_b

        # 3. Taper coefficient
        if L3 > 0 and D2 > self.D3:
            self.outer_extension_ratio = (D2 - self.D3) / D2
            if self.outer_extension_ratio > 0.3:
                fitness -= max_penalty
            else:
                fitness += min(1.0, 0.3 / self.outer_extension_ratio * taper_weight)

        # Penalties
        penalty = 0
        if L1 > 0:
            if L1 > 150:
                penalty += max_penalty
            elif abs(L1 - 100) > 30:
                penalty += (abs(L1 - 100) - 30) / 100 * 2
        if L2 > 0:
            if L2 > 150:
                penalty += max_penalty
            elif abs(L2 - 100) > 30:
                penalty += (abs(L2 - 100) - 30) / 100 * 2
        fitness -= penalty

        if D0 >= self.D1 or D0 <= self.D3:
            fitness -= max_penalty
        if D2 > D0 or D2 <= self.D3:
            fitness -= max_penalty
        if T0 < round(self.T1, 1) or T0 > 2 * self.T2:
            fitness -= max_penalty

        TL1 = self.segments.get('TL1', 0)
        if L1 > TL1 and TL1 > 0:
            fitness -= max_penalty
        if L3 > 700:
            fitness -= max_penalty

        return fitness

    def _calculate_fitness_change(self, individual):
        """Calculate fitness for manual parameter change (returns detailed info)."""
        fitness = self._calculate_fitness(individual)
        return fitness, self.T0, self.L1, self.L2, self.L3, self.L0, self.area_change_ratio, self.outer_extension_ratio

    def _create_variant(self, individual, variation_factor=0.1):
        variant = individual.copy()
        for key in ['D0', 'D2']:
            if random.random() < 0.8:
                variant[key] += random.uniform(-variation_factor, variation_factor) * individual[key]
        variant['D0'] = max(self.D3 + 0.5, min(self.D1 - 0.5, variant['D0']))
        variant['D2'] = max(self.D3 + 0.5, min(variant['D0'] - 0.5, variant['D2']))
        return variant

    def _format_solution(self, individual, fitness, index):
        d_p = self.precision_settings.get('d_precision', 1)
        t_p = self.precision_settings.get('t_precision', 1)
        l_p = self.precision_settings.get('l_precision', 0)
        return {
            'name': f"方案{index}" if index > 0 else "最佳方案",
            'D0': round(individual['D0'], d_p),
            'T0': round(self.T0, t_p),
            'L0': round(self.L0, l_p),
            'D1': round(self.D1, 2),
            'D2': round(individual['D2'], 2),
            'D3': round(self.D3, 2),
            'L1': round(self.L1, 2),
            'L2': round(self.L2, 2),
            'L3': round(self.L3, 2),
            'L4': round(self.L4, 2),
            'T1': round(self.T1, 2),
            'T2': round(self.T2, 2),
            'T3': round(self.T3, 2),
            'fitness': round(fitness, 4),
            'area_change_rate': round(self.area_change_ratio * 100, 2),
            'outer_extension_ratio': round(self.outer_extension_ratio * 100, 2),
        }

    def _convert_ga_result(self, ga_result):
        """Convert GA result into final optimization result."""
        if not ga_result or 'all_solutions' not in ga_result:
            default = self._initialize_population(1)[0]
            f = self._calculate_fitness(default)
            return {
                'status': 'warning',
                'solutions': [self._format_solution(default, f, 1)],
                'best_fitness': float(f),
                'fitness_history': {'best': [float(f)], 'average': [float(f)]},
                'computation_time': 0.0,
                'is_feasible': False,
            }

        solutions = []
        for i, sol_data in enumerate(ga_result['all_solutions']):
            ind = sol_data.get('individual')
            fit = sol_data.get('fitness')
            if ind and fit is not None:
                self._calculate_fitness(ind)
                solutions.append(self._format_solution(ind, fit, i + 1))

        if not solutions:
            default = self._initialize_population(1)[0]
            f = self._calculate_fitness(default)
            solutions = [self._format_solution(default, f, 1)]

        # 把 fitness_history 里的 numpy float / bool 全部转成原生 Python 类型
        best_hist = ga_result.get('best_fitnesses', [])
        avg_hist = ga_result.get('avg_fitnesses', [])
        try:
            best_hist = [float(x) for x in best_hist]
        except Exception:
            best_hist = []
        try:
            avg_hist = [float(x) for x in avg_hist]
        except Exception:
            avg_hist = []

        return {
            'status': 'success',
            'solutions': solutions,
            'best_fitness': float(ga_result.get('best_fitness', 0)),
            'fitness_history': {
                'best': best_hist,
                'average': avg_hist,
            },
            'computation_time': float(ga_result.get('computation_time', 0)),
            'is_feasible': bool(ga_result.get('is_feasible', False)),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def optimize_process_parameters(analysis_results: Dict[str, Any],
                                progress_callback=None,
                                population_size=300, generations=200,
                                repetitions=1,
                                d_precision=1, t_precision=1, l_precision=0,
                                add1=10, add2=10,
                                weights=None, material_allowance=1.3) -> Dict[str, Any]:
    try:
        optimizer = ProcessOptimizer(
            analysis_results, d_precision, t_precision, l_precision, add1, add2,
            weights=weights, material_allowance=material_allowance,
        )
        optimizer.precision_settings = {
            'd_precision': min(max(0, d_precision), 3),
            't_precision': min(max(0, t_precision), 3),
            'l_precision': min(max(0, l_precision), 3),
        }

        def wrapper_cb(progress, info):
            if progress_callback:
                try:
                    progress_callback(progress, info)
                except Exception:
                    pass

        ga_result = run_multiple_optimizations(
            fitness_func=optimizer._calculate_fitness,
            initialize_func=optimizer._initialize_population,
            create_variant_func=optimizer._create_variant,
            population_size=population_size,
            generations=generations,
            repetitions=repetitions,
            progress_callback=wrapper_cb,
        )

        result = optimizer._convert_ga_result(ga_result)

        if 'is_feasible' in ga_result:
            result['is_feasible'] = ga_result['is_feasible']
            if not ga_result['is_feasible']:
                result['status'] = 'warning'
                result['warning'] = '基础方案不能求出满足所有约束条件的可行解，建议调整参数'

        return result

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            'status': 'error',
            'error': str(e),
            'solutions': [],
            'best_fitness': 0,
            'is_feasible': False,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Local multi-method adapter (restores old project behaviour)
# ═══════════════════════════════════════════════════════════════════════════════

_PP_METHODS = [
    "Taper+TP抽+扩口",
    "普通抽+TP抽+Taper+扩口",
    "加长Taper+TP抽+扩口",
    "仅TP抽",
    "普通抽+TP抽",
    "仅扩口",
    "普通抽+扩口",
    "TP抽+扩口",
    "普通抽+Taper+扩口",
    "普通抽+Taper",
]


def available_pp_methods() -> List[str]:
    return list(_PP_METHODS)


def _prepare_analysis_results_for_legacy(analysis_results: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy optimizers expect pandas DataFrame in wall_thickness_data."""
    out = dict(analysis_results or {})
    out.pop("actual_z_length", None)
    wt = out.get("wall_thickness_data") or []
    out["wall_thickness_data"] = _coerce_wall_thickness_table(wt)
    return out


def _run_bruteforce_method(
    pp_method: str,
    analysis_results: Dict[str, Any],
    *,
    d_precision: int,
    t_precision: int,
    l_precision: int,
    add3: float,
    weights: Dict[str, float] = None,
    material_allowance: float = 1.3,
) -> Dict[str, Any]:
    L4 = float(analysis_results.get("L4") or 0)

    if pp_method == "仅TP抽":
        optimizer = opt_tp.OPT_TP(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, L4=L4, weights=weights,
            material_allowance=material_allowance,
        )
        sols = optimizer.brute_force_optimize()
    elif pp_method == "普通抽+TP抽":
        optimizer = opt_tp.OPT_TP(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, L4=L4, weights=weights,
            material_allowance=material_allowance, add3=add3,
        )
        sols = optimizer.brute_force_optimize2()
    elif pp_method == "仅扩口":
        optimizer = opt_kuo.OPT_Kuo(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, add3=add3, L4=L4, weights=weights,
            material_allowance=material_allowance,
        )
        sols = optimizer.brute_force_optimize()
    elif pp_method == "普通抽+扩口":
        optimizer = opt_nor_kuo.OPT_Nor_Kuo(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, add3=add3, L4=L4, weights=weights,
            material_allowance=material_allowance,
        )
        sols = optimizer.brute_force_optimize()
    elif pp_method == "TP抽+扩口":
        optimizer = opt_tp_kuo.OPT_TP_Kuo(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, add3=add3, L4=L4, weights=weights,
            material_allowance=material_allowance,
        )
        sols = optimizer.brute_force_optimize()
    elif pp_method == "普通抽+Taper+扩口":
        optimizer = opt_nor_taper_kuo.OPT_Nor_Taper_Kuo(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, add3=add3, L4=L4, weights=weights,
            material_allowance=material_allowance,
        )
        sols = optimizer.brute_force_optimize()
    elif pp_method == "普通抽+Taper":
        optimizer = opt_nor_taper.OPT_Nor_Taper(
            analysis_results, d_precision=d_precision, t_precision=t_precision,
            l_precision=l_precision, add3=add3, L4=L4, weights=weights,
            material_allowance=material_allowance,
        )
        sols = optimizer.brute_force_optimize()
    else:
        raise ValueError(f"未知暴力模式: {pp_method}")

    sols = sols or []
    best_fit = max([float(s.get("fitness", 0)) for s in sols], default=0.0)
    return {
        "status": "success" if sols else "warning",
        "solutions": sols,
        "best_fitness": best_fit,
        "fitness_history": {"best": [], "average": []},
        "computation_time": 0.0,
        "is_feasible": bool(best_fit >= 0.74 - 1e-6),
        "pp_method": pp_method,
    }


def _run_ga_method(
    pp_method: str,
    analysis_results: Dict[str, Any],
    *,
    progress_callback=None,
    population_size=300,
    generations=200,
    repetitions=1,
    d_precision=1,
    t_precision=1,
    l_precision=0,
    add1=10,
    add2=10,
    weights: Dict[str, float] = None,
    material_allowance: float = 1.3,
) -> Dict[str, Any]:
    if pp_method == "Taper+TP抽+扩口":
        return optimize_process_parameters(
            analysis_results,
            progress_callback=progress_callback,
            population_size=population_size,
            generations=generations,
            repetitions=repetitions,
            d_precision=d_precision,
            t_precision=t_precision,
            l_precision=l_precision,
            add1=add1,
            add2=add2,
            weights=weights,
            material_allowance=material_allowance,
        )
    if pp_method == "普通抽+TP抽+Taper+扩口":
        return opt_normal_tp.opt_normal_parameters(
            analysis_results,
            progress_callback=progress_callback,
            population_size=population_size,
            generations=generations,
            repetitions=repetitions,
            d_precision=d_precision,
            t_precision=t_precision,
            l_precision=l_precision,
            add1=add1,
            add2=add2,
            weights=weights,
            material_allowance=material_allowance,
        )
    if pp_method == "加长Taper+TP抽+扩口":
        return opt_replenish.opt_replenish_parameters(
            analysis_results,
            progress_callback=progress_callback,
            population_size=population_size,
            generations=generations,
            repetitions=repetitions,
            d_precision=d_precision,
            t_precision=t_precision,
            l_precision=l_precision,
            add1=add1,
            add2=add2,
            weights=weights,
            material_allowance=material_allowance,
        )
    raise ValueError(f"未知GA模式: {pp_method}")


def optimize_process_parameters_by_method(
    analysis_results: Dict[str, Any],
    *,
    pp_method: str = "Taper+TP抽+扩口",
    progress_callback=None,
    population_size=300,
    generations=200,
    repetitions=1,
    d_precision=1,
    t_precision=1,
    l_precision=0,
    add1=10,
    add2=10,
    add3=0,
    weights: Dict[str, float] = None,
    material_allowance: float = 1.3,
) -> Dict[str, Any]:
    if pp_method not in _PP_METHODS:
        return {
            "status": "error",
            "error": f"不支持的工艺模式: {pp_method}",
            "solutions": [],
            "is_feasible": False,
            "pp_method": pp_method,
            "available_methods": available_pp_methods(),
        }

    legacy_input = _prepare_analysis_results_for_legacy(analysis_results)
    try:
        if pp_method in ("仅TP抽", "普通抽+TP抽", "仅扩口", "普通抽+扩口", "TP抽+扩口", "普通抽+Taper+扩口", "普通抽+Taper"):
            return _run_bruteforce_method(
                pp_method, legacy_input,
                d_precision=d_precision, t_precision=t_precision,
                l_precision=l_precision, add3=add3,
                weights=weights,
                material_allowance=material_allowance,
            )
        return _run_ga_method(
            pp_method, legacy_input,
            progress_callback=progress_callback,
            population_size=population_size,
            generations=generations,
            repetitions=repetitions,
            d_precision=d_precision,
            t_precision=t_precision,
            l_precision=l_precision,
            add1=add1,
            add2=add2,
            weights=weights,
            material_allowance=material_allowance,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            "status": "error",
            "error": str(e),
            "solutions": [],
            "is_feasible": False,
            "pp_method": pp_method,
        }


def _with_blank_d0(updates, params):
    """重算后把芯轴约束写回的 D0 交给前端。"""
    out = dict(updates)
    if isinstance(params, dict) and params.get("D0") not in (None, ""):
        out["D0"] = params.get("D0")
    return out


def recalc_solution_by_method(
    analysis_results: Dict[str, Any],
    *,
    pp_method: str,
    params: Dict[str, Any],
    d_precision=1,
    t_precision=1,
    l_precision=0,
    add1=10,
    add2=10,
    add3=0,
    weights: Dict[str, float] = None,
    material_allowance: float = 1.3,
) -> Dict[str, Any]:
    legacy_input = _prepare_analysis_results_for_legacy(analysis_results)
    L4 = float(legacy_input.get("L4") or 0)
    try:
        if pp_method == "Taper+TP抽+扩口":
            optimizer = ProcessOptimizer(legacy_input, d_precision, t_precision, l_precision, add1, add2, weights=weights, material_allowance=material_allowance)

            safe_params = params or {}
            if not isinstance(safe_params, dict):
                safe_params = {}

            # 前端在重算/预览时可能把 area_change_rate / extension_ratio（dict）也带回 params，
            # 但 GA 适应度计算只需要 D0/D2 等数字。这里做一次瘦身与清洗，避免 abs(dict) 之类错误。
            for k in ("area_change_rate", "extension_ratio", "outer_extension_ratio"):
                if k in safe_params and isinstance(safe_params.get(k), dict):
                    safe_params.pop(k, None)

            fit, T0, L1, L2, L3, L0, area, ext = optimizer._calculate_fitness_change(safe_params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": {
                    "T0": T0, "L1": L1, "L2": L2, "L3": L3, "L0": L0,
                    # 与 optimize() 返回字段保持一致：直接返回 number（百分比）
                    "area_change_rate": round(float(area) * 100, 2),
                    "outer_extension_ratio": round(float(ext) * 100, 2),
                },
            }
        if pp_method == "普通抽+TP抽+Taper+扩口":
            optimizer = opt_normal_tp.OPT_Normal_TP(legacy_input, d_precision, t_precision, l_precision, add1, add2, weights=weights, material_allowance=material_allowance)
            fit, T0, L1, L2, L3, L0, Ln, area1, area2, area3, area4, ext = optimizer._calculate_fitness_change(params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": _with_blank_d0({
                    "T0": T0, "L1": L1, "L2": L2, "L3": L3, "L0": L0, "Ln": Ln,
                    "area_change_rate": {
                        "area_change1": float(area1) * 100,
                        "area_change2": float(area2) * 100,
                        "area_change3": float(area3) * 100,
                        "area_change4": float(area4) * 100,
                    },
                    "extension_ratio": {"outer": float(ext) * 100},
                }, params),
            }
        if pp_method == "加长Taper+TP抽+扩口":
            optimizer = opt_replenish.OPT_Replenish(legacy_input, d_precision, t_precision, l_precision, add1, add2, weights=weights, material_allowance=material_allowance)
            safe_params = dict(params or {})
            if safe_params.get("D20") in (None, ""):
                try:
                    d0 = float(safe_params.get("D0") or 0)
                    d2 = float(safe_params.get("D2") or 0)
                except (TypeError, ValueError):
                    d0, d2 = 0.0, 0.0
                if d0 > 0 and d2 > 0:
                    safe_params["D20"] = (d0 + d2) / 2.0
                elif d0 > 0:
                    safe_params["D20"] = d0
            fit, T0, L1, L2, L3, L0, area, outer1, outer2 = optimizer._calculate_fitness_change(safe_params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": {
                    "T0": T0, "L1": L1, "L2": L2, "L3": L3, "L0": L0,
                    "D20": safe_params.get("D20"),
                    "area_change_rate": {"area_change": area * 100},
                    "extension_ratio": {"outer1": outer1 * 100, "outer2": outer2 * 100},
                },
            }
        if pp_method in ("仅TP抽", "普通抽+TP抽"):
            optimizer = opt_tp.OPT_TP(legacy_input, d_precision, t_precision, l_precision, L4=L4, weights=weights, material_allowance=material_allowance, add3=add3)
            if pp_method == "仅TP抽":
                fit, L1, L2, L0, area = optimizer._calculate_fitness_change(params)
                updates = {"L1": L1, "L2": L2, "L0": L0, "area_change_rate": {"area_change": float(area) * 100}}
            else:
                fit, T0, Ln, L1, L2, L0, area1, area2, area3, area4 = optimizer._calculate_fitness_change2(params)
                updates = _with_blank_d0({
                    "T0": T0, "Ln": Ln, "L1": L1, "L2": L2, "L0": L0,
                    "area_change_rate": {
                        "area_change1": float(area1) * 100,
                        "area_change2": float(area2) * 100,
                        "area_change3": float(area3) * 100,
                        "area_change4": float(area4) * 100,
                    },
                }, params)
            return {"success": True, "fitness": round(float(fit), 4), "updates": updates}
        if pp_method == "仅扩口":
            optimizer = opt_kuo.OPT_Kuo(legacy_input, d_precision, t_precision, l_precision, add3=add3, L4=L4, weights=weights, material_allowance=material_allowance)
            fit, D0, T0, L1, L0 = optimizer._calculate_fitness_change(params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": {
                    "D0": D0, "T0": T0, "L1": L1, "Ln": 0, "L0": L0,
                    "D1": optimizer.D1, "D3": optimizer.D3, "Dn": optimizer.D3,
                },
            }
        if pp_method == "普通抽+扩口":
            optimizer = opt_nor_kuo.OPT_Nor_Kuo(legacy_input, d_precision, t_precision, l_precision, add3=add3, L4=L4, weights=weights, material_allowance=material_allowance)
            fit, T0, L1, Ln, L0, area1, area2, area3 = optimizer._calculate_fitness_change(params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": _with_blank_d0({
                    "T0": T0, "L1": L1, "Ln": Ln, "L0": L0,
                    "area_change_rate": {
                        "area_change1": float(area1) * 100,
                        "area_change2": float(area2) * 100,
                        "area_change3": float(area3) * 100,
                    },
                }, params),
            }
        if pp_method == "TP抽+扩口":
            optimizer = opt_tp_kuo.OPT_TP_Kuo(legacy_input, d_precision, t_precision, l_precision, add3=add3, L4=L4, weights=weights, material_allowance=material_allowance)
            fit, T0, L1, L2, L4v, L0, area = optimizer._calculate_fitness_change(params)
            return {"success": True, "fitness": round(float(fit), 4), "updates": {"T0": T0, "L1": L1, "L2": L2, "L4": L4v, "L0": L0, "area_change_rate": {"area_change": float(area) * 100}}}
        if pp_method == "普通抽+Taper+扩口":
            optimizer = opt_nor_taper_kuo.OPT_Nor_Taper_Kuo(legacy_input, d_precision, t_precision, l_precision, add3=add3, L4=L4, weights=weights, material_allowance=material_allowance)
            safe_params = dict(params or {})
            # 前端编辑项只有 D0/T0；Dn 是搜索变量，须沿用当前方案。缺 Dn 时 D2==Dn。
            if safe_params.get("Dn") in (None, ""):
                if safe_params.get("D2") not in (None, ""):
                    safe_params["Dn"] = safe_params["D2"]
            # → T0, L1, L3, Ln, L0, a1, a2, a3, outer_ext
            fit, T0, L1, L3, Ln, L0, area1, area2, area3, ext = optimizer._calculate_fitness_change(safe_params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": _with_blank_d0({
                    "T0": T0, "L1": L1, "L3": L3, "Ln": Ln, "L0": L0,
                    "Dn": safe_params.get("Dn"),
                    "area_change_rate": {
                        "area_change1": float(area1) * 100,
                        "area_change2": float(area2) * 100,
                        "area_change3": float(area3) * 100,
                    },
                    "extension_ratio": {"outer": float(ext) * 100},
                }, safe_params),
            }
        if pp_method == "普通抽+Taper":
            optimizer = opt_nor_taper.OPT_Nor_Taper(legacy_input, d_precision, t_precision, l_precision, add3=add3, L4=L4, weights=weights, material_allowance=material_allowance)
            # → T0, L3, Ln, L0, a1, a2, a3, outer_ext
            fit, T0, L3, Ln, L0, area1, area2, area3, ext = optimizer._calculate_fitness_change(params)
            return {
                "success": True,
                "fitness": round(float(fit), 4),
                "updates": _with_blank_d0({
                    "T0": T0, "L3": L3, "Ln": Ln, "L0": L0,
                    "area_change_rate": {
                        "area_change1": float(area1) * 100,
                        "area_change2": float(area2) * 100,
                        "area_change3": float(area3) * 100,
                    },
                    "extension_ratio": {"outer": float(ext) * 100},
                }, params),
            }
        return {"success": False, "error": f"不支持的模式: {pp_method}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Automatic process route recommendation (from old program)
# ═══════════════════════════════════════════════════════════════════════════════

def recommend_pp_method(
    centerline_length: float,
    max_diameter: float,
    min_diameter: float,
    L4: float = 0,
) -> Dict[str, Any]:
    """
    根据管件几何参数自动推荐最佳工艺路线。
    移植自老程序 pages/11_process_planning.py 的推荐逻辑。

    Args:
        centerline_length: 形线总长度 (mm)
        max_diameter: 最大外径 (mm)
        min_diameter: 最小外径 (mm)
        L4: 直线段长度 (mm)，默认0

    Returns:
        {
            'recommended': '推荐工艺方法名',
            'reason': '推荐原因',
            'all_options': [{'method': ..., 'label': ..., 'feasible': bool}, ...]
        }
    """
    line_length = max(0, centerline_length - (L4 or 0))

    if max_diameter > 0 and min_diameter > 0:
        D_change = (max_diameter - min_diameter) / min_diameter * 100
    else:
        D_change = 0

    options = []

    # 判断各工艺是否可行
    def feasible_by_length(ll, dc, threshold=30):
        return ll < 150 and dc < threshold

    def feasible_by_length_high_change(ll, dc, threshold=30):
        # 形线短、外径变化大 → 普通抽+扩口（与下方推荐分支同一条件）。
        return ll < 150 and dc >= threshold

    def feasible_by_length2(ll, dc):
        return 150 <= ll < 300

    def feasible_by_length3(ll):
        return ll >= 300

    options.append({
        'method': 'tp',
        'label': '仅TP抽',
        'feasible': feasible_by_length(line_length, D_change),
    })
    options.append({
        'method': 'normal+tp',
        'label': '普通抽+TP抽',
        'feasible': feasible_by_length(line_length, D_change),
    })
    options.append({
        'method': 'only-flaring',
        'label': '仅扩口',
        'feasible': feasible_by_length(line_length, D_change),
    })
    options.append({
        'method': 'normal+flaring',
        'label': '普通抽+扩口',
        'feasible': feasible_by_length_high_change(line_length, D_change),
    })
    options.append({
        'method': 'tp+flaring',
        'label': 'TP抽+扩口',
        'feasible': feasible_by_length2(line_length, D_change),
    })
    options.append({
        'method': 'normal+taper+flaring',
        'label': '普通抽+Taper+扩口',
        'feasible': feasible_by_length3(line_length),
    })
    options.append({
        'method': 'tp+taper+flaring',
        'label': 'Taper+TP抽+扩口',
        'feasible': feasible_by_length3(line_length),
    })
    options.append({
        'method': 'normal+tp+taper+flaring',
        'label': '普通抽+TP抽+Taper+扩口',
        'feasible': True,
    })

    # 推荐逻辑：形线 ≥ 300mm 时优先 普通抽+Taper+扩口（高于 Taper+TP抽+扩口）
    recommended_key = None
    reason = ""

    if line_length < 150 and D_change < 30:
        recommended_key = 'tp'
        reason = f"形线长度 {line_length:.0f}mm < 150mm 且外径变化率 {D_change:.1f}% < 30%，推荐采用 [TP抽] 工艺优化模式"
    elif line_length < 150 and D_change >= 30:
        recommended_key = 'normal+flaring'
        reason = f"形线长度 {line_length:.0f}mm < 150mm 且外径变化率 {D_change:.1f}% ≥ 30%，推荐采用 [普通抽+扩口] 工艺优化模式"
    elif 150 <= line_length < 300:
        recommended_key = 'tp+flaring'
        reason = f"形线长度 {line_length:.0f}mm 在 150~300mm 之间，推荐采用 [TP抽+扩口] 工艺优化模式"
    else:
        recommended_key = 'normal+taper+flaring'
        reason = f"形线长度 {line_length:.0f}mm ≥ 300mm，推荐采用 [普通抽+Taper+扩口] 工艺优化模式（优先于 Taper+TP抽+扩口）"

    return {
        'recommended': recommended_key,
        'reason': reason,
        'line_length': round(line_length, 1),
        'D_change': round(D_change, 2),
        'all_options': options,
    }
