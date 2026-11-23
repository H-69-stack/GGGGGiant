"""Auth API - Login, register, user management."""
import datetime
import os
import re
import secrets
from functools import wraps
from flask import Blueprint, request, jsonify, make_response
from core.common.db import UserDB
from core.common.license import license_check, get_license_manager, dev_mode, dev_status
from core.common.licensing.fingerprint import get_machine_fingerprint
from core.common.licensing.manager import fingerprint_meta
from core.common.licensing.install import install_license_content
from core.common.licensing.activation_code import ActivationCodeError
from core.common.network_config import get_public_info, save_network_config
from core.common.local_addresses import build_access_urls
from core.common.workrole_access import normalize_workrole
from config import DEVICE_COOKIE_NAME, DEVICE_COOKIE_MAX_AGE, COOKIE_SECURE, LICENSE_DAT_PATH, BIND_SERVER_MACHINE

auth_bp = Blueprint('auth', __name__)
user_db = UserDB()
_auth_sessions = {}
_user_active_session = {}

VALID_AUTHORITIES = {'user', 'admin'}
VALID_WORKROLES = {'ALL', '工艺规划', '模具设计', '硬模成型'}
VALID_APPROVAL_STATUSES = {'pending', 'approved', 'rejected'}


def _license_status():
    """开发态（门禁关闭）返回合成"有效"状态；正式态返回 license.dat 真实状态。"""
    return dev_status() if dev_mode() else get_license_manager().get_status()


def _machine_code_payload(fp: str) -> dict:
    """机器码 + 稳定性扩展字段。

    新指纹模块（MachineGuid 单一强标识 + 首次锚定）会提供
    ``fingerprint_details()``；未落地时 fingerprint_meta 会返回中性默认值，
    因此本接口在旧模块下同样可用。
    """
    meta = fingerprint_meta(fp)
    return {
        'machine_code': meta['machine_code'],
        'fingerprint': fp,
        'fingerprint_stable': meta['fingerprint_stable'],
        'fingerprint_source': meta['fingerprint_source'],
        'fingerprint_anchored': meta['fingerprint_anchored'],
        'machine_code_hint': meta['machine_code_hint'],
    }


def _server_license_expires_at():
    status = _license_status()
    return status.expires_at if status.valid else None


