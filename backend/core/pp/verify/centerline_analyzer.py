import numpy as np
import trimesh
import logging
from scipy.interpolate import splprep, splev
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import ConvexHull
from sklearn.decomposition import PCA
from tqdm import tqdm
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from functools import partial

# 设置日志格式
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 全局变量用于在子进程中存储mesh数据（避免重复加载）
global_mesh_data = None

def init_worker(mesh_vertices, mesh_faces):
    """
    初始化工作进程，将mesh数据加载到全局变量
    """
    global global_mesh_data
    # 重建trimesh对象
    global_mesh_data = trimesh.Trimesh(vertices=mesh_vertices, faces=mesh_faces)

def process_single_section(args, principal_axis, axis_origin, max_inward_search=8.0, step=0.5):
    """
    处理单个截面的计算（用于多进程）
    
    Args:
        args: 包含proj_pos的元组
        principal_axis: PCA主轴方向向量
        axis_origin: 主轴起点
        max_inward_search: 最大向内搜索距离
        step: 搜索步长
        
    Returns:
        tuple: (proj_pos, centroid, quality, valid) 或 (proj_pos, None, None, False)
    """
    proj_pos = args[0]
    try:
        # 计算当前切片平面的位置
        plane_origin = axis_origin + proj_pos * principal_axis
        
        # 获取截面
        section = _get_valid_section_worker(principal_axis, plane_origin, max_inward_search, step)
        
        if section is not None and hasattr(section, 'vertices') and len(section.vertices) > 5:
            # 评估截面质量
            quality = _evaluate_section_quality_worker(section, principal_axis, plane_origin)
            
            # 计算几何中心
            centroid = _compute_section_centroid_with_convex_hull_worker(
                section.vertices, principal_axis, plane_origin
            )
            
            if centroid is not None:
                return (proj_pos, centroid, quality, True)
    except Exception as e:
        logger.warning(f"主轴位置={proj_pos:.2f}mm处截面计算失败: {e}")
    
    return (proj_pos, None, None, False)

def _get_valid_section_worker(plane_normal, plane_origin, max_inward_search=8.0, step=0.5):
    """
    工作进程中的截面获取函数
    """
    global global_mesh_data
    if global_mesh_data is None:
        return None
    
    try:
        # 归一化法向
        n = plane_normal / (np.linalg.norm(plane_normal) + 1e-12)
    except Exception:
        n = plane_normal

    # 优先尝试当前截面
    section = global_mesh_data.section(plane_normal=n, plane_origin=plane_origin)
    if _section_is_valid_worker(section):
        return section

    # 向内搜索（只朝正向内侧移动，避免越界震荡）
    distance = step
    while distance <= max_inward_search:
        try:
            candidate_origin = plane_origin + n * distance
            sec = global_mesh_data.section(plane_normal=n, plane_origin=candidate_origin)
            if _section_is_valid_worker(sec):
                return sec
        except Exception:
            pass
        distance += step

    return section

def _section_is_valid_worker(section, min_vertices=20, min_area=1e-2):
    """
    工作进程中的截面有效性判断函数
    """
    try:
        if section is None:
            return False
        if not hasattr(section, 'vertices') or len(section.vertices) < min_vertices:
            return False

        # 投影到2D平面，检查是否存在闭合多边形
        planar = section.to_planar()
        polygons = getattr(planar, 'polygons_full', [])
        if polygons is None or len(polygons) == 0:
            return False

        # 估算面积：优先用凸包面积做快速下界过滤
        pts2d = planar.vertices
        if pts2d is None or len(pts2d) < 3:
            return False
        try:
            hull = ConvexHull(pts2d)
            area = hull.volume  # Path2D中volume表示面积
        except Exception:
            area = 0.0
        if area < min_area:
            return False

        return True
    except Exception:
        return False

def _evaluate_section_quality_worker(section, principal_axis, plane_origin):
    """
    工作进程中的截面质量评估函数
    """
    try:
        if section is None or not hasattr(section, 'vertices') or len(section.vertices) < 3:
            return {'score': 0.0, 'area': 0.0, 'perimeter': 0.0, 'is_closed': False, 'vertex_count': 0}
        
        # 投影到2D平面
        relative_points = section.vertices - plane_origin
        
        # 构建垂直于主轴的正交坐标系
        if abs(principal_axis[0]) < 0.9:
            u1 = np.array([1, 0, 0])
        elif abs(principal_axis[1]) < 0.9:
            u1 = np.array([0, 1, 0])
        else:
            u1 = np.array([0, 0, 1])
        
        u1 = u1 - np.dot(u1, principal_axis) * principal_axis
        u1 = u1 / np.linalg.norm(u1)
        u2 = np.cross(principal_axis, u1)
        u2 = u2 / np.linalg.norm(u2)
        
        # 投影到2D平面
        points_2d = np.column_stack([
            np.dot(relative_points, u1),
            np.dot(relative_points, u2)
        ])
        
        # 计算凸包
        hull = ConvexHull(points_2d)
        hull_area = hull.volume  # 2D中volume是面积
        hull_perimeter = hull.area  # 2D中area是周长
        
        # 检查闭合性
        is_closed = True
        try:
            planar = section.to_planar()
            polygons = getattr(planar, 'polygons_full', [])
            is_closed = polygons is not None and len(polygons) > 0
        except Exception:
            is_closed = False
        
        # 计算对称性指标（基于点到中心的距离分布）
        center_2d = np.mean(points_2d, axis=0)
        distances = np.linalg.norm(points_2d - center_2d, axis=1)
        distance_std = np.std(distances)
        distance_mean = np.mean(distances)
        symmetry_score = 1.0 / (1.0 + distance_std / (distance_mean + 1e-6))
        
        # 综合质量评分
        area_score = min(1.0, hull_area / 100.0)  # 面积越大越好，100mm²为满分
        perimeter_score = min(1.0, hull_perimeter / 50.0)  # 周长适中，50mm为满分
        closure_score = 1.0 if is_closed else 0.3
        vertex_score = min(1.0, len(section.vertices) / 50.0)  # 点数越多越好，50个为满分
        
        # 加权综合评分
        total_score = (area_score * 0.3 + perimeter_score * 0.2 + 
                      closure_score * 0.3 + symmetry_score * 0.1 + vertex_score * 0.1)
        
        return {
            'score': total_score,
            'area': hull_area,
            'perimeter': hull_perimeter,
            'is_closed': is_closed,
            'vertex_count': len(section.vertices),
            'symmetry_score': symmetry_score,
            'area_score': area_score,
            'perimeter_score': perimeter_score,
            'closure_score': closure_score,
            'vertex_score': vertex_score
        }
        
    except Exception as e:
        logger.warning(f"截面质量评估失败: {e}")
        return {'score': 0.0, 'area': 0.0, 'perimeter': 0.0, 'is_closed': False, 'vertex_count': 0}

