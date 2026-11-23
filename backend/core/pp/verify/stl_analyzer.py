import trimesh
import numpy as np
from typing import Dict, List, Tuple, Optional
import logging
from scipy.spatial import ConvexHull
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from .centerline_analyzer import CenterlineAnalyzer
from tqdm import tqdm
import os
import pickle
import pandas as pd
from datetime import datetime
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

logger = logging.getLogger(__name__)

class STLAnalyzer:
    """STL模型分析器，专注于管件截面分析"""
    
    def __init__(self, subdivision_level=2):
        self.logger = logging.getLogger(__name__)
        self.mesh = None
        self.original_mesh = None  # 保存原始网格
        self.bounds = None
        self.center = None
        self.length_axis = 2  # 固定使用Z轴为长度方向
        self.translation_offset = None  # 平移偏移量
        self.rotation_applied = False  # 是否已应用旋转
        self.centerline_analyzer = None  # 中心线分析器
        self.use_centerline = True  # 默认使用中心线分析模式
        self.subdivision_level = subdivision_level  # 网格细分级别
        from config import PP_MESH_CACHE_DIR
        self.cache_dir = PP_MESH_CACHE_DIR  # 网格缓存目录
        self.current_file_hash = None  # 当前文件的哈希值，用于缓存区分
        
    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
    
    def _get_cache_path(self, subdivision_level: int, cache_type: str) -> str:
        """获取缓存文件路径"""
        self._ensure_cache_dir()
        if self.current_file_hash is None:
            # 如果没有文件哈希，使用通用缓存（向后兼容）
            cache_filename = f"mesh_subdiv_{subdivision_level}_{cache_type}.pkl"
        else:
            # 使用文件哈希确保不同文件的缓存不会冲突
            cache_filename = f"mesh_subdiv_{subdivision_level}_{cache_type}_{self.current_file_hash}.pkl"
        return os.path.join(self.cache_dir, cache_filename)
    
    def _load_cached_mesh(self, subdivision_level: int, cache_type: str) -> Optional[trimesh.Trimesh]:
        """加载缓存的网格"""
        try:
            cache_path = self._get_cache_path(subdivision_level, cache_type)
            if os.path.exists(cache_path):
                with open(cache_path, 'rb') as f:
                    mesh_data = pickle.load(f)
                    return trimesh.Trimesh(vertices=mesh_data['vertices'], faces=mesh_data['faces'])
        except Exception as e:
            self.logger.warning(f"加载缓存网格失败: {e}")
        return None
    
    def _save_mesh_to_cache(self, mesh: trimesh.Trimesh, subdivision_level: int, cache_type: str):
        """保存网格到缓存"""
        try:
            cache_path = self._get_cache_path(subdivision_level, cache_type)
            mesh_data = {
                'vertices': mesh.vertices,
                'faces': mesh.faces
            }
            with open(cache_path, 'wb') as f:
                pickle.dump(mesh_data, f)
            self.logger.info(f"网格缓存已保存: {cache_path}")
        except Exception as e:
            self.logger.warning(f"保存网格缓存失败: {e}")
    
    def _subdivide_mesh(self, mesh: trimesh.Trimesh, subdivision_level: int) -> trimesh.Trimesh:
        """
        细分网格以增加密度
        
        Args:
            mesh: 原始网格
            subdivision_level: 细分级别
            
        Returns:
            细分后的网格
        """
        if subdivision_level <= 0:
            return mesh
        
        self.logger.info(f"开始网格细分，级别: {subdivision_level}")
        
        # 检查缓存
        cached_mesh = self._load_cached_mesh(subdivision_level, "preprocess")
        if cached_mesh is not None:
            self.logger.info(f"从缓存加载细分级别 {subdivision_level} 的网格")
            return cached_mesh
        
        # 进行网格细分
        current_mesh = mesh.copy()
        
        try:
            for i in tqdm(range(subdivision_level), desc="网格细分", unit="级别"):
                try:
                    # 使用trimesh的subdivide方法
                    vertices, faces = trimesh.remesh.subdivide(current_mesh.vertices, current_mesh.faces)
                    current_mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
                    self.logger.info(f"细分级别 {i+1}/{subdivision_level} 完成，顶点数: {len(current_mesh.vertices)}")
                except Exception as e:
                    self.logger.warning(f"细分级别 {i+1} 失败: {e}")
                    break
            
            # 保存到缓存
            self._save_mesh_to_cache(current_mesh, subdivision_level, "preprocess")
            
            self.logger.info(f"网格细分完成，最终顶点数: {len(current_mesh.vertices)}")
            return current_mesh
            
        except Exception as e:
            self.logger.error(f"网格细分失败: {e}")
            return mesh
        
    def load_stl(self, stl_path: str, progress_callback=None) -> bool:
        """
        加载STL文件，旋转模型使Z轴最长，然后平移模型，并进行网格细分
        
        Args:
            stl_path: STL文件路径
            progress_callback: 进度回调函数 func(progress, message)
            
        Returns:
            是否成功加载
        """
        try:
            if progress_callback:
                progress_callback(0, "正在读取STL文件...")
                
            # 计算文件哈希，用于缓存区分
            import hashlib
            with open(stl_path, 'rb') as f:
                file_content = f.read()
                self.current_file_hash = hashlib.md5(file_content).hexdigest()[:8]  # 使用前8位作为哈希
            
            # 加载原始STL文件
            loaded = trimesh.load(stl_path)
            # 兼容：trimesh.load 可能返回 Scene（包含多个几何体）
            if isinstance(loaded, trimesh.Scene):
                geoms = list(getattr(loaded, "geometry", {}).values())
                if len(geoms) == 0:
                    self.logger.error("STL文件为空或无有效几何体(Scene)")
                    return False
                loaded = trimesh.util.concatenate(geoms)
            self.original_mesh = loaded
            if self.original_mesh.is_empty:
                self.logger.error("STL文件为空或无效")
                return False
            
            self.logger.info(f"原始STL文件加载成功，顶点数: {len(self.original_mesh.vertices)}，文件哈希: {self.current_file_hash}")
            
            if progress_callback:
                progress_callback(10, "模型加载成功，正在准备处理...")
            
            # 复制原始网格用于处理
            self.mesh = self.original_mesh.copy()
            
            if progress_callback:
                progress_callback(15, "正在优化模型方向...")

            # 第一步：旋转模型使Z轴方向最长
            t0 = time.time()
            self._rotate_model_for_longest_z()
            # print(f"模型旋转耗时: {time.time() - t0:.4f}秒") # 0.03s
            
            if progress_callback:
                progress_callback(25, "正在校准模型位置...")

            # 第二步：平移模型使所有轴的最小值等于0
            t0 = time.time()
            self._translate_model_to_origin()
            # print(f"模型平移耗时: {time.time() - t0:.4f}秒") # 0.01s
            
            # 第三步：网格细分（如果指定了细分级别）
            t0 = time.time()
            if self.subdivision_level > 0:
                if progress_callback:
                    progress_callback(35, f"正在进行网格细分(级别{self.subdivision_level})...")
                self.logger.info(f"开始网格细分，级别: {self.subdivision_level}")
                self.mesh = self._subdivide_mesh(self.mesh, self.subdivision_level)
                self.logger.info(f"网格细分完成，最终顶点数: {len(self.mesh.vertices)}")
            else:
                self.logger.info("跳过网格细分（细分级别为0）")
            # print(f"网格细分耗时: {time.time() - t0:.4f}秒") # 0.01s
            
            if progress_callback:
                progress_callback(60, "正在初始化中心线分析器...")

            # 重新计算边界框和中心点
            self.bounds = self.mesh.bounds
            self.center = self.mesh.center_mass
            
            # 初始化中心线分析器
            t0 = time.time()
            self.centerline_analyzer = CenterlineAnalyzer(self.mesh)
            # print(f"中心线分析器初始化耗时: {time.time() - t0:.4f}秒") # 0.01s
            
            if progress_callback:
                progress_callback(70, "正在提取中心线(可能需要较长时间)...")

            # 自动提取中心线
            t0 = time.time()
            if self.extract_centerline():
                self.logger.info("中心线分析模式已启用")
            else:
                self.logger.warning("中心线提取失败，但继续使用中心线模式")
            # TODO：最耗时
            print(f"stl_analyzer 中心线提取耗时: {time.time() - t0:.4f}秒") # 55s
            
            self.logger.info(f"成功加载STL文件，最终顶点数: {len(self.mesh.vertices)}")
            self.logger.info(f"长度轴: Z轴")
            self.logger.info(f"模型已旋转和平移，Z轴范围: {self.bounds[0][2]:.3f} 到 {self.bounds[1][2]:.3f}")
            return True
            
        except Exception as e:
            self.logger.error(f"加载STL文件失败: {str(e)}")
            return False
    
    def _rotate_model_for_longest_z(self):
        """
        基于包围盒尺寸的轴向对齐（不使用PCA）：
        1) 比较 X/Y/Z 三轴长度，找到最长轴；
        2) 仅通过 90° 整数倍旋转把最长轴转到 Z 轴；
        3) 不做任意角度旋转，默认输入模型已沿某一坐标轴放置。
        """
        if self.rotation_applied:
            return

        try:
            bounds = self.mesh.bounds
            dims = np.abs(bounds[1] - bounds[0])
            longest_axis = int(np.argmax(dims))  # 0:X, 1:Y, 2:Z

            # 仅允许 90° 旋转：
            # X -> Z: 绕Y轴 -90°
            # Y -> Z: 绕X轴 +90°
            if longest_axis == 0:
                self.mesh.apply_transform(
                    trimesh.transformations.rotation_matrix(np.radians(-90.0), [0, 1, 0])
                )
                self.logger.info("A方案轴向对齐：检测到X轴最长，已绕Y轴旋转-90°使其对齐Z轴")
            elif longest_axis == 1:
                self.mesh.apply_transform(
                    trimesh.transformations.rotation_matrix(np.radians(90.0), [1, 0, 0])
                )
                self.logger.info("A方案轴向对齐：检测到Y轴最长，已绕X轴旋转+90°使其对齐Z轴")
            else:
                self.logger.info("A方案轴向对齐：检测到Z轴最长，无需旋转")

        except Exception as e:
            self.logger.warning(f"A方案轴向对齐失败，跳过旋转: {e}")

        self.rotation_applied = True
    
    def _translate_model_to_origin(self):
        """
        平移模型：
        - 令模型底面贴近XY平面（z_min -> 0）
        - 将模型在XY方向平移到靠近原点（xy中心 -> 0,0）
        """
        current_bounds = self.mesh.bounds
        min_corner = current_bounds[0]
        max_corner = current_bounds[1]

        center_xy = (min_corner[:2] + max_corner[:2]) / 2.0
        self.translation_offset = np.array(
            [-center_xy[0], -center_xy[1], -min_corner[2]],
            dtype=float
        )

        self.mesh.apply_translation(self.translation_offset)
        self.logger.info(f"模型已平移到底面贴合XY平面并靠近原点，偏移量: {self.translation_offset}")
    
    def extract_centerline(self):
        """
        提取中心线
        
        Returns:
            是否成功提取中心线
        """
        if self.centerline_analyzer is None:
            self.logger.error("中心线分析器未初始化")
            return False
        
        try:
            # 提取中心线点
            t0 = time.time()
            centerline_points = self.centerline_analyzer.extract_centerline()
            if centerline_points is None:
                return False
            t1 = time.time()
            # print(f"中心线提取耗时: {t1 - t0:.4f}秒") # 55s
            
            # 平滑中心线
            t0 = time.time()
            smoothed_centerline = self.centerline_analyzer.smooth_centerline()
            if smoothed_centerline is None:
                return False
            t2 = time.time()
            # print(f"中心线平滑耗时: {t2 - t0:.4f}秒") # 0.02s
            
            # 计算中心线长度 - 使用“处理后的中心线”(已覆盖原中心线)以确保后续一致
            t0 = time.time()
            centerline_length = self.centerline_analyzer.calculate_centerline_length(use_original=False)
            # 若存在首端截断补偿，将长度还原
            try:
                if getattr(self.centerline_analyzer, 'discarded_start_length', 0.0) > 0:
                    centerline_length += self.centerline_analyzer.discarded_start_length
            except Exception:
                pass
            t3 = time.time()
            # print(f"中心线长度计算耗时: {t3 - t0:.4f}秒") # 0.00s
            
            self.logger.info(f"中心线提取成功，长度: {centerline_length:.2f}mm")
            return True
            
        except Exception as e:
            self.logger.error(f"中心线提取失败: {str(e)}")
            return False
    
    def switch_to_centerline_analysis(self):
        """
        切换到中心线分析模式（保持向后兼容）
        """
        if self.centerline_analyzer is None:
            self.logger.error("中心线分析器未初始化")
            return False
        
        # 提取中心线
        if not self.extract_centerline():
            return False
        
        self.use_centerline = True
        self.logger.info("已切换到中心线分析模式")
        return True
    
    def get_model_info(self) -> Dict:
        """
        获取模型基本信息（从Z=0处开始计算长度）
        
        Returns:
            模型信息字典
        """
        if self.mesh is None:
            return {}
        
        bounds = self.mesh.bounds
        dimensions = bounds[1] - bounds[0]
        
        # 确保从Z=0处开始计算长度
        z_min = bounds[0][2]
        z_max = bounds[1][2]
        start_z = max(0.0, z_min)  # 从Z=0或模型起始位置开始
        total_length = z_max - start_z  # 从Z=0处开始的实际长度
        
        # 基础信息
        info = {
            'total_length': total_length,
            'bounds': {
                'min': bounds[0].tolist(),
                'max': bounds[1].tolist(),
                'dimensions': dimensions.tolist()
            },
            'volume': self.mesh.volume,
            'surface_area': self.mesh.area,
            'vertices_count': len(self.mesh.vertices),
            'faces_count': len(self.mesh.faces),
            'length_axis': 'Z轴',
            'center_mass': self.mesh.center_mass.tolist(),
            'z_range': [start_z, z_max],  # 从Z=0处开始的Z轴范围
            'start_z': start_z,  # 起始Z值
            'transformation_info': {
                'rotation_applied': self.rotation_applied,
                'translation_offset': self.translation_offset.tolist() if self.translation_offset is not None else None
            },
            'use_centerline': self.use_centerline
        }
        
        # 优先使用中心线信息
        if self.centerline_analyzer:
            centerline_info = self.centerline_analyzer.get_centerline_info()
            if centerline_info:
                info['centerline_info'] = centerline_info
                # 用户要求：直接使用可视化中的中心线长度（即截断后的有效长度），不包含任何补偿
                # 之前的逻辑是: length_with_comp = centerline_info.get('length_with_compensation', centerline_info['length'])
                # 现在强制使用不带补偿的几何长度，确保与3D可视化完全一致
                info['total_length'] = centerline_info['length']
        
        return info
    
    def get_cross_section_at_position(self, position_mm: float) -> Dict:
        """
        在指定位置获取横截面（沿中心线的相对位置）
        
        Args:
            position_mm: 沿中心线的位置（毫米，从0开始）
            
        Returns:
            横截面数据
        """
        if self.mesh is None:
            return {'error': '模型未加载'}
        
        # 使用中心线方法（直接使用相对位置，不需要转换为绝对Z坐标）
        if self.centerline_analyzer:
            return self.centerline_analyzer.get_cross_section_at_centerline_position(position_mm)
        else:
            return {'error': '中心线分析器未初始化'}
    
    @staticmethod
    def _parallel_worker_perimeter(pos, centerline_analyzer):
        """并行工作函数：获取单个位置的截面周长（静态方法，支持pickle）"""
        try:
            section_data = centerline_analyzer.get_cross_section_at_centerline_position(pos)
            if 'error' not in section_data and 'perimeter' in section_data:
                return (pos, section_data['perimeter'])
            return None
        except Exception as e:
            logging.getLogger(__name__).warning(f"位置 {pos} 周长获取失败: {e}")
            return None
    
    def get_perimeter_data_along_centerline(self, sample_interval_mm: float = 1.0, progress_callback=None) -> Dict:
        """
        沿中心线获取多个位置的周长数据（并行优化版）
        
        Args:
            sample_interval_mm: 采样间隔（毫米）
            progress_callback: 进度回调函数 func(progress, message)
            
        Returns:
            包含周长数据的字典
        """
        if self.mesh is None:
            return {'error': '模型未加载'}
        
        if not self.centerline_analyzer:
            return {'error': '中心线分析器未初始化'}
        
        try:
            # 获取中心线信息
            centerline_info = self.centerline_analyzer.get_centerline_info()
            if not centerline_info or centerline_info['length'] <= 0:
                return {'error': '无法获取中心线信息'}
            
            # 计算总长度（考虑补偿）
            total_length = centerline_info.get('length_with_compensation', centerline_info['length'])
            
            # 生成采样位置
            positions = []
            current_pos = 0.0
            while current_pos <= total_length:
                positions.append(current_pos)
                current_pos += sample_interval_mm
            
            # 确保包含终点
            if positions[-1] < total_length:
                positions.append(total_length)
            
            # -------------------------- 并行处理核心 --------------------------
            # 进程数=CPU核心数（避免资源耗尽）
            max_workers = multiprocessing.cpu_count()
            perimeter_data = []
            
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务（使用静态方法）
                future_to_pos = {
                    executor.submit(
                        self._parallel_worker_perimeter,
                        pos,
                        self.centerline_analyzer
                    ): pos for pos in positions
                }
                
                # 进度条显示
                completed_count = 0
                total_tasks = len(positions)
                # 设置position=0和leave=True确保进度条保留在屏幕上
                with tqdm(total=total_tasks, desc="stl_analyzer 并行获取周长数据", position=0, leave=True) as pbar:
                    for future in as_completed(future_to_pos):
                        result = future.result()
                        if result is not None:
                            perimeter_data.append(result)
                        pbar.update(1)
                        completed_count += 1
                        if progress_callback and completed_count % 10 == 0:  # 每10个任务更新一次回调，减少开销
                            progress = (completed_count / total_tasks) * 100
                            progress_callback(progress, f"计算周长: {completed_count}/{total_tasks}")
            
            # 按位置排序（并行处理后顺序可能乱）
            perimeter_data.sort(key=lambda x: x[0])
            # -------------------------- 并行处理结束 --------------------------
            
            # 若首端被截断：将 [0, discarded_start_length] 段补齐，周长等于第一个有效完整截面的周长
            try:
                discarded = centerline_info.get('discarded_start_length', 0.0)
                if discarded > 0 and len(perimeter_data) > 0:
                    first_pos, first_perim = perimeter_data[0]
                    # 仅当首个位置大于0才需要补齐
                    if first_pos > 0:
                        fill_positions = []
                        p = 0.0
                        while p < first_pos:
                            fill_positions.append(p)
                            p += sample_interval_mm
                        # 去重并合并
                        filled = [(p, first_perim) for p in fill_positions if p < first_pos + 1e-6]
                        perimeter_data = filled + perimeter_data
            except Exception as e:
                self.logger.warning(f"首端补齐失败: {e}")
            
            # 若尾端被截断：将 (last_pos, total_length] 段补齐，周长等于最后一个有效完整截面的周长
            try:
                discarded_end = centerline_info.get('discarded_end_length', 0.0)
                if discarded_end > 0 and len(perimeter_data) > 0:
                    last_pos, last_perim = perimeter_data[-1]
                    if last_pos < total_length:
                        fill_positions = []
                        p = last_pos + sample_interval_mm
                        while p <= total_length + 1e-9:
                            fill_positions.append(p)
                            p += sample_interval_mm
                        appended = [(p, last_perim) for p in fill_positions]
                        perimeter_data = perimeter_data + appended
            except Exception as e:
                self.logger.warning(f"尾端补齐失败: {e}")
            
            # 确保从大头开始（周长值大的一端作为首端）
            if len(perimeter_data) > 2:
                perimeter_data = self._ensure_large_end_first(perimeter_data)
            
            # 两端异常值处理：舍去首端和尾端12mm数据，基于20mm趋势外推
            if len(perimeter_data) > 10:
                perimeter_data = self._handle_perimeter_endpoint_anomalies(perimeter_data, 12.0, 20.0)
                 
            
            return {
                'success': True,
                'total_length': total_length,
                'sample_interval': sample_interval_mm,
                'perimeter_data': perimeter_data,
                'sample_count': len(perimeter_data)
            }
            
        except Exception as e:
            self.logger.error(f"获取周长数据失败: {str(e)}")
            return {'error': f'获取周长数据失败: {str(e)}'}
    
    def create_3d_visualization(self, max_faces: int = 15000) -> go.Figure:
        """
        创建3D模型可视化（优化版：自动降采样用于显示）
        
        Args:
            max_faces: 可视化最大面数，默认15000，兼顾显示效果和传输速度
            
        Returns:
            Plotly 3D图表对象
        """
        if self.mesh is None:
            return go.Figure()
        
        try:
            # 降采样网格用于可视化
            viz_mesh = self.mesh
            if len(self.mesh.faces) > max_faces:
                try:
                    viz_mesh = self.mesh.simplify_quadric_decimation(max_faces)
                except Exception:
                    # 如果降采样失败，回退到原始网格
                    viz_mesh = self.mesh

            vertices = viz_mesh.vertices
            faces = viz_mesh.faces
            
            fig = go.Figure(data=[
                go.Mesh3d(
                    x=vertices[:, 0],
                    y=vertices[:, 1],
                    z=vertices[:, 2],
                    i=faces[:, 0],
                    j=faces[:, 1],
                    k=faces[:, 2],
                    opacity=0.8,
                    color='lightblue',
                    name='STL模型'
                )
            ])
            
            # 添加中心线显示
            if self.centerline_analyzer:
                centerline_info = self.centerline_analyzer.get_centerline_info()
                if centerline_info and centerline_info['points'] is not None:
                    centerline_points = centerline_info['points']
                    
                    # 添加中心线
                    fig.add_trace(go.Scatter3d(
                        x=centerline_points[:, 0],
                        y=centerline_points[:, 1],
                        z=centerline_points[:, 2],
                        mode='lines',
                        line=dict(color='orange', width=3),
                        name='中心线',
                        showlegend=True
                    ))
                    
                    # 添加中心线起点和终点标记
                    fig.add_trace(go.Scatter3d(
                        x=[centerline_points[0, 0]],
                        y=[centerline_points[0, 1]],
                        z=[centerline_points[0, 2]],
                        mode='markers',
                        marker=dict(size=8, color='green', symbol='circle'),
                        name='起点',
                        showlegend=True
                    ))
                    
                    fig.add_trace(go.Scatter3d(
                        x=[centerline_points[-1, 0]],
                        y=[centerline_points[-1, 1]],
                        z=[centerline_points[-1, 2]],
                        mode='markers',
                        marker=dict(size=8, color='red', symbol='circle'),
                        name='终点',
                        showlegend=True
                    ))
            
            # 移除坐标轴标记，只显示模型
            # 不再添加穿过模型的坐标轴线
            
            fig.update_layout(
                title='STL模型3D可视化',
                scene=dict(
                    xaxis_title='X轴 (mm)',
                    yaxis_title='Y轴 (mm)',
                    zaxis_title='Z轴 (mm)',
                    aspectmode='data'
                ),
                height=600,
                showlegend=True
            )
            
            return fig
            
        except Exception as e:
            self.logger.error(f"3D可视化生成失败: {str(e)}")
            return go.Figure()
    
    def create_section_visualization(self, position_mm: float) -> go.Figure:
        """
        创建指定位置的截面可视化
        
        Args:
            position_mm: 截面位置（毫米）
            
        Returns:
            Plotly 2D图表对象
        """
        section_data = self.centerline_analyzer.get_cross_section_at_centerline_position(position_mm)
        
        if 'error' in section_data:
            fig = go.Figure()
            fig.add_annotation(
                text=f"错误: {section_data['error']}",
                xref="paper", yref="paper",
                x=0.5, y=0.5,
                showarrow=False
            )
            return fig
        
        # 使用X-Y平面进行2D显示
        vertices_2d = np.array(section_data['vertices'])[:, [0, 1]]  # X-Y平面
        
        # 创建截面图
        fig = go.Figure()
        
        # 绘制截面交点（不显示在图例中）
        fig.add_trace(go.Scatter(
            x=vertices_2d[:, 0],
            y=vertices_2d[:, 1],
            mode='markers',
            marker=dict(size=2, color='blue'),
            name='截面交点',
            fill='toself',
            fillcolor='rgba(0,100,255,0.3)',
            showlegend=False  # 不在图例中显示
        ))
        
        # 添加中心点
        center_2d = np.mean(vertices_2d, axis=0)
        fig.add_trace(go.Scatter(
            x=[center_2d[0]],
            y=[center_2d[1]],
            mode='markers',
            marker=dict(size=10, color='red', symbol='x'),
            name='截面中心'
        ))
        
        # 添加边界框
        bounds_2d = section_data['bounds']
        min_coords = [bounds_2d['min'][0], bounds_2d['min'][1]]  # X, Y最小值
        max_coords = [bounds_2d['max'][0], bounds_2d['max'][1]]  # X, Y最大值
        
        # 绘制边界框
        bbox_x = [min_coords[0], max_coords[0], max_coords[0], min_coords[0], min_coords[0]]
        bbox_y = [min_coords[1], min_coords[1], max_coords[1], max_coords[1], min_coords[1]]
        
        fig.add_trace(go.Scatter(
            x=bbox_x,
            y=bbox_y,
            mode='lines',
            line=dict(color='red', width=2, dash='dash'),
            name='边界框',
            showlegend=True
        ))
        
        # 计算X和Y轴的范围
        x_range = max_coords[0] - min_coords[0]
        y_range = max_coords[1] - min_coords[1]
        
        # 使用相同的比例尺（取较大的范围）
        max_range = max(x_range, y_range)
        
        # 计算新的边界，保持中心点不变
        center_x = (min_coords[0] + max_coords[0]) / 2
        center_y = (min_coords[1] + max_coords[1]) / 2
        
        new_min_x = center_x - max_range / 2
        new_max_x = center_x + max_range / 2
        new_min_y = center_y - max_range / 2
        new_max_y = center_y + max_range / 2
        
        fig.update_layout(
            title=f'截面视图 (中心线位置: {position_mm:.1f}mm)',
            xaxis_title=f'X轴 (mm) - 最大值: {max_coords[0]:.1f}',
            yaxis_title=f'Y轴 (mm) - 最大值: {max_coords[1]:.1f}',
            height=400,
            showlegend=True,
            xaxis=dict(
                range=[new_min_x, new_max_x],
                scaleanchor="y",
                scaleratio=1,
            ),
            yaxis=dict(
                range=[new_min_y, new_max_y],
            )
        )
        
        return fig
    
    def _handle_perimeter_endpoint_anomalies(self, perimeter_data, trim_distance=12.0, trend_distance=20.0):
        """
        处理A方案周长两端异常值（严格端段替换版）：
        - 按“位置距离”定义首尾待替换区间（不是按点数）；
        - 直接舍弃这两段原始值，并用相邻稳定段线性外推补回；
        - 在区间边界加过渡混合，保证曲线连续平滑。
        """
        if len(perimeter_data) < 10:
            return perimeter_data

        positions = np.asarray([item[0] for item in perimeter_data], dtype=float)
        perimeters = np.asarray([item[1] for item in perimeter_data], dtype=float)
        if len(positions) != len(perimeters) or len(positions) < 10:
            return perimeter_data

        # 若位置不严格递增，先按位置排序
        if np.any(np.diff(positions) < 0):
            order = np.argsort(positions)
            positions = positions[order]
            perimeters = perimeters[order]

        start_pos = float(positions[0])
        end_pos = float(positions[-1])
        if end_pos <= start_pos:
            return perimeter_data

        # 位置区间定义
        low_trim_end = start_pos + float(trim_distance)
        high_trim_start = end_pos - float(trim_distance)
        low_fit_start = low_trim_end
        low_fit_end = low_trim_end + float(trend_distance)
        high_fit_start = high_trim_start - float(trend_distance)
        high_fit_end = high_trim_start

        if high_fit_start <= low_fit_end:
            self.logger.warning("端部拟合区间重叠，跳过周长端部异常值处理")
            return perimeter_data

        processed = perimeters.copy()

        try:
            low_fit_mask = (positions >= low_fit_start - 1e-9) & (positions <= low_fit_end + 1e-9)
            high_fit_mask = (positions >= high_fit_start - 1e-9) & (positions <= high_fit_end + 1e-9)
            low_replace_mask = positions <= low_trim_end + 1e-9
            high_replace_mask = positions >= high_trim_start - 1e-9

            if np.count_nonzero(low_fit_mask) < 2 or np.count_nonzero(high_fit_mask) < 2:
                self.logger.warning("端部拟合点不足，跳过周长端部异常值处理")
                return perimeter_data

            # 用稳定段做线性外推
            low_coef = np.polyfit(positions[low_fit_mask], perimeters[low_fit_mask], deg=1)
            high_coef = np.polyfit(positions[high_fit_mask], perimeters[high_fit_mask], deg=1)

            processed[low_replace_mask] = np.polyval(low_coef, positions[low_replace_mask])
            processed[high_replace_mask] = np.polyval(high_coef, positions[high_replace_mask])

            # 边界过渡带：避免替换区和中段之间出现折点
            transition = min(8.0, max(3.0, trim_distance / 2.0))
            low_t0 = low_trim_end
            low_t1 = low_trim_end + transition
            high_t0 = high_trim_start - transition
            high_t1 = high_trim_start

            low_trans_mask = (positions >= low_t0 - 1e-9) & (positions <= low_t1 + 1e-9)
            if np.any(low_trans_mask):
                t = (positions[low_trans_mask] - low_t0) / max(1e-9, (low_t1 - low_t0))
                t = np.clip(t, 0.0, 1.0)
                w = 0.5 * (1.0 + np.cos(np.pi * t))  # 1->0
                fit_vals = np.polyval(low_coef, positions[low_trans_mask])
                orig_vals = perimeters[low_trans_mask]
                processed[low_trans_mask] = w * fit_vals + (1.0 - w) * orig_vals

            high_trans_mask = (positions >= high_t0 - 1e-9) & (positions <= high_t1 + 1e-9)
            if np.any(high_trans_mask):
                t = (positions[high_trans_mask] - high_t0) / max(1e-9, (high_t1 - high_t0))
                t = np.clip(t, 0.0, 1.0)
                w = 0.5 * (1.0 - np.cos(np.pi * t))  # 0->1
                fit_vals = np.polyval(high_coef, positions[high_trans_mask])
                orig_vals = perimeters[high_trans_mask]
                processed[high_trans_mask] = w * fit_vals + (1.0 - w) * orig_vals

            self.logger.info(
                f"A方案周长端部异常值处理完成：替换首尾各{trim_distance:.1f}mm，趋势窗{trend_distance:.1f}mm"
            )
            return [(float(p), float(v)) for p, v in zip(positions, processed)]

        except Exception as e:
            self.logger.warning(f"A方案周长端部异常值处理失败，保留原始数据: {e}")
            return perimeter_data
    
    def _ensure_large_end_first(self, perimeter_data: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        """
        确保周长数据从大头（周长值大的一端）开始
        
        Args:
            perimeter_data: 周长数据列表 [(position, perimeter), ...]
            
        Returns:
            调整后的周长数据列表
        """
        if len(perimeter_data) < 2:
            return perimeter_data
        
        # 获取首端和尾端的周长值
        start_perimeter = perimeter_data[0][1]
        end_perimeter = perimeter_data[-1][1]
        
        # 如果尾端周长大于首端，需要颠倒数据
        if end_perimeter > start_perimeter:
            self.logger.info(f"检测到尾端周长({end_perimeter:.2f}mm)大于首端周长({start_perimeter:.2f}mm)，正在颠倒数据方向")
            
            # 颠倒数据顺序，确保位置从0开始
            reversed_data = []
            start_pos = perimeter_data[0][0]
            end_pos = perimeter_data[-1][0]
            total_length = end_pos - start_pos
            
            for i, (pos, perimeter) in enumerate(perimeter_data):
                # 计算颠倒后的位置，确保从0开始
                new_pos = end_pos - pos
                reversed_data.append((new_pos, perimeter))
            
            # 按位置重新排序
            reversed_data.sort(key=lambda x: x[0])
            
            # 确保位置从0开始
            if reversed_data and reversed_data[0][0] != 0:
                offset = reversed_data[0][0]
                normalized_data = []
                for pos, perimeter in reversed_data:
                    normalized_data.append((pos - offset, perimeter))
                reversed_data = normalized_data
            
            self.logger.info("数据方向已调整，现在从大头开始，位置从0开始")
            return reversed_data
        else:
            self.logger.info(f"首端周长({start_perimeter:.2f}mm)大于尾端周长({end_perimeter:.2f}mm)，数据方向正确")
            return perimeter_data
    
    @staticmethod
    def _parallel_worker_section(args):
        """并行工作函数：分析单个截面数据（静态方法，支持pickle）"""
        i, (pos, perimeter), total_length, centerline_analyzer = args
        try:
            if not centerline_analyzer:
                return None
            
            section_data = centerline_analyzer.get_cross_section_at_centerline_position(pos)
            
            # 计算截面长宽
            if 'bounds' in section_data:
                bounds = section_data['bounds']
                dimensions = bounds['dimensions']
                width = max(dimensions[0], dimensions[1])
                length = min(dimensions[0], dimensions[1])
            else:
                width = length = 0
            
            # 获取X/Y轴长度
            x_length = section_data.get('x_length', 0)
            y_length = section_data.get('y_length', 0)
            
            # 获取3D顶点作为outer_edge
            vertices_3d = section_data.get('vertices', [])
            center_pt = section_data.get('center_point', [])
            
            # 检测打凹
            if_is_daao = section_data.get('if_is_daao', False)
            
            # 构造结果
            return {
                '序号': i + 1,
                '中心线长度(mm)': pos,
                '外径周长(mm)': perimeter,
                '截面宽度(mm)': width,
                '截面长度(mm)': length,
                'x_length': x_length,
                'y_length': y_length,
                '位置百分比(%)': (pos / total_length) * 100 if total_length > 0 else 0,
                '是否为打凹管件': if_is_daao,
                'pos': pos,
                'outer_edge': vertices_3d,
                'position': center_pt,
                'daao_detail': {
                    'position': pos,
                    'percentage': (pos / total_length) * 100 if total_length > 0 else 0,
                    'perimeter': perimeter,
                    'width': width,
                    'length': length
                } if if_is_daao else None
            }
        except Exception as e:
            logging.getLogger(__name__).warning(f"位置 {pos} 截面分析失败: {e}")
            return None
    
    def analyze_stl_comprehensive(self, stl_path: str, sample_interval_mm: float = 1.0, progress_callback=None) -> Dict:
        """
        综合STL分析：包含网格细分、中心线提取、异常值处理、周长数据生成
        
        Args:
            stl_path: STL文件路径
            sample_interval_mm: 周长数据采样间隔（毫米）
            progress_callback: 进度回调函数 func(progress, message)
            
        Returns:
            包含完整分析结果的字典
        """
        try:
            t0 = time.time()
            
            # Define sub-progress callback for load_stl
            load_cb = None
            if progress_callback:
                progress_callback(0, "开始综合分析...")
                def load_cb(p, m):
                    # Map 0-100 to 0-40 (Loading is ~40% of work)
                    progress_callback(p * 0.4, f"预处理: {m}")

            # 1. 加载STL文件（包含网格细分）
            if not self.load_stl(stl_path, progress_callback=load_cb):
                return {'error': 'STL文件加载失败'}
            print(f"stl_analyzer STL文件加载耗时: {time.time() - t0:.4f}秒") # 66s 最耗时
            
            if progress_callback:
                progress_callback(40, "正在获取模型基本信息...")

            t0 = time.time()
            # 2. 获取模型基本信息
            model_info = self.get_model_info()
            print(f"stl_analyzer 模型基本信息获取耗时: {time.time() - t0:.4f}秒")
            
            if progress_callback:
                progress_callback(42, "正在生成周长数据...")

            # Define sub-progress callback for perimeter data
            perimeter_cb = None
            if progress_callback:
                def perimeter_cb(p, m):
                    # Map 0-100 to 42-60
                    progress_callback(42 + p * 0.18, f"周长分析: {m}")

            t0 = time.time()
            # 3. 生成周长数据（包含异常值处理）
            perimeter_result = self.get_perimeter_data_along_centerline(sample_interval_mm, progress_callback=perimeter_cb)
            if not perimeter_result.get('success', False):
                return {'error': f"周长数据生成失败: {perimeter_result.get('error', '未知错误')}"}
            print(f"stl_analyzer 周长数据生成耗时: {time.time() - t0:.4f}秒")
            
            if progress_callback:
                progress_callback(60, "正在分析截面数据...")

            t0 = time.time()
            # 4. 创建周长数据表
            perimeter_data = perimeter_result['perimeter_data']
            
            # 确保从大头开始
            if len(perimeter_data) > 2:
                perimeter_data = self._ensure_large_end_first(perimeter_data)
            
            df_data = []
            total_length = model_info.get('total_length', 0)
            
            # -------------------------- 并行处理核心 --------------------------
            # 准备并行任务参数（包含所有需要的信息）
            task_args = [
                (i, item, total_length, self.centerline_analyzer)
                for i, item in enumerate(perimeter_data)
            ]
            
            # 进程池并行处理
            max_workers = multiprocessing.cpu_count()
            section_results = []
            
            total_tasks = len(task_args)
            completed_count = 0
            
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                # 提交任务（使用静态方法）
                future_to_task = {
                    executor.submit(self._parallel_worker_section, args): args 
                    for args in task_args
                }
                
                # 设置position=0和leave=True确保进度条保留在屏幕上
                with tqdm(total=total_tasks, desc="stl_analyzer 并行分析截面数据", position=0, leave=True) as pbar:
                    for future in as_completed(future_to_task):
                        result = future.result()
                        if result is not None:
                            section_results.append(result)
                        pbar.update(1)
                        completed_count += 1
                        
                        if progress_callback and completed_count % 10 == 0:
                            # Map to 60-95%
                            current_pct = (completed_count / total_tasks)
                            total_progress = 60 + current_pct * 35
                            progress_callback(total_progress, f"截面分析: {completed_count}/{total_tasks}")

            # 处理结果：排序+检测打凹
            if progress_callback:
                progress_callback(95, "正在进行弯曲检测和结果整理...")
                
            section_results.sort(key=lambda x: x['pos'])  # 按位置排序
            for result in section_results:
                df_data.append({
                    '序号': result['序号'],
                    '中心线长度(mm)': result['中心线长度(mm)'],
                    '外径周长(mm)': result['外径周长(mm)'],
                    '截面宽度(mm)': result['截面宽度(mm)'],
                    '截面长度(mm)': result['截面长度(mm)'],
                    'x_length': result['x_length'],
                    'y_length': result['y_length'],
                    '位置百分比(%)': result['位置百分比(%)'],
                })
            
            # -------------------------- 并行处理结束 --------------------------
            
            print(f"stl_analyzer 截面数据生成耗时: {time.time() - t0:.4f}秒")
            
            t0 = time.time()
            # 5. 弯曲检测
            bend_angle = 0
            is_bended = False
            if self.centerline_analyzer:
                centerline_info = self.centerline_analyzer.get_centerline_info()
                if centerline_info and centerline_info.get('points') is not None:
                    points = centerline_info['points']
                    if len(points) > 10:
                        # 计算起始和结束向量的夹角
                        start_vector = points[len(points)//4] - points[0]
                        end_vector = points[-1] - points[-len(points)//4]
                        
                        # 归一化向量
                        start_vector = start_vector / np.linalg.norm(start_vector)
                        end_vector = end_vector / np.linalg.norm(end_vector)
                        
                        # 计算夹角
                        dot_product = np.dot(start_vector, end_vector)
                        angle = np.arccos(np.clip(dot_product, -1.0, 1.0))
                        bend_angle = np.degrees(angle)
                        is_bended = bend_angle > 2.0  # 2度阈值
            print(f"stl_analyzer 弯曲检测耗时: {time.time() - t0:.4f}秒") # 1s 第四耗时
            
            # 6. 组装完整结果
            results = {
                'success': True,
                'model_info': model_info,
                'centerline_length': model_info.get('total_length', 0),
                'volume': model_info.get('volume', 0),
                'processed_mesh': self.mesh,
                'centerline_perimeter_data': df_data,
                'perimeter_result': perimeter_result,
                'bend_angle': bend_angle,
                'is_bended': is_bended,
                'subdivision_info': {
                    'subdivision_level': self.subdivision_level,
                    'original_vertices': len(self.original_mesh.vertices) if self.original_mesh else 0,
                    'final_vertices': len(self.mesh.vertices),
                    'precision_improvement': len(self.mesh.vertices) / len(self.original_mesh.vertices) if self.original_mesh else 1.0
                },
                'analysis_metadata': {
                    'sample_interval_mm': sample_interval_mm,
                    'total_samples': len(perimeter_data),
                    'file_hash': self.current_file_hash,
                    'analysis_timestamp': pd.Timestamp.now().isoformat(),
                    'discarded_start_length': self.centerline_analyzer.get_centerline_info().get('discarded_start_length', 0.0),
                    'discarded_end_length': self.centerline_analyzer.get_centerline_info().get('discarded_end_length', 0.0)
                }
            }
            
            self.logger.info(f"综合STL分析完成: {len(df_data)}个采样点, 中心线长度: {results['centerline_length']:.1f}mm")
            return results
            
        except Exception as e:
            self.logger.error(f"综合STL分析失败: {str(e)}")
            return {'error': f'分析过程中发生错误: {str(e)}'}
    
    def create_perimeter_visualization(self, df_data: List[Dict], title: str = "周长变化曲线") -> go.Figure:
        """
        创建周长数据可视化图表
        
        Args:
            df_data: 周长数据列表
            title: 图表标题
            
        Returns:
            Plotly图表对象
        """
        if not df_data:
            return go.Figure()
        
        # 提取数据
        positions = [item['中心线长度(mm)'] for item in df_data]
        perimeters = [item['外径周长(mm)'] for item in df_data]
        
        # 创建图表
        fig = go.Figure()
        
        # 添加周长数据线（优化：纯线条，不使用marker减少渲染开销）
        fig.add_trace(go.Scatter(
            x=positions,
            y=perimeters,
            mode='lines',
            name='处理后的周长',
            line=dict(color='blue', width=2, shape='spline'),
            hovertemplate='<b>位置:</b> %{x:.1f} mm<br>' +
                         '<b>周长:</b> %{y:.2f} mm<br>' +
                         '<extra></extra>'
        ))
        
        # 添加平均线
        avg_line = np.mean(perimeters)
        fig.add_hline(
            y=avg_line, 
            line_dash="dash", 
            line_color="red",
            annotation_text=f"平均值: {avg_line:.2f}mm"
        )
        
        # 设置布局
        fig.update_layout(
            title=f"{title} (5mm采样, 已处理两端20mm异常值)",
            xaxis_title="中心线位置 (mm)",
            yaxis_title="截面周长 (mm)",
            height=400,
            hovermode='x unified',
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
            margin=dict(t=70, b=50, l=50, r=50)
        )
        
        # 添加网格和设置Y轴范围
        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='lightgray')
        y_min = max(100, min(perimeters) - 10)  # 最小不低于100mm
        y_max = max(perimeters) + 10
        
        fig.update_yaxes(
            showgrid=True, 
            gridwidth=1, 
            gridcolor='lightgray',
            range=[y_min, y_max],
            dtick=50,
            tickmode='linear'
        )
        
        return fig
    
    def get_analysis_summary(self) -> Dict:
        """
        获取分析结果摘要
        
        Returns:
            分析摘要字典
        """
        if not self.mesh or not self.centerline_analyzer:
            return {'error': '分析器未初始化'}
        
        try:
            # 获取基本信息
            model_info = self.get_model_info()
            centerline_info = self.centerline_analyzer.get_centerline_info()
            
            summary = {
                'file_info': {
                    'file_hash': self.current_file_hash,
                    'subdivision_level': self.subdivision_level,
                    'original_vertices': len(self.original_mesh.vertices) if self.original_mesh else 0,
                    'final_vertices': len(self.mesh.vertices),
                    'precision_improvement': len(self.mesh.vertices) / len(self.original_mesh.vertices) if self.original_mesh else 1.0
                },
                'model_info': {
                    'total_length': model_info.get('total_length', 0),
                    'volume': model_info.get('volume', 0),
                    'surface_area': model_info.get('surface_area', 0),
                    'bounds': model_info.get('bounds', {}),
                    'center_mass': model_info.get('center_mass', [0, 0, 0])
                },
                'centerline_info': centerline_info,
                'analysis_capabilities': {
                    'mesh_subdivision': True,
                    'pca_axis_analysis': True,
                    'convex_hull_centroid': True,
                    'endpoint_anomaly_handling': True,
                    'perimeter_data_generation': True,
                    '3d_visualization': True,
                    'section_analysis': True
                }
            }
            
            return summary
            
        except Exception as e:
            self.logger.error(f"获取分析摘要失败: {str(e)}")
            return {'error': f'获取摘要失败: {str(e)}'}

# 测试代码（可选）
if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 创建分析器实例
    analyzer = STLAnalyzer(subdivision_level=2)
    
    # 测试STL文件路径（请替换为实际路径）
    stl_path = "test.stl"
    
    if os.path.exists(stl_path):
        # 执行综合分析
        results = analyzer.analyze_stl_comprehensive(stl_path, sample_interval_mm=5.0)
        
        if results.get('success', False):
            print(f"分析成功！")
            print(f"中心线长度: {results['centerline_length']:.2f}mm")
            print(f"采样点数: {len(results['centerline_perimeter_data'])}")
            print(f"是否弯曲: {results['is_bended']} (弯曲角度: {results['bend_angle']:.2f}°)")
            print(f"是否打凹管件: {results['is_tube_daao']}")
            
            # 生成可视化
            fig_3d = analyzer.create_3d_visualization()
            fig_perimeter = analyzer.create_perimeter_visualization(results['centerline_perimeter_data'])
            
            # 显示图表
            fig_3d.show()
            fig_perimeter.show()
        else:
            print(f"分析失败: {results.get('error', '未知错误')}")
    else:
        print(f"STL文件不存在: {stl_path}")