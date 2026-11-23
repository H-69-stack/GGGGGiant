# -*- coding: utf-8 -*-
"""FreeCADCmd 无头执行（pipe 路默认方式）。

流程（与 ``ENTITY_PIPELINE.md`` §6.2 一致）::

    job json → <out_root>/_scratch/pipe_jobs/job_<uuid8>.json
        {input, output_dir, options, addon_parent, result_json}
    freecadcmd.exe -c "exec(compile(open(r'<runner>', encoding='utf-8').read(), 'runner', 'exec'))"
        env: PIPESOLID_JOB / PYTHONUTF8=1 / PYTHONIOENCODING=utf-8, cwd=仓库根
    <runner> 在 FreeCAD 进程内 import PipeSolidBridge.solidify → 写 result json
    主进程读回 result json，附加耗时，返回 report dict

相对原版的改造：
* 参数用列表传（长中文路径 + 空格安全），代码作为 ``-c`` 的**实参**（已实测可行，
  比 stdin 干净：没有 ``[FreeCAD Console mode]`` 回显和 ``>>>`` 提示符）。
* job/result json 落在显式 ``jobs_dir``，文件名 uuid8，默认保留便于排错。
* 冷启动 / 热执行分开计时：``seconds_process``（子进程墙钟）减去
  ``report['seconds']``（FreeCAD 内几何耗时）≈ 进程冷启动开销。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from . import config


class HeadlessError(RuntimeError):
    """freecadcmd 缺失 / 超时 / 未产出结果 / status=error。"""


def _render_runner_code(runner=None) -> str:
    path = Path(runner or config.RUNNER)
    return 'exec(compile(open(r"%s", encoding="utf-8").read(), "runner", "exec"))' % str(path)


def _hidden_window_kwargs():
    """Windows 下隐藏控制台窗口；freecadcmd.exe 是控制台程序。

    打包版用 pythonw 启动，没有自己的控制台，每次 Popen freecadcmd 都会
    新弹一个黑窗。STARTF_USESHOWWINDOW + SW_HIDE 隐藏窗口，
    CREATE_NO_WINDOW 再保证不创建控制台。非 Windows 返回空 dict。
    """
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def available():
    """(是否可用, freecadcmd 路径或原因) —— 只查文件，不启动进程。"""
    exe = config.freecad_cmd()
    if not exe or not Path(exe).is_file():
        return False, "找不到 freecadcmd.exe（FREECAD_HOME=%s，可用 FREECAD_CMD 覆盖）" % (
            config.CONFIG.get("freecad_home"))
    if not config.RUNNER.is_file():
        return False, "找不到 freecad_runner.py：%s" % config.RUNNER
    if not (config.ADDON / "solidify.py").is_file():
        return False, "找不到插件 PipeSolidBridge：%s" % config.ADDON
    return True, str(exe)


def run(step_path, out_dir, options=None, log=None, *, jobs_dir=None, timeout=1200,
        keep=True, exe=None, runner=None, cwd=None, label=None):
    """在一个全新的 FreeCADCmd 进程里跑 ``solidify.process_file``。

    :param step_path: 待重蒙皮的 STEP
    :param out_dir: FreeCAD 端输出目录（solidify 会写 solid.step/report.json）
    :param options: solidify 选项（direction/half_points/max_sections/export_stl）
    :param jobs_dir: job/result json 目录（默认 ``config.jobs_dir()``）
    :param keep: 是否保留 job/result json
    :param label: 注册到 ``proc_control`` 的标签（默认 ``freecad:<文件名>``）
    :returns: solidify 的 report dict，额外带 ``_job_path`` / ``_result_path`` /
              ``seconds_process`` / ``seconds_backend`` / ``mode``；失败抛 HeadlessError
    """
    opts = dict(options or {})
    exe_path = str(exe) if exe else config.freecad_cmd()
    if not exe_path or not Path(exe_path).is_file():
        raise HeadlessError("找不到 freecadcmd.exe，请检查 FREECAD_HOME（%s）"
                            % config.CONFIG.get("freecad_home"))

    step_path = Path(step_path).resolve()
    if not step_path.is_file():
        raise HeadlessError("输入 STEP 不存在：%s" % step_path)

    jdir = Path(jobs_dir) if jobs_dir else config.jobs_dir()
    jdir.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:8]
    job_path = jdir / ("job_%s.json" % job_id)
    result_path = jdir / ("result_%s.json" % job_id)
    job = {
        "input": str(step_path),
        "output_dir": str(Path(out_dir).resolve()),
        "options": opts,
        "addon_parent": str(config.addon_parent()),
        "result_json": str(result_path),
    }
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    env = os.environ.copy()
    env["PIPESOLID_JOB"] = str(job_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)

    code = _render_runner_code(runner)
    workdir = str(cwd or config.ROOT)
    if log:
        log("FreeCADCmd 无头执行：%s（job %s）" % (step_path.name, job_id))
    t0 = time.time()
    # 注册到 proc_control：force 停止时 taskkill /T /F 能把 FreeCADCmd 连同子孙进程一起清掉。
    # 用 Popen 而不是 subprocess.run，因为 run 会阻塞到超时才返回，中途没有任何句柄可杀。
    try:
        from ... import proc_control  # core.entity.proc_control
    except Exception:  # noqa: BLE001 - 单独复用 vendor/pss 时退化为「不注册」
        proc_control = None
    proc_label = "freecad:%s" % (label or step_path.stem)
    try:
        proc = subprocess.Popen([exe_path, "-c", code], text=True, encoding="utf-8",
                                errors="replace", stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, cwd=workdir,
                                **_hidden_window_kwargs())
    except OSError as exc:
        raise HeadlessError("FreeCADCmd 启动失败：%s（%s）" % (exc, exe_path)) from None
    if proc_control is not None:
        proc_control.register(proc, proc_label)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 超时：先按树杀（taskkill /T /F），再兜底 kill，避免 FreeCADCmd 挂死占端口
            try:
                if proc_control is not None:
                    proc_control.terminate_tree(proc.pid)
                else:
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.communicate(timeout=10)
            except Exception:  # noqa: BLE001
                pass
            raise HeadlessError("FreeCADCmd 处理超时（%ss）：%s"
                                % (timeout, step_path.name)) from None
    finally:
        # 正常/异常/超时路径都必须出表，否则 stop 会去杀一个已结束的 pid
        if proc_control is not None:
            proc_control.unregister(proc)
    seconds_process = round(time.time() - t0, 3)

    output = ((stdout or "") + "\n" + (stderr or "")).strip()
    if log:
        log("FreeCADCmd 退出码 %s，墙钟 %.2fs" % (proc.returncode, seconds_process))
    if not result_path.is_file():
        raise HeadlessError("FreeCADCmd 未返回结果（退出码 %s）：%s"
                            % (proc.returncode, output[-1200:]))
    try:
        report = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HeadlessError("结果 json 解析失败（%s）：%s" % (exc, result_path)) from None
    if not keep:
        for p in (job_path, result_path):
            try:
                p.unlink()
            except Exception:
                pass

    if report.get("status") != "ok":
        raise HeadlessError(report.get("message") or "FreeCADCmd 处理失败（status=%s）"
                            % report.get("status"))

    report["_job_path"] = str(job_path)
    report["_result_path"] = str(result_path)
    report["seconds_process"] = seconds_process
    report["seconds_backend"] = seconds_process
    report["mode"] = "headless"
    return report


def cold_start_seconds(exe=None, timeout=120, log=None):
    """测一次 FreeCADCmd 进程启动开销（跑空代码，不含几何）。"""
    exe_path = str(exe) if exe else config.freecad_cmd()
    if not exe_path:
        return None
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    try:
        proc = subprocess.run([exe_path, "-c", "print('ok')"], text=True, encoding="utf-8",
                              errors="replace", capture_output=True, env=env,
                              cwd=str(config.ROOT), timeout=timeout,
                              **_hidden_window_kwargs())
    except Exception as exc:  # noqa: BLE001
        if log:
            log("冷启动测量失败：%s" % exc)
        return None
    dt = round(time.time() - t0, 3)
    if log:
        log("FreeCADCmd 冷启动 %.3fs（退出码 %s）" % (dt, proc.returncode))
    return dt


__all__ = ["HeadlessError", "available", "run", "cold_start_seconds"]
