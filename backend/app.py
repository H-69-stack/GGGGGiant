import ipaddress
import os

from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
from flask_socketio import SocketIO
from config import Config, LICENSE_GATE_ENABLED, FRONTEND_DIST_DIR

socketio = SocketIO(cors_allowed_origins="*")

# 需要受许可证保护的功能模块前缀（无有效许可证时禁止访问）。
# 登录 / 许可证状态等 auth 接口不在此列，以便客户在未激活时仍能登录管理员、安装许可证。
_LICENSE_PROTECTED_PREFIXES = (
    '/api/normal/',
    '/api/tp/',
    '/api/taper/',
    '/api/pp/',
    '/api/rfm/',
    '/api/rotation/',
    '/api/entity/',
)

# 无需登录的 auth 接口（其余 /api/auth/ 默认需登录）
_AUTH_PUBLIC_PATHS = frozenset({
    '/api/auth/login',
    '/api/auth/signup',
    '/api/auth/license-status',
    '/api/auth/allowed-roles',
    '/api/auth/machine-code',
    '/api/auth/activate',
})


def _parse_cidrs(cidr_str):
    networks = []
    for part in (cidr_str or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            pass
    return networks


def _client_ip():
    """获取真实客户端 IP。

    服务部署在 nginx 之后，真实 IP 由 nginx 通过 X-Real-IP / X-Forwarded-For
    透传（见 docker/nginx.conf）。取 X-Forwarded-For 的第一跳。
    """
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP', '')
    if real_ip:
        return real_ip.strip()
    return request.remote_addr or ''


def _ip_allowed(ip_str, networks):
    if not networks:
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in networks)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    CORS(app)
    socketio.init_app(app)

    allowed_networks = _parse_cidrs('')  # 占位，实际在 before_request 中动态读取

    def _effective_allowed_networks():
        from core.common.network_config import get_allowed_cidrs, is_ip_allowlist_enabled
        if not is_ip_allowlist_enabled():
            return []
        return _parse_cidrs(get_allowed_cidrs())

    @app.before_request
    def _access_and_license_gate():
        if request.method == 'OPTIONS':
            return None

        # 1) IP 白名单：仅允许局域网/指定网段访问，外部一律拒绝。
        networks = _effective_allowed_networks()
        if networks:
            ip = _client_ip()
            if not _ip_allowed(ip, networks):
                return jsonify({
                    'success': False,
                    'message': '访问被拒绝：当前网络不在授权的局域网范围内'
                }), 403

        # 2) 许可证门禁：功能模块在许可证无效时禁止访问。
        #    本地开发/调试时可设置 GTAPP_LICENSE_GATE=0 临时关闭。
        path = request.path or ''
        if LICENSE_GATE_ENABLED and path.startswith(_LICENSE_PROTECTED_PREFIXES):
            from core.common.license import get_license_manager
            status = get_license_manager().get_status()
            if not status.valid:
                return jsonify({
                    'success': False,
                    'message': f'软件未授权或许可证异常：{status.reason}',
                    'license_invalid': True,
                }), 403

        # 3) 业务 API：要求登录 + 按 workrole 鉴权（与前端模块划分一致）。
        from core.common.workrole_access import (
            BUSINESS_API_PREFIXES,
            required_workroles_for_api,
            user_can_access_workroles,
        )
        from api.auth import resolve_current_user

        if path.startswith('/api/auth/') and path not in _AUTH_PUBLIC_PATHS:
            user = resolve_current_user()
            if not user:
                return jsonify({'success': False, 'message': '请先登录'}), 401
            request.current_user = user
        elif path.startswith(BUSINESS_API_PREFIXES):
            user = resolve_current_user()
            if not user:
                return jsonify({'success': False, 'message': '请先登录'}), 401
            request.current_user = user
            required_roles = required_workroles_for_api(path)
            if required_roles is not None and not user_can_access_workroles(user, required_roles):
                return jsonify({
                    'success': False,
                    'message': '当前账户类型无权访问该功能模块',
                    'workrole_denied': True,
                }), 403

        return None

    # Register API blueprints
    from api.auth import auth_bp
    from api.normal import normal_bp
    from api.tp import tp_bp
    from api.taper import taper_bp
    from api.pp import pp_bp
    from api.pp_qa import pp_qa_bp
    from api.rfm import rfm_bp
    from api.rotation import rotation_bp
    from api.entity import entity_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(normal_bp, url_prefix='/api/normal')
    app.register_blueprint(tp_bp, url_prefix='/api/tp')
    app.register_blueprint(taper_bp, url_prefix='/api/taper')
    app.register_blueprint(pp_bp, url_prefix='/api/pp')
    app.register_blueprint(pp_qa_bp, url_prefix='/api/pp')
    from core.pp.dev_cache import is_dev_tools_enabled
    if is_dev_tools_enabled():
        from api.pp_dev import pp_dev_bp, hydrate_dev_session
        app.register_blueprint(pp_dev_bp, url_prefix='/api/pp/dev')
        hydrate_dev_session()
    app.register_blueprint(rfm_bp, url_prefix='/api/rfm')
    app.register_blueprint(rotation_bp, url_prefix='/api/rotation')
    app.register_blueprint(entity_bp, url_prefix='/api/entity')

    @app.route('/api/version')
    def _api_version():
        # Public (no auth): the login page shows the real build version before
        # the user authenticates. brand.js used to hardcode '2.0.0.8' so every
        # build mis-reported its version.
        from core.common import build_info
        return jsonify({
            'version': build_info.get_version(),
            'channel': build_info.BUILD_CHANNEL,
            'commit': build_info.GIT_COMMIT,
            'release': build_info.is_release(),
        })

    _register_frontend_routes(app)
    _print_startup_banner()
    return app


