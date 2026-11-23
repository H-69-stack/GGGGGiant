"""License check decorator for Flask endpoints."""
from functools import wraps
from flask import request, jsonify
from core.common.license import license_check


def require_license(f):
    """Decorator that checks if the request has a valid license.

    Expects the license key in the X-License header or in the JSON body as 'license'.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        license_key = request.headers.get('X-License', '')
        if not license_key:
            data = request.get_json(silent=True) or {}
            license_key = data.get('license', '')
        if not license_key:
            return jsonify({'success': False, 'message': '未提供许可证'}), 403

        valid, info = license_check(license_key)
        if not valid:
            return jsonify({'success': False, 'message': info}), 403

        return f(*args, **kwargs)
    return decorated