def _parse_date(date_str: str) -> datetime.date | None:
    try:
        return datetime.datetime.strptime(str(date_str).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _is_user_expired(user: dict) -> bool:
    """admin 账户永不过期；普通用户 expires_at 为空表示永久有效。"""
    if user.get('authority') == 'admin':
        return False
    exp = user.get('expires_at')
    if not exp:
        return False
    exp_date = _parse_date(exp)
    if not exp_date:
        return True
    return datetime.date.today() > exp_date


def _user_expiry_message(user: dict) -> str:
    exp = user.get('expires_at') or ''
    return f'账户已于 {exp} 到期，请联系管理员续期'


def _requires_device_binding(user: dict) -> bool:
    return user.get('authority') != 'admin'


def _check_user_approval(user: dict) -> tuple[bool, str, int]:
    status = user.get('approval_status') or 'approved'
    if user.get('authority') == 'admin':
        return True, '', 200
    if status == 'pending':
        return False, '账户待管理员审批，审批通过后方可登录使用', 403
    if status == 'rejected':
        return False, '账户注册已被拒绝，请联系管理员', 403
    return True, '', 200


def _device_cookie_from_request() -> str:
    return (request.cookies.get(DEVICE_COOKIE_NAME) or '').strip()


def _tokens_match(stored: str | None, provided: str | None) -> bool:
    if not stored or not provided:
        return False
    return secrets.compare_digest(stored, provided)


def _check_device_for_login(user: dict, cookie_token: str) -> tuple[bool, str, bool]:
    """校验登录设备。返回 (通过, 消息, 是否首次绑定)。"""
    if not _requires_device_binding(user):
        return True, '', False

    stored = user.get('device_token')
    if not stored:
        return True, '', True

    if _tokens_match(stored, cookie_token):
        return True, '', False

    return False, '该账户已绑定其他浏览器，请联系管理员解绑设备后重试', False


def _verify_device_for_session(user: dict) -> bool:
    if not _requires_device_binding(user):
        return True
    stored = user.get('device_token')
    if not stored:
        return True
    return _tokens_match(stored, _device_cookie_from_request())


def _attach_device_cookie(response, token: str):
    response.set_cookie(
        DEVICE_COOKIE_NAME,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite='Lax',
        max_age=DEVICE_COOKIE_MAX_AGE,
        path='/',
    )


def _new_device_token() -> str:
    return secrets.token_urlsafe(32)


def _bind_device(user_id: int) -> str:
    token = _new_device_token()
    user_db.update_user({'id': user_id, 'device_token': token})
    return token


def _revoke_user_sessions(user_id: int):
    active = _user_active_session.pop(user_id, None)
    if active:
        _auth_sessions.pop(active, None)
    for token, session in list(_auth_sessions.items()):
        if session.get('id') == user_id:
            _auth_sessions.pop(token, None)
    _user_active_session.pop(user_id, None)


def _register_session(user_id: int) -> str:
    """单会话：新登录踢掉该用户旧会话。"""
    _revoke_user_sessions(user_id)
    token = secrets.token_urlsafe(32)
    _auth_sessions[token] = {'id': user_id}
    _user_active_session[user_id] = token
    return token


def _resolve_expires_at_for_register(data: dict) -> tuple[bool, str]:
    """解析并写入 data['expires_at']。admin 恒为 None（永久）。"""
    authority = data.get('authority', 'user')
    if authority == 'admin':
        data['expires_at'] = None
        return True, '验证通过'

    lic_exp = _server_license_expires_at()
    if not lic_exp:
        return False, '服务器许可证无效，无法设置账户有效期'

    raw = data.get('expires_at', '__unset__')
    if raw == '__unset__':
        data['expires_at'] = lic_exp
        raw = lic_exp

    if raw is None or (isinstance(raw, str) and raw.strip() == ''):
        data['expires_at'] = None
        return True, '验证通过'

    user_exp = _parse_date(raw)
    lic_date = _parse_date(lic_exp)
    if not user_exp:
        return False, '账户到期日格式应为 YYYY-MM-DD'
    if user_exp < datetime.date.today():
        return False, '账户到期日不能早于今天'
    if lic_date and user_exp > lic_date:
        return False, f'账户到期日不能晚于服务器许可证到期日（{lic_exp}）'
    data['expires_at'] = user_exp.strftime('%Y-%m-%d')
    return True, '验证通过'


def _resolve_expires_at_for_update(update_data: dict, target_user: dict) -> tuple[bool, str]:
    """管理员修改账户到期日。"""
    authority = update_data.get('authority', target_user.get('authority', 'user'))
    if authority == 'admin':
        update_data['expires_at'] = None
        return True, '验证通过'

    if 'expires_at' not in update_data:
        return True, '验证通过'

    lic_exp = _server_license_expires_at()
    if not lic_exp:
        return False, '服务器许可证无效，无法设置账户有效期'

    raw = update_data['expires_at']
    if raw is None or (isinstance(raw, str) and raw.strip() == ''):
        update_data['expires_at'] = None
        return True, '验证通过'

    user_exp = _parse_date(raw)
    lic_date = _parse_date(lic_exp)
    if not user_exp:
        return False, '账户到期日格式应为 YYYY-MM-DD'
    if lic_date and user_exp > lic_date:
        return False, f'账户到期日不能晚于服务器许可证到期日（{lic_exp}）'
    update_data['expires_at'] = user_exp.strftime('%Y-%m-%d')
    return True, '验证通过'


def _check_workrole_licensed(workrole):
    manager = get_license_manager()
    status = _license_status()
    if not status.valid:
        return False, status.reason or '许可证无效，无法注册账户'
    if dev_mode():
        return True, '验证通过（开发态：允许全部账户类型）'
    if not manager.is_workrole_allowed(workrole):
        allowed = '、'.join(status.allowed_workroles) or '（无）'
        return False, f'许可证未授权该账户类型，本许可证仅允许：{allowed}'
    return True, '验证通过'


def _check_max_users():
    status = _license_status()
    if not status.valid:
        return False, status.reason or '许可证无效，无法注册账户'
    if status.max_users <= 0:
        return True, '验证通过'
    success, users = user_db.query_all_users()
    count = 0
    if success:
        for u in users:
            if u.get('authority') == 'admin':
                continue
            if u.get('approval_status') == 'rejected':
                continue
            count += 1
    if count >= status.max_users:
        return False, f'已达许可证允许的最大账户数（{status.max_users}）'
    return True, '验证通过'


def _server_license_marker():
    status = _license_status()
    return status.license_id or 'SERVER_LICENSE'


def _prepare_registration_data(data, *, approval_status='approved'):
    lic_valid, lic_msg = license_check()
    if not lic_valid:
        return False, lic_msg
    ok, msg = _check_max_users()
    if not ok:
        return False, msg
    data['license'] = _server_license_marker()
    data['approval_status'] = approval_status
    if data.get('authority') == 'admin':
        data['approval_status'] = 'approved'
    return True, '验证通过'


def validate_password(password):
    if len(password) < 8:
        return False, "密码长度不能少于8位"
    if not re.search(r'[A-Z]', password):
        return False, "密码必须包含大写字母"
    if not re.search(r'[a-z]', password):
        return False, "密码必须包含小写字母"
    if not re.search(r'[0-9]', password):
        return False, "密码必须包含数字"
    if not re.search(r'[^a-zA-Z0-9]', password):
        return False, "密码必须包含特殊字符"
    return True, "验证通过"


def _public_user(user):
    public = dict(user)
    public.pop('password', None)
    public.pop('device_token', None)
    public['device_bound'] = bool(user.get('device_token'))
    return public


def resolve_current_user():
    """根据请求头中的会话令牌解析当前登录用户（供全局 before_request 使用）。"""
    token = request.headers.get('X-Auth-Token', '')
    if not token:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]
    session = _auth_sessions.get(token)
    if not session:
        return None
    if _user_active_session.get(session['id']) != token:
        return None
    success, user = user_db.get_user(session['id'])
    if not success:
        _auth_sessions.pop(token, None)
        return None
    if _is_user_expired(user):
        _revoke_user_sessions(user['id'])
        return None
    ok, msg, _ = _check_user_approval(user)
    if not ok:
        _revoke_user_sessions(user['id'])
        return None
    if not _verify_device_for_session(user):
        _revoke_user_sessions(user['id'])
        return None
    request.auth_token = token
    return user


