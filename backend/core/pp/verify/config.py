"""
STL管件截面分析系统配置文件
"""

# 应用配置
APP_CONFIG = {
    'name': 'STL管件截面分析系统',
    'version': '1.0.0',
    'description': '专注于STL管件截面分析的Streamlit应用',
    'author': 'STL Analysis System',
    'icon': '🔍'
}

# 文件上传配置
UPLOAD_CONFIG = {
    'max_file_size': 100 * 1024 * 1024,  # 100MB
    'allowed_pdf_types': ['.pdf'],
    'allowed_stl_types': ['.stl'],
    'temp_dir': 'temp'
}

# STL分析配置
STL_CONFIG = {
    'default_positions': [0.1, 0.25, 0.5, 0.75, 0.9],  # 默认分析位置（比例）
    'min_vertices': 100,  # 最小顶点数
    'max_vertices': 1000000,  # 最大顶点数
    'visualization': {
        'opacity': 0.8,
        'color': 'lightblue',
        'height': 600
    }
}

# 截面分析配置
SECTION_CONFIG = {
    'default_step': 1.0,  # 默认位置步长
    'precision': 3,  # 数值精度
    'visualization': {
        'marker_size': 2,
        'line_width': 2,
        'height': 400
    }
}

# 日志配置
LOGGING_CONFIG = {
    'level': 'INFO',
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': 'app.log',
    'max_size': 10 * 1024 * 1024,  # 10MB
    'backup_count': 5
}

# 界面配置
UI_CONFIG = {
    'page_title': 'STL管件截面分析系统',
    'page_icon': '🔍',
    'layout': 'wide',
    'initial_sidebar_state': 'expanded',
    'theme': {
        'primaryColor': '#1f77b4',
        'backgroundColor': '#ffffff',
        'secondaryBackgroundColor': '#f0f2f6',
        'textColor': '#262730'
    }
} 