def _compute_section_centroid_with_convex_hull_worker(section_vertices, principal_axis, plane_origin):
    """
    工作进程中的截面质心计算函数
    """
    if len(section_vertices) < 3:
        return None
    
    try:
        # 将截面点投影到垂直于主轴的2D平面
        relative_points = section_vertices - plane_origin
        
        # 构建垂直于主轴的正交坐标系
        if abs(principal_axis[0]) < 0.9:
            u1 = np.array([1, 0, 0])
        elif abs(principal_axis[1]) < 0.9:
            u1 = np.array([0, 1, 0])
        else:
            u1 = np.array([0, 0, 1])
        
        # 使用Gram-Schmidt正交化
        u1 = u1 - np.dot(u1, principal_axis) * principal_axis
        u1 = u1 / np.linalg.norm(u1)
        
        u2 = np.cross(principal_axis, u1)
        u2 = u2 / np.linalg.norm(u2)
        
        # 投影到2D平面
        points_2d = np.column_stack([
            np.dot(relative_points, u1),
            np.dot(relative_points, u2)
        ])
        
        # 计算2D凸包
        if len(points_2d) >= 3:
            hull = ConvexHull(points_2d)
            hull_points = points_2d[hull.vertices]
            
            # 方法1：计算凸包的真正几何中心（面积加权）
            hull_centroid_2d = _compute_polygon_centroid_worker(hull_points)
            
            # 方法2：如果凸包太小，使用加权质心
            if len(hull_points) < 6:  # 凸包顶点太少
                # 计算所有点到凸包中心的距离权重
                center_approx = np.mean(points_2d, axis=0)
                distances = np.linalg.norm(points_2d - center_approx, axis=1)
                weights = 1 / (distances + 1e-6)
                weights = weights / np.sum(weights)
                hull_centroid_2d = np.average(points_2d, weights=weights, axis=0)
            
            # 将2D中心转换回3D空间
            centroid_3d = plane_origin + hull_centroid_2d[0] * u1 + hull_centroid_2d[1] * u2
            
            return centroid_3d
        else:
            # 如果点数不足，使用简单平均
            return np.mean(section_vertices, axis=0)
            
    except Exception as e:
        logger.warning(f"凸包计算失败: {e}")
        # 回退到简单平均
        return np.mean(section_vertices, axis=0)

def _compute_polygon_centroid_worker(vertices):
    """
    工作进程中的多边形质心计算函数
    """
    if len(vertices) < 3:
        return np.mean(vertices, axis=0)
    
    # 使用鞋带公式计算多边形面积和质心
    n = len(vertices)
    area = 0.0
    cx = 0.0
    cy = 0.0
    
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i, 0] * vertices[j, 1] - vertices[j, 0] * vertices[i, 1]
        area += cross
        cx += (vertices[i, 0] + vertices[j, 0]) * cross
        cy += (vertices[i, 1] + vertices[j, 1]) * cross
    
    area *= 0.5
    if abs(area) < 1e-10:  # 避免除零
        return np.mean(vertices, axis=0)
    
    cx /= (6.0 * area)
    cy /= (6.0 * area)
    
    return np.array([cx, cy])

