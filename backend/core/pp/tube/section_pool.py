# -*- coding: utf-8 -*-
"""站内并行的截面进程池。

为什么可以并行
--------------
每个站位只依赖 ``(shape, origin, normal)`` 三个输入，不读写任何共享状态，
所以按站位切片丢给多个进程不会改变任何读数：任务按索引回填，结果顺序与
串行完全一致。真正省不掉的是阶段之间的样条拟合（barrier）、端部探测里
「前一次无效就停」的语义，以及读 STEP / 收尾 / 写缓存。

为什么用进程而不是线程
----------------------
OCP 是 pybind11 绑定，调用期间不释放 GIL；OCCT 的建模算法也不可重入。
线程并行既拿不到加速，还可能互相破坏内部状态。进程之间是独立的 OCCT
实例，安全。

Windows 的代价
--------------
没有 fork，每个 worker 都是新解释器：重 import OCP（约 3.7 s）+ 读一次
STEP（0.2–1.4 s）。这段只能用池的 ``initializer`` 付一次，所以池在整次
测量期间复用，不要按阶段反复建池。

开关
----
* ``GTAPP_MEASURE_WORKERS``：进程数，1 表示串行。不设时默认
  min(物理核数, 8, 可用提交内存允许数)；显式设置则只夹在 1..32，完全按用户来。
  取物理核而不是逻辑核，是因为超线程出来的第二个线程会和第一个抢同一个
  浮点/内存端口：本机 8C16T 上 16 进程（69.4 ms/站）明显慢于 8 进程
  （55.7 ms/站）。工作站（如 32C/64T）需要更多时用环境变量显式调高。
* ``GTAPP_MEASURE_PARALLEL=0``：彻底关掉站内并行（排查问题用）。

上限与内存
----------
每个 worker 各自持有整份 shape（几百 MB 量级，和 STEP 大小与曲面复杂度有关），
所以进程数还要受内存约束：32 个 worker 按每份 300 MB 估就是约 10 GB。
工作站跑之前先看内存余量，必要时用 ``GTAPP_MEASURE_WORKERS`` 手动压一档。

默认进程数 = min(物理核数, ``DEFAULT_MAX_WORKERS``, 按可用提交内存算出的上限)。
后半段是 2026-11 补的：13 代 i7 这类 6P+8E 的机器物理核有 14 个，16 GB 内存
按 14 个进程同时 import OCP + 各读一份 shape 会直接打满提交内存，子进程在
``_worker_init`` 阶段就整批死掉，表现为「一个站位都没成功 → 连续失败 3 次 →
改串行」。压到 4–8 个进程就正常。
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager

import numpy as np

logger = logging.getLogger(__name__)

WORKERS_ENV = 'GTAPP_MEASURE_WORKERS'
PARALLEL_ENV = 'GTAPP_MEASURE_PARALLEL'
MAX_WORKERS = 32
#: 默认进程数上限：再往上边际收益很小，内存风险却随进程数线性上升。
DEFAULT_MAX_WORKERS = 8
#: 每个 worker 的粗略内存预算：import OCP + 一份 shape + 一次求交的中间几何。
WORKER_MEMORY_MB = 900
#: 给系统、Flask、浏览器、FreeCAD 留的余量。
MEMORY_RESERVE_MB = 2560

#: worker 进程内缓存的 shape（spawn 出来的子进程各自持有一份）。
_W_SHAPE = None
_W_STEP_PATH = ''


def physical_cores() -> int:
    """物理核数（Windows 走 GetLogicalProcessorInformationEx，失败退回逻辑核数）。"""
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes

            relation_processor_core = 0
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            query = kernel32.GetLogicalProcessorInformationEx
            query.argtypes = [wintypes.DWORD, ctypes.c_void_p,
                              ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            length = wintypes.DWORD(0)
            query(relation_processor_core, None, ctypes.byref(length))
            if length.value:
                buffer = (ctypes.c_char * length.value)()
                if query(relation_processor_core, buffer, ctypes.byref(length)):
                    cores = 0
                    offset = 0
                    while offset + 8 <= length.value:
                        relationship = wintypes.DWORD.from_buffer(buffer, offset).value
                        size = wintypes.DWORD.from_buffer(buffer, offset + 4).value
                        if size <= 0:
                            break
                        if relationship == relation_processor_core:
                            cores += 1
                        offset += size
                    if cores:
                        return cores
        except Exception as exc:  # noqa: BLE001 - 探测失败就用逻辑核数
            logger.debug('[section_pool] 物理核探测失败：%s', exc)
    try:
        return os.cpu_count() or 4
    except Exception:  # noqa: BLE001
        return 4


def available_commit_mb():
    """当前可提交内存（物理可用 + 页面文件可用），取不到返回 None。"""
    if os.name != 'nt':
        return None
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPageFile // (1024 * 1024))
    except Exception as exc:  # noqa: BLE001 - 探测失败就不按内存限流
        logger.debug('[section_pool] 可用内存探测失败：%s', exc)
        return None


def memory_limited_workers(cores: int) -> int:
    """按可用提交内存压低进程数；探测不到就原样返回。"""
    available = available_commit_mb()
    if not available:
        return cores
    budget = available - MEMORY_RESERVE_MB
    if budget <= 0:
        return 1
    return max(1, min(int(cores), int(budget // WORKER_MEMORY_MB)))


def measurement_workers() -> int:
    """站内并行的进程数；1 表示串行。

    显式设置 ``GTAPP_MEASURE_WORKERS`` 时完全尊重用户（只夹在 1..32）；
    否则取 min(物理核数, 8, 内存允许数)，并在日志里说明压到了多少。
    """
    if os.environ.get(PARALLEL_ENV, '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return 1
    raw = os.environ.get(WORKERS_ENV, '').strip()
    if raw:
        try:
            return max(1, min(MAX_WORKERS, int(raw)))
        except ValueError:
            logger.warning('[section_pool] 忽略非法的 %s=%r，改用默认进程数', WORKERS_ENV, raw)
    cores = physical_cores()
    chosen = max(1, min(MAX_WORKERS, DEFAULT_MAX_WORKERS, memory_limited_workers(cores)))
    if chosen < cores:
        available = available_commit_mb()
        logger.info(
            '[section_pool] 物理核 %d，默认压到 %d 进程（可用提交 %s MB，预留 %d MB，'
            '每进程按 %d MB 估）；可用 %s 手动调整',
            cores, chosen,
            '未知' if available is None else '%d' % available,
            MEMORY_RESERVE_MB, WORKER_MEMORY_MB, WORKERS_ENV)
    return chosen


def _worker_init(step_path: str) -> None:
    """子进程只读一次 STEP：后续所有站位复用这份 shape。"""
    global _W_SHAPE, _W_STEP_PATH
    _W_STEP_PATH = step_path
    try:
        from .brep_sections import read_shape

        _W_SHAPE = read_shape(step_path)
    except Exception as exc:  # noqa: BLE001 - 让父进程的站位级回退兜底
        _W_SHAPE = None
        logger.warning('[section_pool] 子进程读取 STEP 失败：%s', exc)


def _worker_shape():
    global _W_SHAPE
    if _W_SHAPE is None:
        from .brep_sections import read_shape

        _W_SHAPE = read_shape(_W_STEP_PATH)
    return _W_SHAPE


def section_task(args):
    """worker 入口：``(index, origin, normal, details, deflection) -> (index, 截面字典)``。

    必须是模块级函数（可 pickle），不能是闭包或类方法。
    """
    index, origin, normal, details, deflection = args
    from .brep_sections import section_shape

    result = section_shape(
        _worker_shape(),
        np.asarray(origin, dtype=float),
        np.asarray(normal, dtype=float),
        details=details,
        deflection=deflection,
    )
    return index, result


@contextmanager
def section_pool(step_path: str):
    """整次测量共用一个进程池；进程数不够或启动失败时 yield ``None``（串行）。"""
    workers = measurement_workers()
    executor = None
    if workers > 1:
        try:
            executor = ProcessPoolExecutor(
                max_workers=workers,
                initializer=_worker_init,
                initargs=(str(step_path),),
            )
            logger.info('[section_pool] 站内并行 %d 进程：%s', workers, step_path)
        except Exception as exc:  # noqa: BLE001 - 建不起池就老实串行
            logger.warning('[section_pool] 进程池启动失败（%s），本次测量回退串行', exc)
            executor = None
    try:
        yield executor
    finally:
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响结果
                logger.warning('[section_pool] 关闭进程池失败：%s', exc)
