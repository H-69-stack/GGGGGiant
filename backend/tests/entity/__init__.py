# -*- coding: utf-8 -*-
"""Giant-Unified 实体化流水线 · S10 测试包。

运行方式（在仓库根目录执行）::

    D:\\python\\envs\\occ\\Scripts\\python.exe -m unittest discover -s backend\\tests\\entity -t backend -v

也可以用 ``run_unified_e2e.ps1`` 一步跑完「单测 + 冒烟/全量流水线」。

模块分工：

* ``test_contracts``    —— MODELS / 端部口径 / 参考路 / ``ModelSpec.out_path``
* ``test_directions``   —— 镜像评分 + 四类分型方向规则（合成截面环）
* ``test_checks``       —— ``inspect_step`` 对本流水线 c1 交付件（c2 历史件回落）的几何体检
* ``test_route_golden`` —— 慢测：24TK_1324-1 成型_外径形腔 × {c1, pipe} 金标准；
  另含 pipe 主进程复检与 ``cached`` 不合规降级两个快回归
* ``test_report``       —— ``write_part_report`` / ``write_summary`` 落盘契约（含 ``stopped`` 计数）

所有测试用标准库 ``unittest``（本机 Python 没有 pytest），且**不写** ``entity_out``
正式目录：需要落盘的测试统一写到 ``.tmp\\s10\\<子目录>``。
"""