def _current_user():
    return resolve_current_user()


def require_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if not user:
            return jsonify({'success': False, 'message': '请先登录'}), 401
        if user.get('authority') != 'admin':
            return jsonify({'success': False, 'message': '只有管理员可以进行用户管理'}), 403
        request.current_user = user
        return f(*args, **kwargs)
    return wrapper


def _validate_user_payload(data, *, allow_authority=False):
    try:
        user_id = int(data.get('id'))
        if user_id <= 0:
            return False, '账号必须为正整数'
    except (TypeError, ValueError):
        return False, '账号必须为数字'

    authority = data.get('authority', 'user')
    if not allow_authority:
        authority = 'user'
    if authority not in VALID_AUTHORITIES:
        return False, '权限类型不合法'

    workrole = normalize_workrole(data.get('workrole'))
    if workrole not in VALID_WORKROLES:
        return False, '用户类型不合法'
    data['workrole'] = workrole

    data['id'] = user_id
    data['authority'] = authority
    return True, '验证通过'


@auth_bp.route('/login', methods=['POST'])
def login():
    """User login."""
    try:
        return _login_impl()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('login failed')
        return jsonify({
            'success': False,
            'message': f'登录服务异常：{exc}',
        }), 500


def _login_impl():
    data = request.get_json()
    user_id = data.get('id', '')
    password = data.get('password', '')

    if not user_id or not password:
        return jsonify({'success': False, 'message': '账号或密码不能为空'}), 400

    success, result = user_db.query_user(user_id, password)
    if not success:
        return jsonify({'success': False, 'message': result}), 401

    if _is_user_expired(result):
        return jsonify({'success': False, 'message': _user_expiry_message(result)}), 401

    appr_ok, appr_msg, appr_code = _check_user_approval(result)
    if not appr_ok:
        return jsonify({
            'success': False,
            'message': appr_msg,
            'approval_status': result.get('approval_status'),
        }), appr_code

    cookie_token = _device_cookie_from_request()
    dev_ok, dev_msg, bind_new = _check_device_for_login(result, cookie_token)
    if not dev_ok:
        return jsonify({
            'success': False,
            'message': dev_msg,
            'device_mismatch': True,
        }), 403

    lic_valid, lic_info = license_check(result['license'])
    session_token = _register_session(result['id'])

    device_token = None
    if _requires_device_binding(result):
        if bind_new:
            device_token = _bind_device(result['id'])
            result['device_token'] = device_token
        else:
            device_token = result.get('device_token')

    body = {
        'success': True,
        'user': _public_user(result),
        'token': session_token,
        'license_valid': lic_valid,
        'license_info': lic_info if lic_valid else None,
        'license_message': lic_info if not lic_valid else None,
        'device_bound': bool(device_token),
        'device_newly_bound': bind_new,
    }
    if bind_new:
        body['message'] = '首次登录成功，本浏览器已与账户绑定'

    resp = make_response(jsonify(body))
    if device_token:
        _attach_device_cookie(resp, device_token)
    return resp