def _register_frontend_routes(app: Flask) -> None:
    """原生安装包：Flask 托管 frontend/dist（等同 nginx 的 SPA + /api 分流）。

    开发态（GTAPP_DEV_TOOLS=1）不要托管仓库里过期的 dist，否则用浏览器打开
    5060 会看到 8 月打包的旧界面，而真正带 HMR 的前端在 Vite :3001。
    """
    from core.pp.dev_cache import is_dev_tools_enabled
    if is_dev_tools_enabled():
        @app.route('/')
        def _dev_frontend_hint():
            return (
                '<!DOCTYPE html><html lang="zh-CN"><head>'
                '<meta charset="utf-8"><title>Giant Tube DEV</title></head>'
                '<body style="font-family:sans-serif;padding:48px;line-height:1.6">'
                '<h1>这是后端 API 端口 5060</h1>'
                '<p>开发界面请打开 '
                '<a href="http://localhost:3001">http://localhost:3001</a>'
                '（上传分析 → 模型文件上传）。</p>'
                '<p>不要用浏览器打开 5060 来测试前端。'
                '5060 只提供 <code>/api</code>。</p>'
                '</body></html>'
            ), 200, {'Content-Type': 'text/html; charset=utf-8'}
        return

    dist = FRONTEND_DIST_DIR
    if not dist or not os.path.isdir(dist):
        return

    index_path = os.path.join(dist, 'index.html')
    if not os.path.isfile(index_path):
        return

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_frontend(path: str):
        if path.startswith('api/') or path.startswith('socket.io'):
            abort(404)
        if path:
            full = os.path.join(dist, path)
            if os.path.isfile(full):
                directory = os.path.dirname(full)
                filename = os.path.basename(full)
                return send_from_directory(directory, filename)
        return send_from_directory(dist, 'index.html')


def _print_startup_banner():
    """启动时打印许可证状态，便于现场部署。"""
    try:
        from core.common.license import get_license_manager
        from core.common.licensing.fingerprint import (
            get_machine_fingerprint,
            format_machine_code,
        )
        from core.common import build_info
        from config import LICENSE_GATE_ENABLED, BIND_SERVER_MACHINE
        fp = get_machine_fingerprint()
        code = format_machine_code(fp)
        status = get_license_manager().get_status()
        print('=' * 56)
        print(' 自行车抽管软件 - 启动信息')
        print(f'  版本/通道  : {build_info.get_version()}  [{build_info.BUILD_CHANNEL}]  commit {build_info.GIT_COMMIT}')
        print(f'  机器码     : {code}')
        print(f'  机器绑定   : {"开启" if BIND_SERVER_MACHINE else "关闭（托管模式）"}')
        print(f'  许可证门禁 : {"开启" if LICENSE_GATE_ENABLED else "关闭（仅开发态）"}')
        if status.valid:
            print(f'  许可证状态 : 有效（客户：{status.customer}）')
            print(f'  到期日期   : {status.expires_at}（剩余 {status.days_left} 天）')
            print(f'  授权账户类型: {"、".join(status.allowed_workroles)}')
        elif not LICENSE_GATE_ENABLED:
            print('  许可证状态 : 开发态（门禁已关闭，无需激活；注册不限制账户类型）')
        else:
            print(f'  许可证状态 : 无效 —— {status.reason}')
        print('=' * 56)
    except Exception as exc:
        print(f'[启动横幅] 读取许可证状态失败: {exc}')


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
