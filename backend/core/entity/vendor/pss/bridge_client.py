# -*- coding: utf-8 -*-
"""XML-RPC client for the in-FreeCAD bridge (port 18766 in Giant-Unified).

改造点：桥端口默认从 :mod:`core.entity.settings` 取 18766（原 18765 留给原程序），
并新增不抛异常的 :func:`probe` / :meth:`Bridge.available`，供 pipe 路在 18766 被占
（或不是本程序的桥）时降级 headless。
"""
from __future__ import annotations

import socket
import xmlrpc.client

from . import config


class BridgeUnavailable(RuntimeError):
    pass


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout, **kwargs):
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class Bridge:
    def __init__(self, host=None, port=None):
        self.host = host or config.CONFIG["bridge_host"]
        self.port = int(port or config.CONFIG["bridge_port"])

    @property
    def url(self):
        return "http://%s:%d" % (self.host, self.port)

    def _proxy(self, timeout):
        transport = _TimeoutTransport(timeout)
        return xmlrpc.client.ServerProxy(self.url, transport=transport, allow_none=True,
                                        use_builtin_types=True)

    def ping(self, timeout=1.5):
        try:
            return self._proxy(timeout).ping()
        except (ConnectionRefusedError, socket.timeout, TimeoutError, OSError) as exc:
            raise BridgeUnavailable("FreeCAD 桥接未连接（%s）" % exc)
        except xmlrpc.client.ProtocolError as exc:
            raise BridgeUnavailable("FreeCAD 桥接协议错误（%s）" % exc)
        except Exception as exc:  # noqa: BLE001
            raise BridgeUnavailable("FreeCAD 桥接不可用（%s）" % exc)

    def available(self, timeout=1.5):
        """(是否可用, ping 结果或原因) —— 不抛异常。"""
        try:
            info = self.ping(timeout)
        except BridgeUnavailable as exc:
            return False, str(exc)
        if isinstance(info, dict) and info.get("app") == "FreeCAD":
            return True, info
        return False, "端口 %d 上不是 FreeCAD 桥（%r）" % (self.port, info)

    def status(self, timeout=2):
        try:
            return self._proxy(timeout).get_status()
        except Exception:
            return {"busy": False, "stage": "offline"}

    def process(self, step_path, options, output_dir, open_doc=True, timeout=1200):
        proxy = self._proxy(timeout)
        try:
            return proxy.process(str(step_path), options, str(output_dir), bool(open_doc))
        except xmlrpc.client.Fault as exc:
            raise RuntimeError("FreeCAD 处理失败：%s" % exc.faultString)
        except (ConnectionRefusedError, socket.timeout, TimeoutError, OSError) as exc:
            raise BridgeUnavailable("与 FreeCAD 的连接中断（%s）" % exc)

    def open_file(self, path, timeout=120):
        try:
            return self._proxy(timeout).open_file(str(path))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}


def port_open(host=None, port=None, timeout=0.4):
    """端口是否有人监听（不区分是谁）。"""
    host = host or config.CONFIG["bridge_host"]
    port = int(port or config.CONFIG["bridge_port"])
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(host=None, port=None, timeout=1.5):
    """(桥是否真的可用, 信息 dict) —— 用于「18766 被占则降级 headless」。"""
    bridge = Bridge(host, port)
    ok, info = bridge.available(timeout)
    return ok, {"ok": ok, "url": bridge.url, "listening": port_open(host, port, 0.2),
                "detail": info}


__all__ = ["Bridge", "BridgeUnavailable", "port_open", "probe"]
