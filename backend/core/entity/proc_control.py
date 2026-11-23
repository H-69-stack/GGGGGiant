# -*- coding: utf-8 -*-
"""子进程注册表：流水线「停止」的可停止内核。

为什么需要它
------------
一体化流水线里有两类**不在本进程内**的长任务：

* ``prepare_worker``（测量 + 导出模型，OCP 几何，可能几分钟到几十分钟）；
* ``freecadcmd.exe``（pipe 路重蒙皮，单次最长 1200 s）。

OCP / FreeCAD 都是**非线程安全且不可中断**的：协作式 ``stop_event`` 只有在它们
自己的循环里才看得到，而 ``analyze_tube`` / ``solidify`` 内部没有停止点。唯一
可靠的停法是**杀进程**。所以凡是这类子进程，创建后就 :func:`register` 到本模块，
``POST /stop?mode=force`` 时 :func:`terminate_all` 一次性清掉。

跨平台策略
----------
* Windows：``taskkill /PID <pid> /T /F`` 杀整棵进程树（freecadcmd 若再拉起子进程
  也跑不掉）；失败兜底 ``proc.kill()``。
* 其它平台：子进程用 ``start_new_session=True`` 起（见 ``prepare_proc``），这里
  ``os.killpg(pgid, SIGKILL)`` 杀进程组；pgid 与自身同组时只 ``os.kill(pid)``，
  绝不误伤本进程。

线程安全
--------
注册表是一把 ``threading.RLock`` 保护的 ``{pid: record}``：

* 所有读写（register / unregister / active / 取快照）都在锁内完成，调用方
  可以是 Flask 请求线程、编排线程、任意工作线程；
* 锁内**只做字典操作**，不做 ``wait`` / ``taskkill`` 这类阻塞动作 —— 杀进程前
  先复制快照、释放锁，避免一个慢 ``taskkill`` 把别的线程卡在 ``register`` 上；
* :func:`active` 顺手剔除已经退出的进程（``poll() is not None``），注册表不会
  随失败重试无限增长；
* 保护名单里是 ``os.getpid()`` / ``os.getppid()``，绝不杀死当前进程自己。

对外契约（docs/一体化流水线改造方案_2026-11-06.md §4.4）::

    register(proc, label) -> None
    unregister(proc) -> None
    active() -> [{"pid":.., "label":.., "started":..}]
    terminate_all(timeout=5.0) -> list[str]      # 返回被杀掉的 label
    terminate_tree(pid) -> None                  # Windows: taskkill /T /F
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from datetime import datetime

__all__ = [
    "active",
    "register",
    "terminate_all",
    "terminate_tree",
    "unregister",
]

def _safe_getppid() -> int:
    try:
        return int(os.getppid())
    except Exception:  # noqa: BLE001 - 极端平台没有 getppid
        return 0


#: 全局注册表 ``{pid: {"proc": Popen, "label": str, "started": str, "t0": float}}``
_PROCS: dict[int, dict] = {}
#: 保护名单：本进程 + 父进程（taskkill /T 顺着树走，绝不能从这里往上杀）
_PROTECTED_PIDS: frozenset[int] = frozenset(
    pid for pid in (os.getpid(), _safe_getppid()) if isinstance(pid, int) and pid > 0
)
#: 注册表锁。RLock：register/unregister/active 内部可能互相调用，可重入更稳。
_LOCK = threading.RLock()

#: ``taskkill`` 超时（正常几十毫秒；进程已消失时会立刻返回）
_TASKKILL_TIMEOUT = 15.0


def _pid_of(proc) -> int:
    """``Popen`` / ``int`` → pid；拿不到返回 0。"""
    if isinstance(proc, int):
        return proc
    try:
        return int(getattr(proc, "pid", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- 注册
def register(proc, label: str) -> None:
    """把子进程登记进注册表（同一 pid 重复登记会覆盖为最新 label）。"""
    pid = _pid_of(proc)
    if pid <= 0:
        return
    record = {"proc": proc, "label": str(label or pid), "started": _now_iso(), "t0": time.time()}
    with _LOCK:
        _PROCS[pid] = record


def unregister(proc) -> None:
    """从注册表移除（幂等；接受 Popen 对象或 pid）。

    正常路径由调用方在 ``finally`` 里调用；``active()`` / ``terminate_all()`` 也会
    顺手剔除已退出的记录，所以漏调不会造成泄漏。
    """
    pid = _pid_of(proc)
    if pid <= 0:
        return
    with _LOCK:
        _PROCS.pop(pid, None)


def active() -> list[dict]:
    """当前仍在跑的子进程；已退出的（``poll() is not None``）自动出表。"""
    alive: list[dict] = []
    with _LOCK:
        for pid, record in list(_PROCS.items()):
            proc = record.get("proc")
            if proc is None or _poll(proc) is not None:
                _PROCS.pop(pid, None)
                continue
            alive.append(
                {
                    "pid": pid,
                    "label": record.get("label"),
                    "started": record.get("started"),
                }
            )
    return alive


def _poll(proc):
    """``proc.poll()``；对象异常（已关闭 / 非 Popen）时当已退出处理。"""
    try:
        return proc.poll()
    except Exception:  # noqa: BLE001
        return -1


def _lookup(pid: int):
    with _LOCK:
        return _PROCS.get(pid)


# --------------------------------------------------------------------------- 杀
def _is_protected(pid: int) -> bool:
    """是否是不许杀的目标：本进程、父进程、非法 pid。"""
    return pid <= 0 or pid in _PROTECTED_PIDS or pid == os.getpid()


def terminate_tree(pid: int) -> None:
    """杀掉 ``pid`` 及其整棵进程树（尽力而为，不抛异常）。

    * Windows：``taskkill /PID <pid> /T /F``；进程还在就再 ``proc.kill()``；
    * 其它：``os.killpg(pgid, SIGKILL)``（子进程须以 ``start_new_session=True`` 起），
      pgid 与当前进程同组时退化为 ``os.kill(pid, SIGKILL)``。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if _is_protected(pid):
        return

    if os.name == "nt":
        _taskkill(pid)
        record = _lookup(pid)
        proc = (record or {}).get("proc")
        if proc is not None:
            # taskkill 是同步的，但进程完全消失可能晚几十毫秒；短暂等一下再兜底
            for _ in range(15):
                if _poll(proc) is not None:
                    break
                time.sleep(0.1)
            if _poll(proc) is None:
                _safe_call(proc.kill)
        return

    # ---- POSIX：优先杀进程组（prepare_proc / headless 都用 start_new_session）
    pgid = None
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    own_pgid = None
    try:
        own_pgid = os.getpgrp()
    except OSError:
        own_pgid = None
    sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    if pgid and pgid != own_pgid and pgid not in _PROTECTED_PIDS:
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass
    # 进程组单独起时上面已经杀掉；兜底再对 pid 本身发一次（重复 kill 无害）
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def _taskkill(pid: int) -> None:
    """``taskkill /PID <pid> /T /F``（抑制输出；进程不存在时静默失败）。"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TASKKILL_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001 - taskkill 缺失 / 超时都不该影响停止流程
        pass


def _safe_call(func, *args) -> None:
    try:
        func(*args)
    except Exception:  # noqa: BLE001
        pass


def terminate_all(timeout: float = 5.0) -> list[str]:
    """停掉注册表里所有子进程，返回被杀掉的 label 列表。

    两档：先 ``proc.terminate()``（礼貌），最多等 ``timeout`` 秒；仍活着的再
    :func:`terminate_tree`（Windows 杀整棵树）并 ``proc.kill()`` 兜底。

    注意（Windows）：``proc.terminate()`` 是 ``TerminateProcess``，只结束该 pid 本身；
    如果子进程又拉了孙进程且已经先被 terminate 掉，后面的 ``taskkill /T`` 找不到父
    pid 就管不到孙进程。当前的注册对象（prepare_worker / freecadcmd）都不派生子进程，
    所以这个缺口只在理论上存在；真出现时应在子进程启动处改成 job object 或直接
    走 ``terminate_tree``。

    先复制快照再释放锁：``terminate`` / ``wait`` / ``taskkill`` 都是阻塞动作，
    不能压在全局锁里（否则并发的 ``register`` 会被卡住，新进程反而漏杀）。
    """
    try:
        timeout = max(0.0, float(timeout))
    except (TypeError, ValueError):
        timeout = 5.0

    with _LOCK:
        snapshot = list(_PROCS.items())

    targets: list[tuple[int, dict]] = []
    killed: list[str] = []
    for pid, record in snapshot:
        proc = record.get("proc")
        if _is_protected(pid):
            continue
        if proc is None or _poll(proc) is not None:
            unregister(pid)  # 已经自己退出了，顺手清表
            continue
        targets.append((pid, record))
        # 1) 礼貌阶段
        _safe_call(proc.terminate)

    if targets:
        deadline = time.time() + timeout
        while time.time() < deadline and any(_poll(rec["proc"]) is None for _pid, rec in targets):
            time.sleep(0.1)
        # 2) 强制阶段：礼貌没停住的，杀整棵树
        for pid, record in targets:
            proc = record.get("proc")
            label = str(record.get("label") or pid)
            if _poll(proc) is None:
                terminate_tree(pid)
                _safe_call(proc.kill)
            try:
                proc.wait(timeout=3.0)
            except Exception:  # noqa: BLE001 - 已退出 / 杀不掉都不影响返回
                pass
            killed.append(label)
            unregister(pid)

    # 收尾：清理期间被别的线程干掉/已退出的记录
    with _LOCK:
        for pid, record in list(_PROCS.items()):
            proc = record.get("proc")
            if proc is None or _poll(proc) is not None:
                _PROCS.pop(pid, None)
    return killed