@auth_bp.route('/logout', methods=['POST'])
@require_login
def logout():
    """退出登录并注销当前会话（设备绑定 Cookie 保留，便于同浏览器再次登录）。"""
    token = getattr(request, 'auth_token', '')
    user_id = request.current_user['id']
    if token:
        _auth_sessions.pop(token, None)
    if _user_active_session.get(user_id) == token:
        _user_active_session.pop(user_id, None)
    return jsonify({'success': True, 'message': '已退出登录'})


@auth_bp.route('/register', methods=['POST'])
@require_admin
def register():
    """Admin-only user registration."""
    data = request.get_json()
    required = ['id', 'name', 'password', 'workrole']
    for field in required:
        if not data.get(field):
            return jsonify({'success': False, 'message': f'{field}不能为空'}), 400

    valid, msg = _validate_user_payload(data, allow_authority=True)
    if not valid:
        return jsonify({'success': False, 'message': msg}), 400

    valid, msg = validate_password(data['password'])
    if not valid:
        return jsonify({'success': False, 'message': msg}), 400

    ok, msg = _prepare_registration_data(data, approval_status='approved')
    if not ok:
        return jsonify({'success': False, 'message': msg}), 403

    role_ok, role_msg = _check_workrole_licensed(data['workrole'])
    if not role_ok:
        return jsonify({'success': False, 'message': role_msg}), 403

    exp_ok, exp_msg = _resolve_expires_at_for_register(data)
    if not exp_ok:
        return jsonify({'success': False, 'message': exp_msg}), 400

    success, message = user_db.register_user(data)
    if success:
        return jsonify({
            'success': True,
            'message': '用户注册成功（已自动审批）；用户首次登录时将自动绑定浏览器',
        })
    return jsonify({'success': False, 'message': message}), 400


@auth_bp.route('/users/<int:user_id>/approval', methods=['PUT'])
@require_admin
def set_user_approval(user_id):
    """管理员审批或拒绝用户注册。"""
    data = request.get_json() or {}
    status = (data.get('approval_status') or '').strip().lower()
    if status not in VALID_APPROVAL_STATUSES:
        return jsonify({'success': False, 'message': '审批状态不合法'}), 400
    if request.current_user['id'] == user_id:
        return jsonify({'success': False, 'message': '不能修改自己的审批状态'}), 400

    success, target = user_db.get_user(user_id)
    if not success:
        return jsonify({'success': False, 'message': target}), 404
    if target.get('authority') == 'admin':
        return jsonify({'success': False, 'message': '管理员账户无需审批'}), 400

    success, message = user_db.update_user({'id': user_id, 'approval_status': status})
    if success:
        label = {'pending': '待审批', 'approved': '已通过', 'rejected': '已拒绝'}.get(status, status)
        return jsonify({'success': True, 'message': f'审批状态已更新为：{label}'})
    return jsonify({'success': False, 'message': message}), 400


@auth_bp.route('/users/<int:user_id>/unbind-device', methods=['PUT'])
@require_admin
def unbind_user_device(user_id):
    """管理员解绑用户浏览器（用户换电脑时操作）。"""
    success, target = user_db.get_user(user_id)
    if not success:
        return jsonify({'success': False, 'message': target}), 404
    if target.get('authority') == 'admin':
        return jsonify({'success': False, 'message': '管理员账户无需解绑设备'}), 400

    _revoke_user_sessions(user_id)
    success, message = user_db.update_user({'id': user_id, 'device_token': None})
    if success:
        return jsonify({
            'success': True,
            'message': '已解绑该用户的浏览器，用户可在新浏览器上重新登录并绑定',
        })
    return jsonify({'success': False, 'message': message}), 400


@auth_bp.route('/users', methods=['GET'])
@require_admin
def get_all_users():
    success, result = user_db.query_all_users()
    if success:
        return jsonify({'success': True, 'users': [_public_user(user) for user in result]})
    return jsonify({'success': True, 'users': [], 'message': result})


