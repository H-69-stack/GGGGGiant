"""pipe_smoother 几何内核副本（相对 import，作为包使用）。

刻意不在包初始化时 import geometry/assemble：OCP 是重依赖，且几何调用必须
由使用方在需要时显式延迟 import（见 ENTITY_PIPELINE.md §12.1）。
"""
