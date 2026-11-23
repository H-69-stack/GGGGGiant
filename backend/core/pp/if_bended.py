import numpy as np
import math
import logging

# 配置日志
logger = logging.getLogger('bend_analysis')

def is_tube_bended(centerline_points, angle_threshold=15.0):
    """
    判断管件是否弯曲
    
    参数:
        centerline_points: numpy数组，形状为(n, 3)的中心线点集
        angle_threshold: 弯折角度阈值（度），默认15度
        
    返回:
        bool: True表示弯曲，False表示不弯曲
    """
    try:
        # 检查输入数据
        if centerline_points is None or len(centerline_points) < 3:
            logger.warning("中心线点数不足，无法判断弯曲")
            print("警告: 中心线点数不足，无法判断弯曲")
            return False
        
        # 获取首尾点
        point_A = centerline_points[0]  # 第一个点
        point_B = centerline_points[-1]  # 最后一个点
        
        logger.info(f"开始判断管件弯曲，中心线点数: {len(centerline_points)}")
        print(f"信息: 开始判断管件弯曲，中心线点数: {len(centerline_points)}")
        logger.info(f"首点A: {point_A}")
        print(f"信息: 首点A: {point_A}")
        logger.info(f"尾点B: {point_B}")
        print(f"信息: 尾点B: {point_B}")
        logger.info(f"角度阈值: {angle_threshold}度")
        print(f"信息: 角度阈值: {angle_threshold}度")
        
        # 计算向量AB
        vector_AB = point_B - point_A
        length_AB = np.linalg.norm(vector_AB)
        
        if length_AB < 1e-6:
            logger.warning("首尾点距离过小，无法判断弯曲")
            print("警告: 首尾点距离过小，无法判断弯曲")
            return False
        
        # 遍历中心线上的每个点，计算角AOB
        max_angle = 0.0
        max_angle_point_idx = -1
        
        for i, point_O in enumerate(centerline_points):
            # 跳过首尾点
            if i == 0 or i == len(centerline_points) - 1:
                continue
            
            # 计算向量AO和BO
            vector_AO = point_O - point_A
            vector_BO = point_O - point_B
            
            # 计算向量长度
            length_AO = np.linalg.norm(vector_AO)
            length_BO = np.linalg.norm(vector_BO)
            
            # 检查向量长度是否足够
            if length_AO < 1e-6 or length_BO < 1e-6:
                continue
            
            # 计算角AOB（使用余弦定理）
            # cos(AOB) = (AO·BO) / (|AO| * |BO|)
            dot_product = np.dot(vector_AO, vector_BO)
            cos_angle = dot_product / (length_AO * length_BO)
            
            # 限制cos值在[-1, 1]范围内，避免数值误差
            cos_angle = np.clip(cos_angle, -1.0, 1.0)
            
            # 计算角度（弧度转度）
            angle_rad = math.acos(cos_angle)
            angle_deg = math.degrees(angle_rad)
            
            # 对于弯曲检测，我们需要的是偏离直线的程度
            # 如果点O在直线AB上，角AOB应该接近180度
            # 如果点O偏离直线AB，角AOB会小于180度
            # 所以弯曲程度 = 180度 - 角AOB
            bend_angle = 180.0 - angle_deg
            
            # 更新最大弯曲角度
            if bend_angle > max_angle:
                max_angle = bend_angle
                max_angle_point_idx = i
        
        # 判断是否弯曲
        is_bended = max_angle > angle_threshold
        
        # 输出结果到终端
        print(f"=== 管件弯曲分析结果 ===")
        print(f"最大弯折角度: {max_angle:.2f}度")
        if max_angle_point_idx >= 0:
            print(f"最大角度点索引: {max_angle_point_idx}")
            print(f"最大角度点坐标: {centerline_points[max_angle_point_idx]}")
        print(f"角度阈值: {angle_threshold}度")
        print(f"判断结果: {'弯曲' if is_bended else '不弯曲'}")
        print(f"=========================")
        
        # 记录日志
        logger.info(f"最大弯折角度: {max_angle:.2f}度，阈值: {angle_threshold}度")
        print(f"信息: 最大弯折角度: {max_angle:.2f}度，阈值: {angle_threshold}度")
        logger.info(f"管件判断结果: {'弯曲' if is_bended else '不弯曲'}")
        print(f"信息: 管件判断结果: {'弯曲' if is_bended else '不弯曲'}")
        
        return is_bended
        
    except Exception as e:
        logger.error(f"判断管件弯曲时发生错误: {e}")
        print(f"错误: 判断管件弯曲时发生错误 - {e}")
        return False