@auth_bp.route('/users/<int:user_id>', methods=['DELETE'])
@require_admin
def delete_user(user_id):
    if request.current_user['id'] == user_id:
        return jsonify({'success': False, 'message': '不能删除当前登录用户'}), 400
    _revoke_user_sessions(user_id)
    success, message = user_db.delete_user(user_id)
    return jsonify({'success': success, 'message': message})


@auth_bp.route('/users/<int:user_id>', methods=['PUT'])
@require_login
def update_user(user_id):
    data = request.get_json()
    current_user = request.current_user
    is_admin = current_user.get('authority') == 'admin'

    if not is_admin and current_user['id'] != user_id:
        return jsonify({'success': False, 'message': '只能修改自己的账号信息'}), 403

    if is_admin:
        allowed_fields = {'name', 'password', 'authority', 'workrole', 'expires_at'}
    else:
        allowed_fields = {'password'}

    update_data = {'id': user_id}
    for key in allowed_fields:
        if key not in data:
            continue
        if key == 'expires_at':
            update_data[key] = data[key]
        elif data[key] not in (None, ''):
            update_data[key] = data[key]

    if not is_admin and 'password' in update_data:
        if data.get('old_password') != current_user.get('password'):
            return jsonify({'success': False, 'message': '旧密码错误'}), 400

    if 'password' in update_data:
        valid, msg = validate_password(update_data['password'])
        if not valid:
            return jsonify({'success': False, 'message': msg}), 400

    if 'authority' in update_data and update_data['authority'] not in VALID_AUTHORITIES:
        return jsonify({'success': False, 'message': '权限类型不合法'}), 400

    if 'workrole' in update_data:
        update_data['workrole'] = normalize_workrole(update_data['workrole'])
        if update_data['workrole'] not in VALID_WORKROLES:
            return jsonify({'success': False, 'message': '用户类型不合法'}), 400

    if 'workrole' in update_data:
        role_ok, role_msg = _check_workrole_licensed(update_data['workrole'])
        if not role_ok:
            return jsonify({'success': False, 'message': role_msg}), 403

    if is_admin:
        success, target = user_db.get_user(user_id)
        if not success:
            return jsonify({'success': False, 'message': target}), 404
        exp_ok, exp_msg = _resolve_expires_at_for_update(update_data, target)
        if not exp_ok:
            return jsonify({'success': False, 'message': exp_msg}), 400

    success, message = user_db.update_user(update_data)
    return jsonify({'success': success, 'message': message})


@auth_bp.route('/me', methods=['GET'])
@require_login
def get_me():
    current_user = request.current_user
    user_id = request.args.get('id') or current_user['id']
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '用户ID不合法'}), 400

    if user_id != current_user['id'] and current_user.get('authority') != 'admin':
        return jsonify({'success': False, 'message': '只能查看自己的账号信息'}), 403

    success, result = user_db.get_user(user_id)
    if success:
        from core.common.license import license_check
        lic_valid, lic_info = license_check(result['license'])
        return jsonify({
            'success': True,
            'user': _public_user(result),
            'license_valid': lic_valid,
            'license_info': lic_info if lic_valid else '许可无效'
        })
    return jsonify({'success': False, 'message': result}), 404


@auth_bp.route('/signup', methods=['POST'])
def public_signup():
    data = request.get_json()
    required = ['id', 'name', 'password', 'workrole']
    for field in required:
        if not data.get(field):
            return jsonify({'success': False, 'message': f'{field}不能为空'}), 400

    valid, msg = _validate_user_payload(data, allow_authority=False)
    if not valid:
        return jsonify({'success': False, 'message': msg}), 400

    valid, msg = validate_password(data['password'])
    if not valid:
        return jsonify({'success': False, 'message': msg}), 400

    ok, msg = _prepare_registration_data(data, approval_status='pending')
    if not ok:
        return jsonify({'success': False, 'message': msg}), 403

    role_ok, role_msg = _check_workrole_licensed(data['workrole'])
    if not role_ok:
        return jsonify({'success': False, 'message': role_msg}), 403

    data['authority'] = 'user'

    exp_ok, exp_msg = _resolve_expires_at_for_register(data)
    if not exp_ok:
        return jsonify({'success': False, 'message': exp_msg}), 400

    success, message = user_db.register_user(data)
    if success:
        return jsonify({
            'success': True,
            'message': '注册已提交，请等待管理员审批。审批通过后在本浏览器首次登录将自动绑定设备。',
            'approval_status': 'pending',
        })
    return jsonify({'success': False, 'message': message}), 400


