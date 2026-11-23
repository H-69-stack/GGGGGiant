#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
stp2stl.py - 使用PythonOCC库将STP文件转换为STL文件

用法:
    python stp2stl.py input.stp [output.stl] [--ascii] [--tolerance=0.1]

参数:
    input.stp       - 输入的STP文件路径
    output.stl      - 输出的STL文件路径(可选，默认为与输入文件同名但扩展名为.stl)
    --ascii         - 输出ASCII格式STL (默认为二进制格式)
    --tolerance=0.1 - 设置网格精度 (默认为0.1)

示例:
    python stp2stl.py model.stp
    python stp2stl.py model.stp output.stl --ascii --tolerance=0.01
"""

import os
import time
import tempfile
import io

try:
    from OCC.Core.StlAPI import StlAPI_Writer
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    from OCC.Core.TopoDS import TopoDS_Shape
    from OCC.Extend.DataExchange import read_step_file
    OCC_AVAILABLE = True
except ImportError:
    OCC_AVAILABLE = False
    StlAPI_Writer = None
    BRepMesh_IncrementalMesh = None
    TopoDS_Shape = None
    read_step_file = None


def convert_stp_to_stl(stp_file_path, stl_file_path, tolerance=0.1, ascii_mode=False, silent=False):
    """
    将STP文件转换为STL文件
    
    参数:
        stp_file_path (str): STP文件的路径
        stl_file_path (str): 输出STL文件的路径
        tolerance (float): 网格精度，值越小精度越高，但处理时间更长
        ascii_mode (bool): 是否以ASCII模式保存STL (True为ASCII格式，False为二进制格式)
        silent (bool): 是否静默模式，不打印进度信息
    
    返回:
        bool: 转换是否成功
    """
    if not OCC_AVAILABLE:
        if not silent:
            print("STP转换不可用：缺少pythonocc-core库")
        return False

    if not silent:
        print(f"开始转换: {stp_file_path} -> {stl_file_path}")
        print(f"参数: 精度={tolerance}, ASCII模式={ascii_mode}")
    
    start_time = time.time()
    
    try:
        # 读取STEP文件
        if not silent:
            print("正在读取STEP文件...")
        shape = read_step_file(stp_file_path)
        
        if not isinstance(shape, TopoDS_Shape):
            if not silent:
                print("读取STEP文件失败: 返回的不是有效形状")
            return False
        
        # 创建网格
        if not silent:
            print(f"正在生成网格 (精度: {tolerance})...")
        mesh = BRepMesh_IncrementalMesh(shape, tolerance, False, 0.5)
        mesh.Perform()
        if not mesh.IsDone():
            if not silent:
                print("网格创建失败")
            return False
        
        # 创建STL写入器
        stl_writer = StlAPI_Writer()
        stl_writer.SetASCIIMode(ascii_mode)
        
        # 保存STL
        if not silent:
            print(f"正在保存STL文件 ({'ASCII' if ascii_mode else '二进制'}模式)...")
        result = stl_writer.Write(shape, stl_file_path)
        
        if not os.path.exists(stl_file_path):
            if not silent:
                print(f"STL文件保存失败: {stl_file_path}")
            return False
        
        end_time = time.time()
        elapsed_time = end_time - start_time
        
        file_size = os.path.getsize(stl_file_path) / (1024 * 1024)  # 转换为MB
        
        if not silent:
            print(f"转换完成!")
            print(f"- 耗时: {elapsed_time:.2f}秒")
            print(f"- 输出文件: {stl_file_path}")
            print(f"- 文件大小: {file_size:.2f} MB")
        
        return True
        
    except Exception as e:
        if not silent:
            print(f"转换过程中出错: {str(e)}")
            import traceback
            traceback.print_exc()
        return False


def convert_stp_bytes_to_stl(stp_bytes, output_stl_path=None, tolerance=0.1, ascii_mode=False, silent=False):
    """
    将STP文件字节流转换为STL文件
    
    参数:
        stp_bytes (bytes): STP文件的字节内容
        output_stl_path (str, optional): 输出STL文件的路径。如果为None，则返回STL字节流
        tolerance (float): 网格精度，值越小精度越高，但处理时间更长
        ascii_mode (bool): 是否以ASCII模式保存STL (True为ASCII格式，False为二进制格式)
        silent (bool): 是否静默模式，不打印进度信息
    
    返回:
        如果output_stl_path不为None: bool，表示转换是否成功
        如果output_stl_path为None: bytes，转换后的STL文件内容字节流
    """
    if not silent:
        print(f"开始转换内存中的STP数据")
        print(f"参数: 精度={tolerance}, ASCII模式={ascii_mode}")
    
    start_time = time.time()
    
    try:
        # 创建临时文件存储STP内容
        with tempfile.NamedTemporaryFile(delete=False, suffix='.stp') as temp_stp_file:
            temp_stp_path = temp_stp_file.name
            temp_stp_file.write(stp_bytes)
        
        # 如果需要返回字节流而不是保存到文件
        if output_stl_path is None:
            temp_stl_path = tempfile.mktemp(suffix='.stl')
            return_bytes = True
        else:
            temp_stl_path = output_stl_path
            return_bytes = False
        
        # 转换STP到STL
        success = convert_stp_to_stl(
            temp_stp_path, 
            temp_stl_path, 
            tolerance=tolerance, 
            ascii_mode=ascii_mode, 
            silent=silent
        )
        
        if not success:
            if not silent:
                print("转换失败")
            return False if not return_bytes else None
        
        # 如果需要返回字节流
        if return_bytes:
            with open(temp_stl_path, 'rb') as stl_file:
                stl_bytes = stl_file.read()
            
            # 删除临时STL文件
            os.unlink(temp_stl_path)
            
            end_time = time.time()
            elapsed_time = end_time - start_time
            
            if not silent:
                print(f"转换完成!")
                print(f"- 耗时: {elapsed_time:.2f}秒")
            
            return stl_bytes
        
        # 直接返回成功状态
        end_time = time.time()
        elapsed_time = end_time - start_time
        
        if not silent:
            print(f"转换完成!")
            print(f"- 耗时: {elapsed_time:.2f}秒")
            
        return True
        
    except Exception as e:
        if not silent:
            print(f"转换过程中出错: {str(e)}")
            import traceback
            traceback.print_exc()
        return None if return_bytes else False
    
    finally:
        # 清理临时STP文件
        if 'temp_stp_path' in locals() and os.path.exists(temp_stp_path):
            os.unlink(temp_stp_path)


# def main():
#     """主函数，处理命令行参数并执行转换过程"""
#     parser = argparse.ArgumentParser(description='将STP文件转换为STL文件')
#     parser.add_argument('input', help='输入的STP文件路径')
#     parser.add_argument('output', nargs='?', help='输出的STL文件路径 (可选)')
#     parser.add_argument('--ascii', action='store_true', help='输出ASCII格式STL (默认为二进制)')
#     parser.add_argument('--tolerance', type=float, default=0.1, help='设置网格精度 (默认为0.1)')
#
#     args = parser.parse_args()
#
#     # 验证输入文件
#     if not os.path.isfile(args.input):
#         print(f"错误: 输入文件不存在: {args.input}")
#         return 1
#
#     # 确定输出文件路径
#     if args.output:
#         output_path = args.output
#     else:
#         # 使用与输入文件相同的文件名，但改为.stl扩展名
#         input_path = Path(args.input)
#         output_filename = f"{input_path.stem}.stl"
#
#         # 使用更灵活的方式确定STL目录
#         # 首先尝试在项目根目录下查找或创建CADs/STL目录
#         root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#         # 检查预期的目录结构是否存在
#         if not os.path.exists(os.path.join(root_dir, 'bin')) or not os.path.exists(os.path.join(root_dir, 'dataset')):
#             # 如果项目被放在子文件夹中，ROOT_DIR应该是当前目录的父目录
#             root_dir = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
#
#         stl_dir = os.path.join(root_dir, 'CADs', 'STL')
#         os.makedirs(stl_dir, exist_ok=True)
#         output_path = os.path.join(stl_dir, output_filename)
#
#     # 执行转换
#     result = convert_stp_to_stl(args.input, output_path, args.tolerance, args.ascii)
#
#     return 0 if result else 1
#
#
# if __name__ == "__main__":
#     sys.exit(main())
