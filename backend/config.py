import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALL_ROOT = os.environ.get('GTAPP_INSTALL_ROOT', '').strip()

# 前端静态资源（原生 Windows 安装包：Flask 直接托管 Vite 构建产物）
_default_frontend_dist = (
    os.path.join(INSTALL_ROOT, 'frontend', 'dist')
    if INSTALL_ROOT
    else os.path.join(BASE_DIR, '..', 'frontend', 'dist')
)
FRONTEND_DIST_DIR = os.environ.get('GTAPP_FRONTEND_DIST', _default_frontend_dist)
FRONTEND_DIST_DIR = os.path.normpath(FRONTEND_DIST_DIR)

# Runtime data directory (must be defined before DB paths)
_data_override = os.environ.get('GTAPP_DATA_DIR', '').strip()
DATA_DIR = _data_override if _data_override else os.path.join(BASE_DIR, 'data')
UPLOADS_DIR = os.path.join(DATA_DIR, 'uploads')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
EXPORTS_DIR = os.path.join(DATA_DIR, 'exports')

# Database paths
DATABASE_DIR = os.path.join(BASE_DIR, 'database')
USER_DB = os.path.join(DATA_DIR, 'user.db')  # Runtime user DB in data directory
NORMAL_MOLD_DIR = os.path.join(DATABASE_DIR, 'normal-mold')
TAPER_MOLD_DIR = os.path.join(DATABASE_DIR, 'taper-mold')
TP_MOLD_DIR = os.path.join(DATABASE_DIR, 'tp-mold')

# Static files
STATIC_DIR = os.path.join(BASE_DIR, 'static')
DXF_TEMPLATES_DIR = os.path.join(STATIC_DIR, 'dxf_templates')
IMAGES_DIR = os.path.join(STATIC_DIR, 'images')

# License
# 旧版明文许可证（已废弃，仅保留路径以便兼容）
LICENSE_JSON_PATH = os.path.join(STATIC_DIR, 'license.json')
# 新版签名许可证文件（由开发侧发证工具签发，绑定服务器硬件指纹）
_default_license = (
    os.path.join(INSTALL_ROOT, 'license', 'license.dat')
    if INSTALL_ROOT
    else os.path.join(BASE_DIR, 'static', 'license.dat')
)
LICENSE_DAT_PATH = os.environ.get('GTAPP_LICENSE_PATH', _default_license)

# 访问控制：允许访问的客户端 IP 网段（CIDR），逗号分隔。
# 默认仅放行回环与私有局域网网段，外部公网访问会被拒绝。
# 部署时可通过环境变量 GTAPP_ALLOWED_CIDRS 收紧为客户的具体子网，例如：
#   GTAPP_ALLOWED_CIDRS=192.168.10.0/24
_DEFAULT_ALLOWED_CIDRS = '127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7'
ALLOWED_CIDRS = os.environ.get('GTAPP_ALLOWED_CIDRS', _DEFAULT_ALLOWED_CIDRS)
# 是否启用 IP 白名单（默认启用，设为 '0' 可临时关闭用于排障）
IP_ALLOWLIST_ENABLED = os.environ.get('GTAPP_IP_ALLOWLIST', '1') != '0'
# 是否启用许可证门禁。
#   - release 通道（交付镜像）：门禁恒定开启，忽略环境变量，防止客户绕过。
#   - dev 通道（本地开发）：允许用 GTAPP_LICENSE_GATE=0 临时关闭，方便调试。
try:
    from core.common.build_info import is_release as _is_release
    _RELEASE = _is_release()
except Exception:
    _RELEASE = False
if _RELEASE:
    LICENSE_GATE_ENABLED = True
else:
    LICENSE_GATE_ENABLED = os.environ.get('GTAPP_LICENSE_GATE', '1') != '0'

# 是否将 license.dat 绑定到部署服务器硬件指纹。
# Docker 托管部署（开发者本机 + 公网访问）设为 0：仅校验签名/到期/授权范围，不校验服务器机器码。
BIND_SERVER_MACHINE = os.environ.get('GTAPP_BIND_SERVER_MACHINE', '1') != '0'

# 浏览器设备绑定 Cookie（HttpOnly，防账号跨浏览器共享）
DEVICE_COOKIE_NAME = os.environ.get('GTAPP_DEVICE_COOKIE', 'gtapp_device')
DEVICE_COOKIE_MAX_AGE = int(os.environ.get('GTAPP_DEVICE_COOKIE_MAX_AGE', str(365 * 24 * 3600)))
# HTTPS 部署时设为 1
COOKIE_SECURE = os.environ.get('GTAPP_COOKIE_SECURE', '0') == '1'

# Process Planning (PP)
PP_DB_DIR = os.path.join(DATABASE_DIR, 'pp')
PP_TUBE_DB = os.path.join(PP_DB_DIR, 'tube_analysis.db')
# 工艺设计数据库：保存优化后的工艺参数设计，供下游模块（普通抽/TP抽/Taper/硬模成型）使用。
PP_DESIGN_DB = os.path.join(PP_DB_DIR, 'pp_design.db')
PP_UPLOADS_DIR = os.path.join(UPLOADS_DIR, 'pp')
PP_MESH_CACHE_DIR = os.path.join(CACHE_DIR, 'pp', 'mesh')
# Permanent storage for source files referenced by saved DB records.
PP_TUBE_SAVED_DIR = os.path.join(DATA_DIR, 'pp_saved_sources')

# Output root for generated files
OUTPUT_ROOT = os.path.join(DATA_DIR, 'exports')

# Ensure runtime directories exist
for d in [UPLOADS_DIR, CACHE_DIR, EXPORTS_DIR, PP_DB_DIR, PP_UPLOADS_DIR, PP_MESH_CACHE_DIR, PP_TUBE_SAVED_DIR]:
    os.makedirs(d, exist_ok=True)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'giant-tube-app-secret-2026')
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100MB max upload
    JSON_AS_ASCII = False