def calculate_bend_angle(centerline_points):
    """
    计算管件的弯曲角度（不进行判断，只返回最大角度）
    
    参数:
        centerline_points: numpy数组，形状为(n, 3)的中心线点集
        
    返回:
        float: 最大弯折角度（度）
    """
    try:
        if centerline_points is None or len(centerline_points) < 3:
            return 0.0
        
        # 获取首尾点
        point_A = centerline_points[0]
        point_B = centerline_points[-1]
        
        # 计算向量AB
        vector_AB = point_B - point_A
        length_AB = np.linalg.norm(vector_AB)
        
        if length_AB < 1e-6:
            return 0.0
        
        # 遍历中心线上的每个点，计算角AOB
        max_angle = 0.0
        
        for i, point_O in enumerate(centerline_points):
            # 跳过首尾点
            if i == 0 or i == len(centerline_points) - 1:
                continue
            
            # 计算向量AO和BO
            vector_AO = point_O - point_A
            vector_BO = point_O - point_B
            
            # 计算向量长度
            length_AO = np.linalg.norm(vector_AO)
            length_BO = np.linalg.norm(vector_BO)
            
            # 检查向量长度是否足够
            if length_AO < 1e-6 or length_BO < 1e-6:
                continue
            
            # 计算角AOB
            dot_product = np.dot(vector_AO, vector_BO)
            cos_angle = dot_product / (length_AO * length_BO)
            cos_angle = np.clip(cos_angle, -1.0, 1.0)
            
            angle_rad = math.acos(cos_angle)
            angle_deg = math.degrees(angle_rad)
            
            # 计算弯曲角度
            bend_angle = 180.0 - angle_deg
            
            # 更新最大角度
            if bend_angle > max_angle:
                max_angle = bend_angle
        
        return max_angle
        
    except Exception as e:
        logger.error(f"计算弯曲角度时发生错误: {e}")
        print(f"错误: 计算弯曲角度时发生错误 - {e}")
        return 0.0

def analyze_bend_details(centerline_points, angle_threshold=15.0):
    """
    详细分析管件的弯曲情况
    
    参数:
        centerline_points: numpy数组，形状为(n, 3)的中心线点集
        angle_threshold: 弯折角度阈值（度）
        
    返回:
        dict: 包含详细分析结果的字典
    """
    try:
        if centerline_points is None or len(centerline_points) < 3:
            return {
                'is_bended': False,
                'max_angle': 0.0,
                'angle_threshold': angle_threshold,
                'error': '中心线点数不足'
            }
        
        # 获取首尾点
        point_A = centerline_points[0]
        point_B = centerline_points[-1]
        
        # 计算向量AB
        vector_AB = point_B - point_A
        length_AB = np.linalg.norm(vector_AB)
        
        if length_AB < 1e-6:
            return {
                'is_bended': False,
                'max_angle': 0.0,
                'angle_threshold': angle_threshold,
                'error': '首尾点距离过小'
            }
        
        # 遍历中心线上的每个点，计算角AOB
        max_angle = 0.0
        max_angle_point_idx = -1
        max_angle_point = None
        all_angles = []
        
        for i, point_O in enumerate(centerline_points):
            # 跳过首尾点
            if i == 0 or i == len(centerline_points) - 1:
                continue
            
            # 计算向量AO和BO
            vector_AO = point_O - point_A
            vector_BO = point_O - point_B
            
            # 计算向量长度
            length_AO = np.linalg.norm(vector_AO)
            length_BO = np.linalg.norm(vector_BO)
            
            # 检查向量长度是否足够
            if length_AO < 1e-6 or length_BO < 1e-6:
                continue
            
            # 计算角AOB
            dot_product = np.dot(vector_AO, vector_BO)
            cos_angle = dot_product / (length_AO * length_BO)
            cos_angle = np.clip(cos_angle, -1.0, 1.0)
            
            angle_rad = math.acos(cos_angle)
            angle_deg = math.degrees(angle_rad)
            
            all_angles.append(angle_deg)
            
            # 更新最大角度
            if angle_deg > max_angle:
                max_angle = angle_deg
                max_angle_point_idx = i
                max_angle_point = point_O
        
        # 判断是否弯曲
        is_bended = max_angle > angle_threshold
        
        # 计算统计信息
        if all_angles:
            avg_angle = np.mean(all_angles)
            std_angle = np.std(all_angles)
        else:
            avg_angle = 0.0
            std_angle = 0.0
        
        result = {
            'is_bended': is_bended,
            'max_angle': max_angle,
            'angle_threshold': angle_threshold,
            'max_angle_point_idx': max_angle_point_idx,
            'max_angle_point': max_angle_point.tolist() if max_angle_point is not None else None,
            'start_point': point_A.tolist(),
            'end_point': point_B.tolist(),
            'total_length': length_AB,
            'avg_angle': avg_angle,
            'std_angle': std_angle,
            'angle_count': len(all_angles)
        }
        
        return result
        
    except Exception as e:
        logger.error(f"详细分析弯曲情况时发生错误: {e}")
        print(f"错误: 详细分析弯曲情况时发生错误 - {e}")
        return {
            'is_bended': False,
            'max_angle': 0.0,
            'angle_threshold': angle_threshold,
            'error': str(e)
        }
