"""PipeSolidStudio 调用层副本（headless / runner / bridge_client）。

Giant-Unified 实体化流水线只允许 pipe 路通过这里调用 FreeCAD 端算法：

* :mod:`config`        —— 路径/端口（桥 18766、job 目录）
* :mod:`headless`      —— ``freecadcmd.exe -c`` 无头执行（默认方式）
* :mod:`freecad_runner`—— 在 FreeCAD 进程内跑的入口脚本（**禁止** import backend）
* :mod:`bridge_client` —— GUI 桥 18766 的 XML-RPC 客户端（可选，失败降级 headless）
"""

from . import bridge_client, config, headless  # noqa: F401

__all__ = ["config", "headless", "bridge_client"]