class CenterlineAnalyzer:
    """
    中心线分析器 - 用于提取和平滑管件的中心线
    基于thickness_analysis.py中的AxisSectionProcessor类
    """
    
    def __init__(self, mesh, delta=1.0, max_workers=None):
        """
        初始化中心线分析器
        
        Args:
            mesh: trimesh网格对象
            delta: 切片间距(mm)
            max_workers: 最大进程数，None表示自动根据CPU核心数确定
        """
        self.mesh = mesh
        self.delta = delta
        self.max_workers = multiprocessing.cpu_count()
        self.centerline_points = None
        self.smoothed_centerline = None
        self.centerline_length = None
        self.principal_axis = None  # PCA主方向
        self.axis_origin = None     # 主轴起点
        self.discarded_start_length = 0.0  # 首端被截断的等效中心线长度（用于补偿）
        self.discarded_end_length = 0.0    # 尾端被截断的等效中心线长度（用于补偿）
        self.first_inward_shift = 0.0      # 首个有效截面相对原位向内搜索的位移（mm）
        
        # 预处理mesh数据，用于多进程传递
        self.mesh_vertices = mesh.vertices.copy()
        self.mesh_faces = mesh.faces.copy()
        
    def _find_principal_axis_with_pca(self):
        """
        使用PCA主成分分析找到模型的主轴方向
        
        Returns:
            主轴方向向量和起点
        """
        logger.info("使用PCA分析模型主轴方向...")
        
        try:
            # 获取所有顶点
            vertices = self.mesh.vertices
            
            # 计算顶点中心
            centroid = np.mean(vertices, axis=0)
            
            # 将顶点中心化
            centered_vertices = vertices - centroid
            
            # 使用PCA分析
            pca = PCA(n_components=3)
            pca.fit(centered_vertices)
            
            # 获取主成分（按方差大小排序）
            principal_components = pca.components_
            explained_variance = pca.explained_variance_
            
            # 第一主成分是方差最大的方向（主轴方向）
            principal_axis = principal_components[0]
            
            # 确保主轴方向指向Z轴正方向（与原始Z轴夹角小于90度）
            if principal_axis[2] < 0:
                principal_axis = -principal_axis
            
            # 计算主轴在模型中的范围
            projections = np.dot(centered_vertices, principal_axis)
            min_proj = np.min(projections)
            max_proj = np.max(projections)
            
            # 主轴起点（在主轴上的最小投影点）
            axis_origin = centroid + min_proj * principal_axis
            
            self.principal_axis = principal_axis
            self.axis_origin = axis_origin
            
            logger.info(f"PCA主轴方向: {principal_axis}")
            logger.info(f"主轴起点: {axis_origin}")
            logger.info(f"主轴长度: {max_proj - min_proj:.2f}mm")
            logger.info(f"主成分方差比: {explained_variance[0]/np.sum(explained_variance):.3f}")
            
            return principal_axis, axis_origin
            
        except Exception as e:
            logger.error(f"PCA分析失败: {e}")
            # 回退到Z轴方向
            logger.warning("回退到Z轴方向")
            self.principal_axis = np.array([0, 0, 1])
            self.axis_origin = np.array([0, 0, np.min(self.mesh.vertices[:, 2])])
            return self.principal_axis, self.axis_origin
        
    def extract_centerline(self):
        """
        提取中心线点
        
        Returns:
            中心线点数组
        """
        logger.info("开始提取中心线...")

        # A方案改为“轴向假设 + Z向切片”：不再使用PCA主轴
        bounds = self.mesh.bounds
        z_min = float(bounds[0][2])
        z_max = float(bounds[1][2])
        if not np.isfinite(z_min) or not np.isfinite(z_max) or z_max <= z_min:
            logger.error("模型Z向范围异常，无法提取中心线")
            return None

        step = float(self.delta) if self.delta and self.delta > 0 else 1.0
        slide_list = np.arange(z_min, z_max + step * 0.5, step, dtype=float)
        logger.info(f"将沿Z轴生成 {len(slide_list)} 个切片，间隔为 {step}mm")

        # 兼容历史字段
        self.principal_axis = np.array([0.0, 0.0, 1.0], dtype=float)
        self.axis_origin = np.array([0.0, 0.0, z_min], dtype=float)

        centers, section_qualities, center_positions = self._get_cross_section_centers_along_z_axis(slide_list)
        logger.info(f"成功计算 {len(centers)} 个有效截面的中心点")

        # 保留端部质量截断与异常段处理
        if len(centers) > 10 and len(section_qualities) > 10:
            centers, section_qualities, center_positions = self._adaptive_endpoint_truncation(
                centers, section_qualities, slide_list, center_positions
            )

        if len(centers) > 10:
            positions_for_anomaly = center_positions if len(center_positions) == len(centers) else slide_list
            if len(positions_for_anomaly) == len(centers):
                centers = self._handle_endpoint_anomalies(centers, positions_for_anomaly, trim_distance=10.0, trend_distance=10.0)
            else:
                logger.warning("中心点与位置列表长度不匹配，跳过异常值处理")

        if len(centers) > 0:
            self.centerline_points = np.array(centers, dtype=float)
            logger.info(f"成功提取 {len(centers)} 个中心线点")
            return self.centerline_points

        logger.error("无法提取中心线点")
        return None

    def _get_cross_section_centers_along_z_axis(self, slide_list):
        """
        沿Z轴(平行XY平面)切片，使用截面凸包几何中心提取中心线点。
        """
        centers = []
        center_positions = []
        section_qualities = []
        previous_centroid = None

        for z in tqdm(slide_list, desc="centerline_analyzer Z向切片提取中心点", unit="截面"):
            try:
                plane_normal = np.array([0.0, 0.0, 1.0], dtype=float)
                plane_origin = np.array([0.0, 0.0, float(z)], dtype=float)
                section = self.mesh.section(plane_normal=plane_normal, plane_origin=plane_origin)
                if section is None or not hasattr(section, 'vertices') or section.vertices is None:
                    continue

                verts = np.asarray(section.vertices)
                if verts.ndim != 2 or verts.shape[1] != 3 or len(verts) < 6:
                    continue

                points_2d = verts[:, :2]
                hull_area = 0.0
                hull_perimeter = 0.0
                try:
                    hull = ConvexHull(points_2d)
                    hull_points = points_2d[hull.vertices]
                    centroid_2d = _compute_polygon_centroid_worker(hull_points)
                    hull_area = float(hull.volume)
                    hull_perimeter = float(hull.area)
                except Exception:
                    centroid_2d = np.mean(points_2d, axis=0)

                centroid = np.array(
                    [float(centroid_2d[0]), float(centroid_2d[1]), float(np.mean(verts[:, 2]))],
                    dtype=float
                )

                # 连续性约束：抑制异常跳点
                if previous_centroid is not None:
                    distance = float(np.linalg.norm(centroid - previous_centroid))
                    max_jump = 2.0
                    if distance > max_jump:
                        direction = centroid - previous_centroid
                        direction = direction / (np.linalg.norm(direction) + 1e-12)
                        centroid = previous_centroid + direction * max_jump

                centers.append(centroid)
                center_positions.append(float(z))
                previous_centroid = centroid

                area_score = min(1.0, max(0.0, hull_area) / 100.0)
                perimeter_score = min(1.0, max(0.0, hull_perimeter) / 50.0)
                vertex_score = min(1.0, len(verts) / 50.0)
                section_qualities.append({
                    'score': area_score * 0.5 + perimeter_score * 0.2 + vertex_score * 0.3,
                    'area': max(0.0, hull_area),
                    'perimeter': max(0.0, hull_perimeter),
                    'is_closed': True,
                    'vertex_count': len(verts)
                })
            except Exception as e:
                logger.debug(f"Z={z:.2f}mm 处截面中心提取失败: {e}")

        return centers, section_qualities, center_positions
    
    def _get_cross_section_centers_along_principal_axis(self, slide_list, principal_axis, axis_origin):
        """
        沿PCA主轴方向计算各个截面的几何中心位置（端部自适应截断 + 截面质量评估）
        使用多进程并行处理最耗时的截面提取步骤
        
        Args:
            slide_list: 沿主轴方向的切片位置列表
            principal_axis: PCA主轴方向向量
            axis_origin: 主轴起点
            
        Returns:
            中心点列表
        """
        centers = []
        center_positions = []  # 记录每个中心点对应的投影位置
        section_qualities = []  # 记录每个截面的质量指标
        valid_sections = 0
        
        # 计算总长度，用于确定异常值处理范围
        total_length = slide_list[-1] - slide_list[0]
        trim_distance = 10.0  # 两端各10mm异常值处理范围
        
        logger.info(f"开始端部自适应截断分析，使用多进程并行处理（{self.max_workers}个进程）...")
        
        # 准备并行处理的任务
        tasks = [(proj_pos,) for proj_pos in slide_list]
        
        # 使用多进程处理截面计算
        results = []
        with ProcessPoolExecutor(
            max_workers=self.max_workers,
            initializer=init_worker,
            initargs=(self.mesh_vertices, self.mesh_faces)
        ) as executor:
            # 提交所有任务
            partial_process = partial(
                process_single_section,
                principal_axis=principal_axis,
                axis_origin=axis_origin,
                max_inward_search=8.0,
                step=0.5
            )
            
            # 进度条显示
            futures = {executor.submit(partial_process, task): task for task in tasks}
            for future in tqdm(as_completed(futures), total=len(futures), desc="centerline_analyzer 并行计算截面中心", unit="截面"):
                result = future.result()
                results.append(result)
        
        # 按投影位置排序结果（确保顺序正确）
        results.sort(key=lambda x: x[0])
        
        # 处理结果
        previous_centroid = None
        first_valid_found = False
        first_valid_shift = 0.0  # 这里无法直接获取搜索位移，使用默认值
        
        for result in results:
            proj_pos, centroid, quality, valid = result
            
            if valid and centroid is not None and quality is not None:
                # 记录首个有效截面
                if not first_valid_found:
                    first_valid_found = True
                    first_valid_shift = 0.0  # 多进程模式下无法获取单个截面的搜索位移
                
                # 连续性约束：如果与上一个中心点距离过大，进行平滑处理
                if previous_centroid is not None:
                    distance = np.linalg.norm(centroid - previous_centroid)
                    max_jump = 2.0  # 最大允许跳跃距离（mm）
                    
                    if distance > max_jump:
                        # 平滑处理：限制跳跃距离
                        direction = centroid - previous_centroid
                        direction = direction / np.linalg.norm(direction)
                        centroid = previous_centroid + direction * max_jump
                        logger.debug(f"主轴位置={proj_pos:.2f}mm处中心点跳跃过大，已平滑处理")
                
                centers.append(centroid)
                center_positions.append(proj_pos)
                section_qualities.append(quality)
                previous_centroid = centroid
                valid_sections += 1
                
                if valid_sections % 50 == 0:
                    logger.info(f"主轴位置={proj_pos:.2f}mm处计算几何中心，质量={quality['score']:.3f}")
        
        logger.info(f"成功计算 {valid_sections} 个有效截面的中心点")
        
        # 端部自适应截断：基于截面质量分析确定有效范围
        if len(centers) > 10 and len(section_qualities) > 10:
            centers, section_qualities, center_positions = self._adaptive_endpoint_truncation(
                centers, section_qualities, slide_list, center_positions
            )
        
        # 处理首个有效截面的位移补偿（多进程模式下无法精确获取，使用默认逻辑）
        try:
            if not np.isnan(first_valid_shift) and first_valid_shift > 0:
                if getattr(self, 'discarded_start_length', 0.0) < first_valid_shift * 0.9:
                    self.discarded_start_length = float(first_valid_shift)
        except Exception:
            pass
        
        # 两端异常值处理：舍去首端和尾端10mm数据，基于10mm趋势外推
        # 注意：必须使用对应的center_positions作为位置参考，否则回归分析会错位
        if len(centers) > 10:  # 确保有足够的数据点
            # 如果center_positions为空（未截断或兼容旧逻辑），使用slide_list（可能有风险）
            positions_for_anomaly = center_positions if len(center_positions) == len(centers) else slide_list
            if len(positions_for_anomaly) != len(centers):
                # 如果长度还不匹配，尝试截取slide_list的子集（假设是连续的）
                # 但这很难保证，所以最好的情况是adaptive_endpoint_truncation返回了正确的positions
                logger.warning("中心点与位置列表长度不匹配，跳过异常值处理")
            else:
                centers = self._handle_endpoint_anomalies(centers, positions_for_anomaly, trim_distance, 10.0)
        
        # 用户反馈：可视化中心线偏短，未延伸到模型端面。
        # 解决方案：基于现有中心线的首尾趋势，将中心线延伸到模型的投影边界(min_proj/max_proj)。
        # 注意：min_proj/max_proj 对应 slide_list 的首尾。
        if len(centers) >= 2:
            # 获取整个模型的投影范围（从slide_list获取）
            # slide_list 是 linspace(min_proj, max_proj, ...)
            # 所以 min_proj = slide_list[0], max_proj = slide_list[-1]
            scan_min_proj = slide_list[0]
            scan_max_proj = slide_list[-1]
            
            # 调用延伸函数
            centers = self._extend_centerline_to_boundaries(centers, principal_axis, axis_origin, scan_min_proj, scan_max_proj)

        return centers

    def _extend_centerline_to_boundaries(self, centers, principal_axis, axis_origin, min_proj, max_proj):
        """
        将中心线延伸到模型的主轴投影边界 (min_proj, max_proj)
        
        Args:
            centers: 当前中心点列表 (list of np.array)
            principal_axis: PCA主轴方向
            axis_origin: 主轴起点
            min_proj: 最小投影值
            max_proj: 最大投影值
            
        Returns:
            延伸后的中心点列表
        """
        try:
            if len(centers) < 2:
                return centers
                
            centers = np.array(centers)
            
            # --- 首端延伸 (Extension at Start) ---
            # 计算首端当前的投影位置
            start_point = centers[0]
            start_proj = np.dot(start_point - axis_origin, principal_axis)
            
            # 如果当前起点在 min_proj 之后（说明被截断了或者没提取全），则向前延伸
            if start_proj > min_proj + 0.1:  # 留点余量
                # 计算延伸方向：使用前几个点的平均方向
                # 取前5个点（或更少）计算方向
                n_trend = min(5, len(centers))
                direction_start = centers[0] - centers[n_trend-1] # 指向外侧
                direction_start = direction_start / (np.linalg.norm(direction_start) + 1e-9)
                
                # 计算需要延伸的距离
                # 这里的距离是沿着切线方向走到 min_proj 所在的垂直平面
                # 目标点 P_target = centers[0] + t * direction_start
                # 投影方程: dot(P_target - axis_origin, principal_axis) = min_proj
                # dot(centers[0] + t*dir - axis_origin, axis) = min_proj
                # start_proj + t * dot(dir, axis) = min_proj
                # t = (min_proj - start_proj) / dot(dir, axis)
                
                dot_val = np.dot(direction_start, principal_axis)
                if abs(dot_val) > 0.1: # 确保切线不垂直于主轴
                    t = (min_proj - start_proj) / dot_val
                    
                    # t 应该是正数（因为 min_proj < start_proj，且 dir 指向负方向，dot_val 应该为负）
                    # 如果 t < 0，说明方向反了或者已经超出了
                    if t > 0:
                        new_start = centers[0] + t * direction_start
                        logger.info(f"中心线首端延伸: {start_proj:.2f} -> {min_proj:.2f} (延伸距离 {t:.2f}mm)")
                        # 插入新起点
                        centers = np.vstack(([new_start], centers))
            
            # --- 尾端延伸 (Extension at End) ---
            # 计算尾端当前的投影位置
            end_point = centers[-1]
            end_proj = np.dot(end_point - axis_origin, principal_axis)
            
            # 如果当前终点在 max_proj 之前，则向后延伸
            if end_proj < max_proj - 0.1:
                # 计算延伸方向
                n_trend = min(5, len(centers))
                direction_end = centers[-1] - centers[-n_trend] # 指向外侧
                direction_end = direction_end / (np.linalg.norm(direction_end) + 1e-9)
                
                # 目标投影位置: max_proj
                # end_proj + t * dot(dir, axis) = max_proj
                # t = (max_proj - end_proj) / dot(dir, axis)
                
                dot_val = np.dot(direction_end, principal_axis)
                if abs(dot_val) > 0.1:
                    t = (max_proj - end_proj) / dot_val
                    
                    if t > 0:
                        new_end = centers[-1] + t * direction_end
                        logger.info(f"中心线尾端延伸: {end_proj:.2f} -> {max_proj:.2f} (延伸距离 {t:.2f}mm)")
                        # 追加新终点
                        centers = np.vstack((centers, [new_end]))
                        
            return centers.tolist()
            
        except Exception as e:
            logger.warning(f"中心线延伸失败: {e}")
            return centers.tolist() if isinstance(centers, np.ndarray) else centers

    def _adaptive_endpoint_truncation(self, centers, section_qualities, slide_list, center_positions=None):
        """
        端部自适应截断：基于截面质量分析，自动截断质量急剧下降的端部区域
        
        Args:
            centers: 中心点列表
            section_qualities: 截面质量列表
            slide_list: 原始完整切片位置列表（用于计算绝对投影距离）
            center_positions: 中心点对应的投影位置列表（可选，若提供则用于更精确的长度补偿）
            
        Returns:
            截断后的中心点、质量列表和位置列表
        """
        if len(centers) < 20 or len(section_qualities) < 20:
            logger.warning("数据点不足，跳过自适应截断")
            return centers, section_qualities, center_positions if center_positions is not None else []
        
        centers = np.array(centers)
        qualities = np.array([q['score'] for q in section_qualities])
        areas = np.array([q['area'] for q in section_qualities])
        
        # 计算质量趋势
        window_size = min(10, len(qualities) // 4)
        if window_size < 3:
            return centers, section_qualities, center_positions if center_positions is not None else []
        
        # 滑动窗口平均质量
        smoothed_qualities = np.convolve(qualities, np.ones(window_size)/window_size, mode='valid')
        smoothed_areas = np.convolve(areas, np.ones(window_size)/window_size, mode='valid')
        
        # 找到质量稳定区域（质量变化率小于阈值）
        quality_gradient = np.gradient(smoothed_qualities)
        area_gradient = np.gradient(smoothed_areas)
        
        # 首端截断：找到第一个质量稳定点
        start_cut_idx = 0
        for i in range(len(quality_gradient) - 5):
            # 检查后续5个点的质量是否稳定
            if (np.std(quality_gradient[i:i+5]) < 0.05 and 
                np.std(area_gradient[i:i+5]) < 5.0 and
                smoothed_qualities[i] > 0.4):
                start_cut_idx = i + window_size // 2
                break
        
        # 尾端截断：从后往前找到第一个质量稳定点
        end_cut_idx = len(centers)
        for i in range(len(quality_gradient) - 1, 5, -1):
            if (np.std(quality_gradient[i-5:i]) < 0.05 and 
                np.std(area_gradient[i-5:i]) < 5.0 and
                smoothed_qualities[i] > 0.4):
                end_cut_idx = i + window_size // 2
                break
        
        # 应用截断
        if start_cut_idx > 0 or end_cut_idx < len(centers):
            original_count = len(centers)
            
            # 计算被截去的首端等效长度
            # 用户最终决定：直接使用可视化中的中心线长度（即截断后的长度），不再进行任何补偿。
            # 原因：扫描件端部的噪点可能导致包围盒(Bounding Box)被拉长，从而使得基于投影的补偿依然偏大。
            # 可视化中的中心线是经过质量筛选保留下来的可靠部分，用户认为其长度准确。
            self.discarded_start_length = 0.0

            # 计算被截去的尾端等效长度
            self.discarded_end_length = 0.0
                
            centers = centers[start_cut_idx:end_cut_idx]
            section_qualities = section_qualities[start_cut_idx:end_cut_idx]
            if center_positions is not None:
                center_positions = center_positions[start_cut_idx:end_cut_idx]
            
            logger.info(f"端部自适应截断：从{original_count}个点截断到{len(centers)}个点")
            logger.info(f"首端截断{start_cut_idx}个点，尾端截断{original_count-end_cut_idx}个点")
            
            if start_cut_idx > 0:
                logger.info(f"首端截断原因：前{start_cut_idx}个截面质量不稳定（面积急剧缩小）")
            if end_cut_idx < original_count:
                logger.info(f"尾端截断原因：后{original_count-end_cut_idx}个截面质量不稳定（面积急剧缩小）")
        
        return centers.tolist(), section_qualities, center_positions
    
    def _get_cross_section_centers(self, slide_list):
        """
        计算各个截面的几何中心位置
        
        Args:
            slide_list: Z轴切片位置列表
            
        Returns:
            中心点列表
        """
        centers = []
        valid_sections = 0
        
        for z in tqdm(slide_list, desc="计算各个截面的几何中心位置", unit="截面"):
            try:
                # 创建截面平面（垂直于Z轴）
                plane_normal = np.array([0, 0, 1])
                plane_origin = np.array([0, 0, z])
                
                # 获取截面
                section = self.mesh.section(plane_normal=plane_normal, plane_origin=plane_origin)
                
                if section is not None and len(section.vertices) > 5:
                    # 计算截面的几何中心
                    vertices_2d = section.vertices[:, [0, 1]]  # X-Y平面
                    centroid = self._compute_section_centroid(vertices_2d)
                    
                    # 组合XY质心和Z坐标
                    center = np.array([centroid[0], centroid[1], z])
                    centers.append(center)
                    valid_sections += 1
                    
                    if valid_sections % 50 == 0:
                        logger.info(f"Z={z:.2f}mm处计算几何中心")
                        
            except Exception as e:
                logger.warning(f"Z={z:.2f}mm处截面计算失败: {e}")
                continue
        
        logger.info(f"成功计算 {valid_sections} 个有效截面的中心点")
        return centers
    
    def _compute_section_centroid(self, points_2d):
        """
        计算2D点集的质心
        
        Args:
            points_2d: 2D点数组
            
        Returns:
            质心坐标
        """
        if len(points_2d) == 0:
            return np.array([0, 0])
        
        # 使用简单平均作为质心
        centroid = np.mean(points_2d, axis=0)
        return centroid
    
    def _handle_endpoint_anomalies(self, centers, slide_list, trim_distance=5.0, trend_distance=10.0):
        """
        处理两端异常值：基于趋势外推重新生成首尾5mm中心线数据
        
        处理逻辑：
        1. 舍弃首端0-5mm和尾端最后5mm的中心线数据
        2. 首端：基于6-15mm范围的数据拟合趋势，外推到0-5mm
        3. 尾端：基于对应10mm范围的数据拟合趋势，外推到原尾端5mm范围
        
        Args:
            centers: 中心点列表
            slide_list: 切片位置列表
            trim_distance: 两端异常值处理距离（mm），默认5mm
            trend_distance: 用于拟合趋势的数据范围（mm），默认10mm
            
        Returns:
            处理后的中心点列表
        """
        if len(centers) < 10:  # 需要足够的数据点
            return centers
        
        centers = np.array(centers)
        slide_list = np.array(slide_list)
        
        # 计算实际采样间隔
        if len(slide_list) > 1:
            actual_sample_interval = slide_list[1] - slide_list[0]
        else:
            actual_sample_interval = 1.0  # 默认1mm间隔
        
        # 计算需要处理的点数
        trim_points_start = max(1, int(trim_distance / actual_sample_interval))
        trim_points_end = max(1, int(trim_distance / actual_sample_interval))
        trend_points = max(1, int(trend_distance / actual_sample_interval))
        
        # 确保有足够的数据进行趋势拟合
        if len(centers) < trim_points_start + trim_points_end + trend_points:
            logger.warning("数据点不足，跳过异常值处理")
            return centers
        
        logger.info(f"处理中心线两端异常值：舍弃首端{trim_points_start}个点({trim_distance:.1f}mm)，尾端{trim_points_end}个点({trim_distance:.1f}mm)，基于{trend_points}个点({trend_distance:.1f}mm)拟合趋势")
        
        # 计算趋势拟合区域
        # 首端趋势区域：从trim_distance到trim_distance+trend_distance
        start_trend_start = trim_points_start
        start_trend_end = trim_points_start + trend_points
        
        # 尾端趋势区域：从总长度-trim_distance-trend_distance到总长度-trim_distance
        end_trend_start = len(centers) - trim_points_end - trend_points
        end_trend_end = len(centers) - trim_points_end
        
        # 确保趋势区域有效
        if start_trend_end >= end_trend_start:
            logger.warning("趋势拟合区域重叠，跳过异常值处理")
            return centers
        
        # 获取趋势拟合数据
        start_trend_centers = centers[start_trend_start:start_trend_end]
        start_trend_positions = slide_list[start_trend_start:start_trend_end]
        end_trend_centers = centers[end_trend_start:end_trend_end]
        end_trend_positions = slide_list[end_trend_start:end_trend_end]
        
        # 对每个坐标分量分别进行线性插值和外推
        processed_centers = centers.copy()
        
        for coord_idx in range(3):  # X, Y, Z坐标
            # 首端线性拟合和外推
            if len(start_trend_centers) >= 2:
                start_trend_values = start_trend_centers[:, coord_idx]
                
                try:
                    from scipy import stats
                    
                    # 使用线性回归拟合首端趋势
                    start_slope, start_intercept, _, _, _ = stats.linregress(start_trend_positions, start_trend_values)
                    
                    # 首端外推：从0到trim_distance
                    for i in range(trim_points_start):
                        original_position = slide_list[i]
                        # 基于6-15mm范围的线性趋势外推到0-5mm
                        extrapolated_value = start_slope * original_position + start_intercept
                        processed_centers[i, coord_idx] = extrapolated_value
                
                except Exception as e:
                    logger.warning(f"首端线性插值失败: {e}")
                    # 如果线性插值失败，使用首端值
                    for i in range(trim_points_start):
                        processed_centers[i, coord_idx] = start_trend_values[0]
            
            # 尾端线性拟合和外推
            if len(end_trend_centers) >= 2:
                end_trend_values = end_trend_centers[:, coord_idx]
                
                try:
                    from scipy import stats
                    
                    # 使用线性回归拟合尾端趋势
                    end_slope, end_intercept, _, _, _ = stats.linregress(end_trend_positions, end_trend_values)
                    
                    # 尾端外推：从end_trim_start到总长度
                    for i in range(len(centers) - trim_points_end, len(centers)):
                        original_position = slide_list[i]
                        # 基于对应10mm范围的线性趋势外推
                        extrapolated_value = end_slope * original_position + end_intercept
                        processed_centers[i, coord_idx] = extrapolated_value
                
                except Exception as e:
                    logger.warning(f"尾端线性插值失败: {e}")
                    # 如果线性插值失败，使用尾端值
                    for i in range(len(centers) - trim_points_end, len(centers)):
                        processed_centers[i, coord_idx] = end_trend_values[-1]
        
        logger.info("中心线两端异常值处理完成，基于线性插值重新生成首尾5mm数据")
        return processed_centers.tolist()
    
    def smooth_centerline(self, points=None, window_size=15):
        """
        对中心线进行平滑处理
        
        Args:
            points: 中心线点数组，如果为None则使用已提取的点
            window_size: 平滑窗口大小
            
        Returns:
            平滑后的中心线点数组
        """
        if points is None:
            points = self.centerline_points
            
        if points is None or len(points) <= 3:
            logger.warning("中心线点不足，无法进行平滑")
            return points
        
        logger.info(f"开始中心线平滑处理，共{len(points)}个点，窗口大小: {window_size}")
        
        n_points = len(points)
        # 如果点数小于窗口大小，减小窗口
        if n_points < window_size:
            window_size = max(3, n_points // 2)
            logger.info(f"点数较少，自动调整窗口大小为: {window_size}")
        
        # 创建平滑后的点集副本
        smoothed_points = np.copy(points)
        
        # 1. 高斯权重平滑 - 增强平滑效果
        for i in range(n_points):
            weights = np.zeros(n_points)
            for j in range(n_points):
                # 计算高斯权重，距离越远权重越小
                dist = abs(i - j)
                if dist < window_size:
                    # 使用更强的高斯权重，增强平滑效果
                    weights[j] = np.exp(-0.3 * (dist / (window_size / 3)) ** 2)
            
            # 归一化权重
            if np.sum(weights) > 0:
                weights = weights / np.sum(weights)
                # 应用权重，增强平滑效果
                smoothed_points[i] = 0.85 * np.sum(points * weights[:, np.newaxis], axis=0) + 0.15 * points[i]
        
        # 2. 样条拟合平滑
        try:
            # 计算累积距离作为参数化变量
            t = np.zeros(n_points)
            for i in range(1, n_points):
                t[i] = t[i-1] + np.linalg.norm(points[i] - points[i-1])
            
            # 归一化累积距离
            if t[-1] > 0:
                t = t / t[-1]
            
            # 使用样条拟合 - 增强平滑效果
            k = min(3, n_points - 1)  # 样条阶数，最高为3
            s = n_points / 50.0      # 增强平滑因子，减少噪声
            tck, u = splprep([smoothed_points[:, 0], smoothed_points[:, 1], smoothed_points[:, 2]], 
                           u=t, k=k, s=s)
            
            # 在相同参数位置评估样条
            refined_points = np.array(splev(t, tck)).T
            
            # 混合策略：增强样条拟合的影响，提高平滑效果
            alpha = 0.6  # 增加混合权重，增强平滑效果
            for i in range(1, n_points-1):
                # 权重线性变化，中间点更多地使用样条拟合
                edge_weight = 2.0 * min(i, n_points-1-i) / n_points
                blend_weight = alpha * min(1.0, edge_weight)
                
                # 混合原始平滑点和样条拟合点
                smoothed_points[i] = (1 - blend_weight) * smoothed_points[i] + blend_weight * refined_points[i]
            
            logger.info("中心线使用样条拟合进行高级平滑")
        except Exception as e:
            logger.warning(f"样条拟合平滑失败，回退到简单平滑: {e}")
        
        # 注意：端部异常值已在extract_centerline阶段处理过，
        # 这里不再二次替换，避免首尾被过度修正。
        self.smoothed_centerline = smoothed_points
        self.centerline_points = smoothed_points
        return smoothed_points

    def _replace_centerline_endpoint_anomalies_by_points(self, points: np.ndarray, trim_points: int = 10, trend_points: int = 10) -> np.ndarray:
        """
        中心线端部异常段处理（按点数）：
        - 首端/尾端各“舍弃” trim_points 个点（不删除点位，直接用外推值替换其坐标）
        - 首端拟合使用 [trim_points, trim_points+trend_points) 的点
        - 尾端拟合使用 [-trim_points-trend_points, -trim_points) 的点
        - 分别对 X/Y/Z 维度做线性回归，并在端部点对应的弧长参数上外推
        """
        pts = np.asarray(points, dtype=float)
        n = len(pts)
        trim_n = max(1, int(trim_points))
        trend_n = max(2, int(trend_points))
        if n < (trim_n * 2 + trend_n * 2):
            return pts

        # 使用弧长作为自变量，更稳健（不依赖点间距是否均匀）
        t = np.zeros(n, dtype=float)
        for i in range(1, n):
            t[i] = t[i - 1] + float(np.linalg.norm(pts[i] - pts[i - 1]))
        # 防止全零
        if not np.isfinite(t[-1]) or t[-1] <= 1e-12:
            t = np.arange(n, dtype=float)

        s0 = trim_n
        s1 = trim_n + trend_n
        e1 = n - trim_n
        e0 = e1 - trend_n
        if s1 >= e0:
            return pts

        try:
            from scipy import stats
        except Exception:
            return pts

        new_pts = pts.copy()

        # 对每个坐标分量分别拟合
        for coord in range(3):
            y = pts[:, coord]
            try:
                start_slope, start_intercept, *_ = stats.linregress(t[s0:s1], y[s0:s1])
                end_slope, end_intercept, *_ = stats.linregress(t[e0:e1], y[e0:e1])
            except Exception:
                continue

            # 首端外推替换
            for i in range(0, trim_n):
                new_pts[i, coord] = start_slope * t[i] + start_intercept

            # 尾端外推替换
            for i in range(n - trim_n, n):
                new_pts[i, coord] = end_slope * t[i] + end_intercept

        return new_pts
    
    def calculate_centerline_length(self, points=None, use_original=False):
        """
        计算中心线的长度
        
        Args:
            points: 中心线点数组，如果为None则使用平滑后的点
            use_original: 是否使用原始中心线点计算长度（更准确）
            
        Returns:
            中心线总长度
        """
        if points is None:
            if use_original and self.centerline_points is not None:
                points = self.centerline_points
            else:
                points = self.smoothed_centerline
            
        if points is None or len(points) < 2:
            return 0.0
        
        length = 0.0
        for i in range(1, len(points)):
            segment_length = np.linalg.norm(points[i] - points[i-1])
            length += segment_length
        
        self.centerline_length = length
        logger.info(f"中心线长度: {length:.2f} mm")
        return length
    
    def get_centerline_info(self):
        """
        获取中心线信息
        
        Returns:
            中心线信息字典
        """
        if self.smoothed_centerline is None:
            return None
        
        # 计算累积距离
        cumulative_distances = [0.0]
        for i in range(1, len(self.smoothed_centerline)):
            distance = np.linalg.norm(self.smoothed_centerline[i] - self.smoothed_centerline[i-1])
            cumulative_distances.append(cumulative_distances[-1] + distance)
        
        return {
            'points': self.smoothed_centerline,
            'length': self.centerline_length,
            'discarded_start_length': float(getattr(self, 'discarded_start_length', 0.0)),
            'discarded_end_length': float(getattr(self, 'discarded_end_length', 0.0)),
            'length_with_compensation': float(
                self.centerline_length + 
                getattr(self, 'discarded_start_length', 0.0) + 
                getattr(self, 'discarded_end_length', 0.0)
            ),
            'cumulative_distances': cumulative_distances,
            'point_count': len(self.smoothed_centerline)
        }
    
    def get_cross_section_at_centerline_position(self, position_mm):
        """
        在中心线指定位置获取垂直截面
        
        Args:
            position_mm: 沿中心线的位置（毫米）
            
        Returns:
            截面数据字典
        """
        if self.smoothed_centerline is None:
            return {'error': '中心线未初始化'}
        
        centerline_info = self.get_centerline_info()
        cumulative_distances = centerline_info['cumulative_distances']
        
        # 检查位置是否在中心线范围内
        if position_mm < 0 or position_mm > self.centerline_length:
            return {'error': f'位置 {position_mm}mm 超出中心线范围 [0, {self.centerline_length:.1f}]mm'}
        
        # 找到最接近的位置点
        distances = np.array(cumulative_distances)
        idx = np.argmin(np.abs(distances - position_mm))
        
        # 获取该点的位置和方向
        center_point = self.smoothed_centerline[idx]
        
        # 计算切向方向
        if idx == 0:
            # 第一个点，使用下一个点的方向
            direction = self.smoothed_centerline[1] - self.smoothed_centerline[0]
        elif idx == len(self.smoothed_centerline) - 1:
            # 最后一个点，使用前一个点的方向
            direction = self.smoothed_centerline[-1] - self.smoothed_centerline[-2]
        else:
            # 中间点，使用前后点的平均方向
            direction = (self.smoothed_centerline[idx+1] - self.smoothed_centerline[idx-1]) / 2
        
        # 归一化方向向量
        direction = direction / np.linalg.norm(direction)
        
        # 创建垂直于中心线的切割平面
        plane_normal = direction
        plane_origin = center_point
        
        # 获取截面
        section = self.mesh.section(plane_normal=plane_normal, plane_origin=plane_origin)
        
        if section is None or len(section.vertices) == 0:
            return {'error': '无法获取有效截面'}
        
        # 计算截面的边界框
        section_bounds = section.bounds
        section_dimensions = section_bounds[1] - section_bounds[0]

        def identify_inner_outer_walls_improved(points_2d, shrink_ratio=0.99):
            """从识别内外壁点算法用来判定是否是打凹管件"""
            try:
                # 1. 计算形心
                centroid = np.mean(points_2d, axis=0)
                
                # 2. 计算凸包
                hull = ConvexHull(points_2d)
                hull_points = points_2d[hull.vertices]
                
                # 3. 对凸包点进行排序，确保是顺时针或逆时针顺序
                # 计算相对于形心的角度
                angles = np.arctan2(hull_points[:, 1] - centroid[1], 
                                hull_points[:, 0] - centroid[0])
                sorted_indices = np.argsort(angles)
                sorted_hull_points = hull_points[sorted_indices]
                
                # 4. 拟合B样条曲线
                # 闭合曲线需要首尾相连
                closed_points = np.vstack([sorted_hull_points, sorted_hull_points[0]])
                
                # 参数化曲线
                tck, u = splprep([closed_points[:, 0], closed_points[:, 1]], 
                                s=0, k=3, per=True)
                
                # 生成更多的点来获得平滑的曲线
                u_new = np.linspace(0, 1, 100)
                smooth_curve = np.column_stack(splev(u_new, tck))
                
                # 5. 以形心为基准，按比例缩小曲线
                # 计算每个曲线点到形心的向量
                vectors_to_centroid = smooth_curve - centroid
                
                # 按比例缩小向量
                shrunk_vectors = vectors_to_centroid * shrink_ratio
                
                # 计算缩小后的曲线点
                shrunk_curve = centroid + shrunk_vectors
                
                # 6. 判断每个原始点是在缩小曲线内部还是外部
                # 使用射线法判断点是否在多边形内部
                def point_in_polygon(point, polygon):
                    """射线法判断点是否在多边形内部"""
                    x, y = point
                    n = len(polygon)
                    inside = False
                    
                    p1x, p1y = polygon[0]
                    for i in range(n + 1):
                        p2x, p2y = polygon[i % n]
                        if y > min(p1y, p2y):
                            if y <= max(p1y, p2y):
                                if x <= max(p1x, p2x):
                                    if p1y != p2y:
                                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                                    if p1x == p2x or x <= xinters:
                                        inside = not inside
                        p1x, p1y = p2x, p2y
                    return inside
                
                # 判断每个点是否在缩小曲线内部
                internal_mask = np.array([point_in_polygon(point, shrunk_curve) for point in points_2d])
                external_mask = ~internal_mask
                
                # 7. 分离内外壁点
                internal_points = points_2d[internal_mask]
                external_points = points_2d[external_mask]

                # 验证
                if abs(len(internal_points)-len(external_points)) >= 200:
                    return True
                else:
                    return False
                
            except Exception as e:
                print(f"检测失败: {e}")
                import traceback
                traceback.print_exc()
                return False
        
        # 计算截面的凸包和周长
        if len(section.vertices) >= 3:
            # 将3D点投影到2D平面
            # 使用主成分分析找到截面的主方向
            from scipy.spatial import ConvexHull
            
            # 计算截面点相对于中心点的偏移
            relative_points = section.vertices - center_point
            
            # 找到垂直于中心线方向的两个正交向量
            # 使用Gram-Schmidt正交化
            u1 = np.array([1, 0, 0])  # 初始向量
            if np.abs(np.dot(u1, direction)) > 0.9:
                u1 = np.array([0, 1, 0])
            
            # 正交化
            u1 = u1 - np.dot(u1, direction) * direction
            u1 = u1 / np.linalg.norm(u1)
            
            u2 = np.cross(direction, u1)
            u2 = u2 / np.linalg.norm(u2)
            
            # 投影到2D平面
            points_2d = np.column_stack([
                np.dot(relative_points, u1),
                np.dot(relative_points, u2)
            ])
            
            # 计算凸包
            hull = ConvexHull(points_2d)
            hull_area = hull.volume  # 在2D中，volume实际上是面积
            hull_perimeter = hull.area  # 在2D中，area实际上是周长
            
            # 计算X轴和Y轴上的最大长度（最大值减去最小值）
            x_coords = points_2d[:, 0]
            y_coords = points_2d[:, 1]
            
            x_length = np.max(x_coords) - np.min(x_coords)  # X轴最大长度
            y_length = np.max(y_coords) - np.min(y_coords)  # Y轴最大长度
            
            # 确定短轴长度（取X轴、Y轴最大长度中的较大值）
            short_axis_length = max(x_length, y_length)
            
            # 生成椭圆参数
            ellipse_params = self._generate_ellipse_from_perimeter_and_short_axis(
                hull_perimeter, short_axis_length
            )

            if_is_daao = identify_inner_outer_walls_improved(points_2d)
            
        else:
            hull_area = 0
            hull_perimeter = 0
            x_length = 0
            y_length = 0
            short_axis_length = 0
            ellipse_params = None
            if_is_daao = False
        
        cross_section = {
            'position_mm': position_mm,
            'center_point': center_point.tolist(),
            'direction': direction.tolist(),
            'vertices': section.vertices.tolist(),
            'bounds': {
                'min': section_bounds[0].tolist(),
                'max': section_bounds[1].tolist(),
                'dimensions': section_dimensions.tolist()
            },
            'area': hull_area,
            'perimeter': hull_perimeter,
            'vertex_count': len(section.vertices),
            # 新增：X轴和Y轴最大长度
            'x_length': x_length,
            'y_length': y_length,
            'short_axis_length': short_axis_length,
            # 椭圆参数
            'ellipse_params': ellipse_params,
            # 检测是否为打凹管件
            'if_is_daao': if_is_daao
        }
        
        return cross_section
    
    def _generate_ellipse_from_perimeter_and_short_axis(self, perimeter: float, short_axis_length: float):
        """
        基于周长和短轴长度生成椭圆参数
        
        Args:
            perimeter: 椭圆周长
            short_axis_length: 短轴长度（取X轴、Y轴最大长度中的较大值）
            
        Returns:
            椭圆参数字典
        """
        if perimeter <= 0 or short_axis_length <= 0:
            return None
        
        try:
            from scipy.optimize import fsolve
            
            # 短轴长度
            b = short_axis_length / 2  # 短轴半径
            
            # 使用椭圆周长近似公式求解长轴
            # 椭圆周长近似公式：P ≈ π[3(a+b) - sqrt((3a+b)(a+3b))]
            def ellipse_perimeter_equation(a):
                return np.pi * (3*(a+b) - np.sqrt((3*a+b)*(a+3*b))) - perimeter
            
            # 初始猜测：假设椭圆不太扁
            a_init = b * 1.5
            
            # 数值求解长轴半径
            a = fsolve(ellipse_perimeter_equation, a_init)[0]
            
            # 确保长轴大于等于短轴
            if a < b:
                a = b
            
            # 计算椭圆面积
            ellipse_area = np.pi * a * b
            
            # 计算椭圆的实际周长（用于验证）
            actual_perimeter = np.pi * (3*(a+b) - np.sqrt((3*a+b)*(a+3*b)))
            
            return {
                'long_axis_radius': a,
                'short_axis_radius': b,
                'long_axis_length': 2 * a,
                'short_axis_length': 2 * b,
                'area': ellipse_area,
                'perimeter': actual_perimeter,
                'aspect_ratio': b / a,  # 短轴/长轴比例
                'target_perimeter': perimeter
            }
            
        except Exception as e:
            logger.warning(f"椭圆参数计算失败: {e}")
            return None 

# 使用示例
if __name__ == "__main__":
    # 加载mesh文件
    mesh_path = "your_mesh_file.stl"  # 替换为你的mesh文件路径
    try:
        mesh = trimesh.load(mesh_path)
        logger.info(f"成功加载mesh文件: {mesh_path}")
        logger.info(f"mesh顶点数: {len(mesh.vertices)}, 面数: {len(mesh.faces)}")
        
        # 创建中心线分析器，使用多进程（自动检测CPU核心数）
        analyzer = CenterlineAnalyzer(mesh, delta=1.0)
        
        # 提取中心线
        centerline = analyzer.extract_centerline()
        
        # 平滑中心线
        smoothed_centerline = analyzer.smooth_centerline()
        
        # 计算中心线长度
        centerline_length = analyzer.calculate_centerline_length(use_original=False)
        
        # 获取中心线信息
        centerline_info = analyzer.get_centerline_info()
        logger.info(f"中心线信息: {centerline_info}")
        
        # 在指定位置获取截面
        if centerline_info:
            cross_section = analyzer.get_cross_section_at_centerline_position(centerline_length / 2)
            logger.info(f"中间截面信息: {cross_section}")
            
    except Exception as e:
        logger.error(f"程序运行失败: {e}")
        import traceback
        traceback.print_exc()