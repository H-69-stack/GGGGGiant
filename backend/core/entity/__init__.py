"""实体化流水线（entity pipeline）。

把 Giant 生成的 5 类派生模型，用两条互相独立的算法重建成
「1 实体 / 4 面 / 2 条共享分型边 / 0 自由边」的 4 面 Solid：

* ``c1``  —— C1 外推 / 121 保形 / STEP 自参考保形（OCP，主进程内跑）
* ``pipe``—— FreeCAD 重蒙皮（子进程 ``freecadcmd.exe``）

对外只暴露契约见 :mod:`core.entity.contracts` 与 :mod:`core.entity.settings`。
"""

from . import contracts, settings  # noqa: F401  (re-export convenience)

__all__ = ["contracts", "settings"]
