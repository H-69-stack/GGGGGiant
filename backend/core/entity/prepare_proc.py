# -*- coding: utf-8 -*-
"""prepare 子进程编排：可停止的「测量 + 导出模型」。

为什么不是本进程内直接调 ``giant_adapter``
------------------------------------------
``analyze_tube`` / ``export_models`` 里的 OCP 调用**不可中断**，线程也停不下来
（OCP 非线程安全，只能串行）。把这段工作放进 ``prepare_worker`` 子进程后，
``POST /stop`` 只要能杀掉这个进程树，测量/导出就立刻结束 —— 这是「停止停不了」
问题的核心修法（docs/一体化流水线改造方案_2026-11-06.md §4.5）。

流程::

    写 job json → <out_root>/_scratch/prepare_jobs/job_<uuid>.json
    Popen([python, -m core.entity.prepare_worker, job], cwd=<repo>/backend,
          env 带 PYTHONPATH=<repo>/backend, stdout=PIPE, stderr=STDOUT, text=True)
    注册到 proc_control（label = "prepare:<part>"）
    读线程逐行搬 stdout → log；主循环每 0.2 s 查一次 stop_event / 超时
    停止/超时 → terminate_tree(pid)（Windows: taskkill /T /F）
    退出码非 0 或 result json 缺失 → PrepareFailed（附最后 20 行输出）

对外契约（§4.5）::

    class PrepareCancelled(RuntimeError)
    class PrepareFailed(RuntimeError)
    run_prepare(*, part, step, end_treatment, out_root, models=None, force=False,
                log=print, stop_event=None, timeout=7200.0) -> dict
    # {"models": {key: step_path}, "measured_path": str, "seconds": float, "cached": bool}
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from . import proc_control, settings

__all__ = ["PrepareCancelled", "PrepareFailed", "run_prepare"]

#: worker 模块名（``python -m <WORKER_MODULE> <job.json>``）
WORKER_MODULE = "core.entity.prepare_worker"

#: 读 stdout / 查停止标志的轮询间隔（秒）
POLL_INTERVAL = 0.2

#: 失败信息里附带的最后若干行输出
TAIL_LINES = 20


class PrepareCancelled(RuntimeError):
    """用户点了「停止」：子进程树已杀掉，本次 prepare 作废。"""


class PrepareFailed(RuntimeError):
    """prepare 失败：启动失败 / 超时 / 退出码非 0 / 没写 result json。"""


# --------------------------------------------------------------------------- 环境
def _backend_dir() -> Path:
    return Path(getattr(settings, "BACKEND_DIR", Path(settings.ROOT) / "backend"))


def jobs_dir(out_root=None) -> Path:
    """job/result json 目录：``<out_root>/_scratch/prepare_jobs``。"""
    root = Path(out_root or settings.OUT_ROOT)
    path = root / "_scratch" / "prepare_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _child_env() -> dict:
    """子进程环境：PYTHONPATH 指到 backend，UTF-8 输出，去掉会改写解释器根目录的 PYTHONHOME。"""
    env = os.environ.copy()
    backend = str(_backend_dir())
    existing = [p for p in str(env.get("PYTHONPATH") or "").split(os.pathsep) if p]
    if backend not in existing:
        existing.insert(0, backend)
    env["PYTHONPATH"] = os.pathsep.join(existing)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)
    return env


def _popen_kwargs() -> dict:
    """跨平台启动参数：Windows 无窗口；POSIX 单开进程组（方便 killpg 整组）。"""
    kwargs = {
        "cwd": str(_backend_dir()),
        "env": _child_env(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


# --------------------------------------------------------------------------- 小工具
def _emit(log, message: str) -> None:
    """日志永不打断流程。"""
    if log is None:
        return
    try:
        log(message)
    except Exception:  # noqa: BLE001
        pass


def _stop_requested(stop_event) -> bool:
    if stop_event is None:
        return False
    checker = getattr(stop_event, "is_set", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:  # noqa: BLE001
        return False


def _tail(tail: deque, limit: int = TAIL_LINES) -> str:
    lines = list(tail)[-limit:]
    return "\n".join(lines)


def _kill_tree(proc, timeout: float = 5.0) -> None:
    """杀整棵进程树并回收句柄（尽力而为）。"""
    pid = int(getattr(proc, "pid", 0) or 0)
    if pid:
        try:
            proc_control.terminate_tree(pid)
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 - 已经退出
        pass
    try:
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001 - 超时/无 wait 都不影响返回
        pass


# --------------------------------------------------------------------------- 入口
def run_prepare(
    *,
    part: str,
    step,
    end_treatment,
    out_root,
    models=None,
    force: bool = False,
    measure_only: bool = False,
    log=print,
    stop_event=None,
    timeout: float = 7200.0,
    command=None,
) -> dict:
    """在子进程里跑「测量 + 导出模型」，可被 ``stop_event`` 立即打断。

    参数
    ----
    part
        件号。
    step
        原始管件 STEP 路径。
    end_treatment
        ``"c1"`` / ``"v1"``（121 保形走 ``v1``）。
    out_root
        输出根；job json 落 ``<out_root>/_scratch/prepare_jobs/``。
    models
        只导出指定模型键（缺省全出）。
    force
        True 时忽略测量缓存重测。
    measure_only
        True 时只测量、不导出 5 类模型（上传测量用；结果里 ``models`` 为空）。
    log
        一行一条的日志回调（默认 ``print``）。
    stop_event
        ``threading.Event``；一旦 set，立刻杀子树并抛 :class:`PrepareCancelled`。
    timeout
        墙钟超时（秒，默认 7200）；超时同样杀子树，抛 :class:`PrepareFailed`。
    command
        **仅供测试**：覆盖 ``argv``（默认 ``[python, "-m", WORKER_MODULE, job]``），
        自测时可用 ``python -c "import time;time.sleep(30)"`` 之类的假命令验证停止路径。

    返回
    ----
    ``{"models": {key: step_path}, "measured_path": str, "seconds": float, "cached": bool}``
    ``seconds`` 是父进程看到的墙钟（含子进程启动），``models`` 里只含实际写出的路径
    （导出失败/跳过的模型给预期路径，交给下游报「STEP 不存在」）。
    ``measure_only=True`` 时只测量，``models`` 为空、``measured_path`` 仍指向测量缓存。
    """
    part = str(part)
    try:
        timeout = float(timeout or 0)
    except (TypeError, ValueError):
        timeout = 0.0

    if _stop_requested(stop_event):
        raise PrepareCancelled("prepare 已取消（启动前就收到停止请求）：%s" % part)

    out_root = Path(out_root or settings.OUT_ROOT)
    jdir = jobs_dir(out_root)
    token = uuid.uuid4().hex[:12]
    job_path = jdir / ("job_%s.json" % token)
    result_path = jdir / ("job_%s.result.json" % token)

    wanted = [str(key) for key in models] if models else None
    job = {
        "part": part,
        "step": str(Path(os.fspath(step)).resolve()) if step else "",
        "end_treatment": str(end_treatment),
        "out_root": str(out_root),
        "models": wanted,
        "force": bool(force),
        "measure_only": bool(measure_only),
        "result_path": str(result_path),
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    if command:
        argv = [str(item) for item in command]
        if not any(str(item) == str(job_path) for item in argv):
            argv.append(str(job_path))  # 假命令也带上 job，便于事后排查
    else:
        argv = [sys.executable, "-m", WORKER_MODULE, str(job_path)]

    label = "prepare:%s" % part
    _emit(log, "[%s] prepare 子进程启动：%s" % (part, " ".join(argv[:3])))
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv, **_popen_kwargs())
    except OSError as exc:
        raise PrepareFailed("prepare 子进程启动失败：%s（%s）" % (exc, argv[0])) from None

    proc_control.register(proc, label)
    cancelled = False
    timed_out = False
    tail: deque = deque(maxlen=TAIL_LINES)
    result: dict = {}

    # --- 读线程：阻塞读 stdout，主循环靠 queue 超时保持可响应（0.2 s 一轮）
    stream = proc.stdout
    lines: "queue.Queue" = queue.Queue()

    def _pump() -> None:
        try:
            for line in stream:
                lines.put(line)
        except Exception:  # noqa: BLE001 - 管道被 kill 掉很正常
            pass
        finally:
            lines.put(None)

    def _forward(text: str) -> None:
        line = str(text).rstrip("\r\n")
        if not line:
            return
        tail.append(line)
        _emit(log, line)

    pump = threading.Thread(target=_pump, name="prepare-pump-%s" % part, daemon=True)
    pump.start()

    deadline = (started + timeout) if timeout > 0 else None
    try:
        while True:
            try:
                chunk = lines.get(timeout=POLL_INTERVAL)
            except queue.Empty:
                chunk = ""

            if chunk is None:
                break
            if chunk:
                _forward(chunk)
                continue

            # 本轮 0.2 s 内没有新输出：查停止 / 超时 / 是否已退出
            if _stop_requested(stop_event):
                cancelled = True
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            if proc.poll() is not None:
                # 子进程没了，但管道里可能还有尾部输出：再等一轮，读不到就收尾
                try:
                    chunk = lines.get(timeout=0.5)
                except queue.Empty:
                    break
                if chunk is None:
                    break
                _forward(chunk)

        # 收尾：把此刻还能拿到的输出全部转成日志（错误信息要用最后 20 行）
        while True:
            try:
                chunk = lines.get_nowait()
            except queue.Empty:
                break
            if chunk is None:
                break
            _forward(chunk)

        if cancelled:
            _kill_tree(proc)
            raise PrepareCancelled(
                "prepare 已取消：%s（子进程 %s 已杀掉）" % (part, label)
            )
        if timed_out:
            _kill_tree(proc)
            raise PrepareFailed(
                "prepare 超时（%.0f s）：%s\n最后输出：\n%s" % (timeout, part, _tail(tail))
            )

        try:
            returncode = proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise PrepareFailed(
                "prepare 子进程未退出（读不到输出但进程仍在）：%s\n最后输出：\n%s"
                % (part, _tail(tail))
            ) from None

        if returncode != 0:
            raise PrepareFailed(
                "prepare 失败（退出码 %s）：%s\n最后输出：\n%s"
                % (returncode, part, _tail(tail))
            )
        if _stop_requested(stop_event):
            # 子进程刚好跑完，但用户在此期间点了停止：按取消处理，不把结果当成功
            raise PrepareCancelled("prepare 已取消：%s（子进程已完成）" % part)
        if not result_path.is_file():
            raise PrepareFailed(
                "prepare 未写出结果（退出码 0 但 %s 不存在）：%s\n最后输出：\n%s"
                % (result_path.name, part, _tail(tail))
            )
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise PrepareFailed(
                "prepare 结果 json 解析失败（%s）：%s\n最后输出：\n%s"
                % (exc, result_path, _tail(tail))
            ) from None
        if not isinstance(result, dict) or result.get("status") == "error":
            message = (result or {}).get("message") if isinstance(result, dict) else None
            raise PrepareFailed(
                "prepare 报告失败：%s\n%s" % (part, message or _tail(tail))
            )

        models_out = result.get("models") if isinstance(result.get("models"), dict) else {}
        models_out = {
            str(key): os.fspath(path)
            for key, path in models_out.items()
            if isinstance(path, (str, os.PathLike)) and str(path)
        }
        seconds = round(time.monotonic() - started, 3)
        _emit(log, "[%s] prepare 子进程退出 0（%.1f s，%d 类模型）"
              % (part, seconds, len(models_out)))
        return {
            "models": models_out,
            "measured_path": str(result.get("measured_path") or ""),
            "seconds": seconds,
            "cached": bool(result.get("cached")),
        }
    finally:
        proc_control.unregister(proc)
