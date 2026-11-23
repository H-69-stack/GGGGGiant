# -*- coding: utf-8 -*-
"""Local XML-RPC bridge that runs inside the FreeCAD GUI.

The HTTP layer (FastAPI) talks to this server.  All geometry work is dispatched
to the Qt main thread through a queue pumped by a QTimer, because the OCCT /
FreeCAD API is not thread safe.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from socketserver import ThreadingMixIn
from xmlrpc.server import SimpleXMLRPCServer

import FreeCAD as App

try:
    import FreeCADGui as Gui
    GUI = True
except Exception:  # FreeCADCmd
    Gui = None
    GUI = False

LOG_PATH = os.path.join(App.getUserAppDataDir(), "PipeSolidBridge.log")

QUEUE = queue.Queue()
STATE = {
    "busy": False,
    "stage": "idle",
    "message": "",
    "started": None,
    "task": None,
    "progress": 0,
}
STATE_LOCK = threading.Lock()
_SERVER = None
_SERVER_THREAD = None
_PUMP_STARTED = False
#: Giant-Unified 专用端口（原程序 PipeSolidStudio 用 18765，两者可同时运行）
_PORT = int(os.environ.get("PIPESOLID_BRIDGE_PORT", "18766"))


def _log(msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    print("[PipeSolidBridge] " + msg, flush=True)


def _set_state(**kw):
    with STATE_LOCK:
        STATE.update(kw)


def _pump():
    """Main-thread queue pump (Qt event loop)."""
    global _PUMP_STARTED
    while True:
        try:
            fn, ev, box = QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            box["traceback"] = traceback.format_exc()
        finally:
            ev.set()
    if GUI:
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            try:
                from PySide2.QtCore import QTimer
            except Exception:
                from PySide.QtCore import QTimer
        QTimer.singleShot(40, _pump)
        _PUMP_STARTED = True


def _call_on_main(fn, timeout=3600):
    if not GUI:
        return fn()
    ev = threading.Event()
    box = {}
    QUEUE.put((fn, ev, box))
    if not ev.wait(timeout):
        raise TimeoutError("FreeCAD 主线程处理超时")
    if "error" in box:
        raise RuntimeError(box["error"])
    return box.get("value")


# --------------------------------------------------------------------------- RPC
def ping():
    info = {
        "ok": True,
        "app": "FreeCAD",
        "version": ".".join(str(v) for v in App.Version()[:3]),
        "gui": bool(GUI),
        "pid": os.getpid(),
        "port": _PORT,
        "user_data": App.getUserAppDataDir(),
        "time": time.time(),
    }
    return info


def get_status():
    with STATE_LOCK:
        return dict(STATE)


def _shape_stats(shape):
    """只读统计（与 freecad_runner.py 同口径），供主进程归一化 solids/free_edges。"""
    from collections import Counter
    try:
        stats = {"faces": len(shape.Faces), "edges": len(shape.Edges),
                 "solids": len(shape.Solids)}
    except Exception:
        return None
    used = Counter()
    try:
        for face in shape.Faces:
            used.update(edge.hashCode() for edge in face.Edges)
        stats["free_edges"] = sum(1 for count in used.values() if count == 1)
    except Exception:
        stats["free_edges"] = None
    try:
        stats["closed"] = bool(shape.isClosed())
    except Exception:
        stats["closed"] = None
    try:
        stats["volume"] = float(shape.Volume)
    except Exception:
        stats["volume"] = None
    return stats


def process(step_path, options=None, output_dir=None, open_doc=True):
    """Blocking call executed by the HTTP backend in its own worker thread."""
    with STATE_LOCK:
        if STATE["busy"]:
            return {"status": "busy", "message": STATE.get("message", "")}
        STATE.update(busy=True, stage="starting", message="准备处理",
                     started=time.time(), progress=0, task=os.path.basename(step_path))
    try:
        def job():
            from . import solidify
            _set_state(stage="geometry", progress=10, message="重建光滑实体")
            out = solidify.process_file(step_path, output_dir, options or {})
            report = out["report"]
            solid = out["solid"]
            edges = out.get("_edge_shapes") or []
            _set_state(stage="document", progress=88, message="写入 FreeCAD 文档")
            if open_doc and GUI:
                _open_result_document(report, solid, edges)
            _set_state(stage="export", progress=96, message="导出完成")
            report["preview_edges"] = out.get("edges") or []
            report["opened_in_freecad"] = bool(open_doc and GUI)
            stats = _shape_stats(solid)
            if stats:
                report["shape_stats"] = stats
            return report

        report = _call_on_main(job)
        _set_state(busy=False, stage="done", progress=100, message="完成")
        return report
    except BaseException as exc:  # noqa: BLE001
        _set_state(busy=False, stage="error", message=str(exc))
        _log("process failed: %r\n%s" % (exc, traceback.format_exc()))
        return {"status": "error", "message": str(exc), "traceback": traceback.format_exc()}


def open_file(path):
    if not os.path.exists(path):
        return {"ok": False, "message": "文件不存在"}
    try:
        def job():
            App.openDocument(path)
            if GUI:
                Gui.SendMsgToActiveView("ViewFit")
            return True
        _call_on_main(job, timeout=120)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc)}


def _open_result_document(report, solid, edges):
    import Part
    name = report["source"]["name"].replace(".step", "").replace(".stp", "")
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:40]
    doc = App.newDocument("PipeSolid_" + name)
    obj = doc.addObject("Part::Feature", "SmoothSolid")
    obj.Shape = solid
    obj.Label = name + "_光滑实体"
    if getattr(obj, "ViewObject", None):
        obj.ViewObject.ShapeColor = (0.72, 0.78, 0.86)
        obj.ViewObject.LineColor = (0.12, 0.14, 0.18)
        obj.ViewObject.DisplayMode = "Flat Lines"
    if edges:
        eo = doc.addObject("Part::Feature", "PartingLines")
        eo.Shape = Part.makeCompound(edges)
        eo.Label = "分型线"
        if getattr(eo, "ViewObject", None):
            eo.ViewObject.LineColor = (1.0, 0.22, 0.12)
            eo.ViewObject.LineWidth = 3.0
            eo.ViewObject.PointColor = (1.0, 0.22, 0.12)
    doc.recompute()
    try:
        Gui.ActiveDocument = Gui.getDocument(doc.Name)
        Gui.SendMsgToActiveView("ViewFit")
        Gui.updateGui()
    except Exception:
        pass
    return doc.Name


# ------------------------------------------------------------------------ server
class _ThreadedXMLRPC(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server(port=None):
    global _SERVER, _SERVER_THREAD, _PORT
    if _SERVER is not None:
        return {"ok": True, "already": True, "port": _PORT}
    if port:
        _PORT = int(port)
    try:
        server = _ThreadedXMLRPC(("127.0.0.1", _PORT), allow_none=True, logRequests=False)
    except OSError as exc:
        # 端口被占（例如并列运行的原程序/另一实例）：不抛出，让调用方降级 headless
        _log("bridge port %d unavailable: %r" % (_PORT, exc))
        return {"ok": False, "port": _PORT, "message": "端口 %d 被占用：%s" % (_PORT, exc)}
    _SERVER = server
    _SERVER.register_function(ping, "ping")
    _SERVER.register_function(get_status, "get_status")
    _SERVER.register_function(process, "process")
    _SERVER.register_function(open_file, "open_file")
    _SERVER.register_introspection_functions()
    _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, name="PipeSolidRPC", daemon=True)
    _SERVER_THREAD.start()
    if GUI and not _PUMP_STARTED:
        try:
            from PySide6.QtCore import QTimer
        except Exception:
            try:
                from PySide2.QtCore import QTimer
            except Exception:
                from PySide.QtCore import QTimer
        QTimer.singleShot(50, _pump)
    _log("bridge listening on 127.0.0.1:%d (gui=%s)" % (_PORT, GUI))
    return {"ok": True, "already": False, "port": _PORT}




