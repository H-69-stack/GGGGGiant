"""本机局域网 IPv4 地址采集，供管理页展示可访问 URL。"""
from __future__ import annotations

import ipaddress
import os
import platform
import re
import socket
import subprocess
from typing import List


def _is_usable_lan_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if not isinstance(addr, ipaddress.IPv4Address):
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return False
    if addr.is_private:
        return True
    # CGNAT / 部分虚拟网卡（如 100.64.0.0/10）
    if addr in ipaddress.ip_network('100.64.0.0/10'):
        return True
    return False


def get_lan_ipv4_addresses() -> List[str]:
    """返回本机可用于局域网访问的 IPv4 地址（去重、排序）。"""
    found: set[str] = set()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_usable_lan_ip(ip):
                found.add(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 1))
            ip = sock.getsockname()[0]
            if _is_usable_lan_ip(ip):
                found.add(ip)
    except OSError:
        pass

    system = platform.system()
    if system == 'Windows':
        try:
            out = subprocess.run(
                ['ipconfig'],
                capture_output=True,
                text=True,
                encoding='gbk',
                errors='replace',
                timeout=8,
                check=False,
            )
            for match in re.finditer(
                r'IPv4[^:\r\n]*[：:]\s*(\d+\.\d+\.\d+\.\d+)',
                out.stdout or '',
            ):
                ip = match.group(1)
                if _is_usable_lan_ip(ip):
                    found.add(ip)
        except (OSError, subprocess.SubprocessError):
            pass
    elif system in ('Linux', 'Darwin'):
        try:
            out = subprocess.run(
                ['hostname', '-I'],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for part in (out.stdout or '').split():
                if _is_usable_lan_ip(part):
                    found.add(part)
        except (OSError, subprocess.SubprocessError):
            pass

    return sorted(found, key=lambda x: ipaddress.ip_address(x))


def get_app_port() -> int:
    # Giant-Unified 默认 5890（原程序 5880 保留给它自己，两者可同时运行）。
    try:
        return int(os.environ.get('APP_PORT', '5890'))
    except (TypeError, ValueError):
        return 5890


def build_access_urls(port: int | None = None) -> dict:
    """生成本机与局域网访问地址列表。"""
    port = port or get_app_port()
    localhost_url = f'http://127.0.0.1:{port}'
    lan_ips = get_lan_ipv4_addresses()
    lan_urls = [f'http://{ip}:{port}' for ip in lan_ips]
    return {
        'port': port,
        'localhost_url': localhost_url,
        'lan_ips': lan_ips,
        'lan_urls': lan_urls,
    }
