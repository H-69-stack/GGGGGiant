# -*- coding: utf-8 -*-
# FreeCAD GUI initialisation: registers the workbench and auto-starts the
# local XML-RPC bridge used by the PipeSolid web application.
import os
import sys

import FreeCAD as App

try:
    _here = os.path.dirname(os.path.abspath(__file__))
except NameError:  # FreeCAD's addon loader does not always define __file__
    _here = os.path.join(App.getUserAppDataDir(), "Mod", "PipeSolidBridge")
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

import FreeCADGui as Gui


class PipeSolidWorkbench(Gui.Workbench):
    MenuText = "管件实体化"
    ToolTip = "把逐截面管件重建为光滑实体并保留分型线"
    Icon = ""

    def Initialize(self):
        self.appendToolbar("管件实体化", ["PipeSolid_OpenPanel", "PipeSolid_StartBridge"])
        self.appendMenu("管件实体化", ["PipeSolid_OpenPanel", "PipeSolid_StartBridge"])

    def GetClassName(self):
        return "Gui::PythonWorkbench"


class OpenPanelCommand:
    def GetResources(self):
        return {"MenuText": "打开管件实体化面板",
                "ToolTip": "在浏览器中打开 http://127.0.0.1:5890（Giant-Unified 实体化流水线）"}

    def IsActive(self):
        return True

    def Activated(self):
        import webbrowser
        webbrowser.open("http://127.0.0.1:5890")


class StartBridgeCommand:
    def GetResources(self):
        return {"MenuText": "启动本地桥接服务",
                "ToolTip": "启动 127.0.0.1:18766 的 XML-RPC 桥接（Giant-Unified 需要它）"}

    def IsActive(self):
        return True

    def Activated(self):
        from PipeSolidBridge import bridge
        info = bridge.start_server()
        App.Console.PrintMessage("PipeSolid bridge: %s\n" % info)


Gui.addCommand("PipeSolid_OpenPanel", OpenPanelCommand())
Gui.addCommand("PipeSolid_StartBridge", StartBridgeCommand())
Gui.addWorkbench(PipeSolidWorkbench())


def _start_bridge_later():
    try:
        from PipeSolidBridge import bridge
        bridge.start_server()
    except Exception as exc:  # noqa: BLE001
        App.Console.PrintError("PipeSolid bridge failed to start: %r\n" % (exc,))


try:
    from PySide6.QtCore import QTimer
except Exception:
    try:
        from PySide2.QtCore import QTimer
    except Exception:
        QTimer = None

if QTimer is not None:
    QTimer.singleShot(1200, _start_bridge_later)
else:
    _start_bridge_later()