@auth_bp.route('/license-status', methods=['GET'])
def license_status():
    status = _license_status()
    # 顶层再给一份机器码扩展信息（不含完整指纹），供激活页/排障直接使用。
    fp_info = _machine_code_payload(get_machine_fingerprint())
    fp_info.pop('fingerprint', None)
    body = {'success': True, 'license': status.to_public_dict()}
    body.update(fp_info)
    return jsonify(body)


@auth_bp.route('/allowed-roles', methods=['GET'])
def allowed_roles():
    status = _license_status()
    return jsonify({
        'success': True,
        'valid': status.valid,
        'reason': status.reason,
        'allowed_workroles': status.allowed_workroles,
    })


@auth_bp.route('/machine-code', methods=['GET'])
def machine_code():
    """返回本机机器码（公开接口，供激活页展示）。无需登录。"""
    fp = get_machine_fingerprint()
    payload = {'success': True}
    payload.update(_machine_code_payload(fp))
    return jsonify(payload)


@auth_bp.route('/activate', methods=['POST'])
def activate():
    """公开接口：安装短激活码（无需登录）。"""
    data = request.get_json(silent=True) or {}
    code = (data.get('activation_code') or data.get('code') or '').strip()
    if not code:
        return jsonify({'success': False, 'message': '请输入授权码'}), 400
    try:
        status = install_license_content(code)
    except ActivationCodeError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except OSError as exc:
        return jsonify({'success': False, 'message': f'保存授权失败：{exc}'}), 500

    if status.valid:
        return jsonify({
            'success': True,
            'message': f'激活成功（客户：{status.customer}，到期：{status.expires_at}）',
            'license': status.to_public_dict(),
        })
    return jsonify({
        'success': False,
        'message': status.reason or '授权码无效',
        'license': status.to_public_dict(),
    }), 400


@auth_bp.route('/deployment-info', methods=['GET'])
@require_admin
def deployment_info():
    """部署与网络配置信息（管理员）。"""
    fp = get_machine_fingerprint()
    status = get_license_manager().get_status()
    net = get_public_info()
    access = build_access_urls()
    fp_info = _machine_code_payload(fp)
    return jsonify({
        'success': True,
        **fp_info,
        'bind_server_machine': BIND_SERVER_MACHINE,
        'license': status.to_public_dict(),
        'network': net,
        'access': access,
        'license_path': LICENSE_DAT_PATH,
    })


@auth_bp.route('/upload-license', methods=['POST'])
@require_admin
def upload_license():
    """上传 license.dat 或粘贴短激活码（管理员）。"""
    content = None
    if 'file' in request.files and request.files['file'].filename:
        raw = request.files['file'].read()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            return jsonify({'success': False, 'message': '许可证文件必须是 UTF-8 文本格式'}), 400
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        content = data.get('content') or data.get('activation_code') or data.get('code')
    if not content or not str(content).strip():
        return jsonify({'success': False, 'message': '请上传 license.dat 或粘贴授权码'}), 400

    try:
        status = install_license_content(str(content).strip())
    except ActivationCodeError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    except OSError as exc:
        return jsonify({'success': False, 'message': f'写入许可证失败：{exc}'}), 500

    if status.valid:
        return jsonify({
            'success': True,
            'message': f'许可证已安装（客户：{status.customer}，到期：{status.expires_at}）',
            'license': status.to_public_dict(),
        })
    return jsonify({
        'success': False,
        'message': status.reason or '许可证无效',
        'license': status.to_public_dict(),
    }), 400


@auth_bp.route('/network-config', methods=['POST'])
@require_admin
def update_network_config():
    """更新运行时网络访问配置（管理员）。"""
    data = request.get_json(silent=True) or {}
    allowed_cidrs = data.get('allowed_cidrs')
    if allowed_cidrs is None or not str(allowed_cidrs).strip():
        return jsonify({'success': False, 'message': '请填写允许访问的 IP 网段（CIDR）'}), 400
    ip_allowlist_enabled = data.get('ip_allowlist_enabled', True)
    try:
        save_network_config(
            allowed_cidrs=str(allowed_cidrs).strip(),
            ip_allowlist_enabled=bool(ip_allowlist_enabled),
        )
    except OSError as exc:
        return jsonify({'success': False, 'message': f'保存配置失败：{exc}'}), 500
    net = get_public_info()
    return jsonify({
        'success': True,
        'message': '网络访问配置已更新，立即生效',
        'network': net,
    })
