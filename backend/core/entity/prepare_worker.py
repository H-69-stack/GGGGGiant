# -*- coding: utf-8 -*-
"""prepare 子进程：一次「测量 + 导出模型」，跑完写 result json。

为什么是独立进程
----------------
``giant_adapter.measure``（``core.pp.tube.analyze_tube``）与 ``export_models``
（RFM processor + OCP）都**不可中断**：内部没有停止点，``stop_event`` 传不进去。
放在子进程里，停止时直接把整棵进程树 ``taskkill /T /F``，几秒内就能回到 UI。

调用方式（由 :mod:`core.entity.prepare_proc` 拉起）::

    python -m core.entity.prepare_worker <job.json>

环境要求：``cwd=<repo>/backend``、``PYTHONPATH=<repo>/backend``、
``PYTHONUTF8=1`` / ``PYTHONIOENCODING=utf-8``（父进程 ``text=True, encoding="utf-8"``
读 stdout，中文日志不能按 cp936 写）。

job json（§4.5）::

    {part, step, end_treatment, out_root, models, force, result_path}

result json::

    {"models": {key: step_path}, "measured_path": str, "seconds": float, "cached": bool}

stdout 每行都是进度（``flush=True``，父进程逐行转日志）；异常打印 traceback 到
stderr 并以退出码 1 结束（父进程据此抛 ``PrepareFailed``）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

#: ``__main__`` 里 reconfigure 之后，父进程按 UTF-8 读到的中文日志才不乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 老 Python / 被重定向的流没有 reconfigure
        pass


def _log(message) -> None:
    """进度行：一行一条，立刻 flush（父进程逐行读）。"""
    try:
        print(message, flush=True)
    except Exception:  # noqa: BLE001 - 日志永不打断几何
        pass


def _job_from_argv(argv) -> dict:
    if len(argv) < 2:
        raise SystemExit("用法：python -m core.entity.prepare_worker <job.json>")
    job_path = argv[1]
    with open(job_path, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    if not isinstance(job, dict):
        raise SystemExit("job json 必须是对象：%s" % job_path)
    job["_job_path"] = job_path
    return job


def run_job(job: dict) -> dict:
    """执行一次 prepare：测量（带缓存）→ 导出模型 → 返回 result dict。

    ``job["measure_only"]=True`` 时只测量、跳过模型导出（上传测量用）。
    只在本进程内跑，不注册 ``proc_control``（父进程注册的是这个 worker 本身）。
    """
    # cwd / PYTHONPATH 由父进程保证（backend 目录），这里按模块名导入即可
    from core.entity import contracts, giant_adapter, settings

    started = time.monotonic()
    part = str(job.get("part") or "").strip()
    if not part:
        raise ValueError("job 缺少 part")
    step = job.get("step")
    if not step:
        raise ValueError("job 缺少 step（原始管件 STEP 路径）")
    step = os.path.abspath(os.fspath(step))
    if not os.path.isfile(step):
        raise FileNotFoundError("原始管件 STEP 不存在：%s" % step)

    out_root = os.path.abspath(os.fspath(job.get("out_root") or settings.OUT_ROOT))
    end_treatment = str(
        job.get("end_treatment") or contracts.end_treatment_for(part)
    ).lower()
    wanted = job.get("models")
    if wanted:
        wanted = [str(key) for key in wanted]
    else:
        wanted = list(contracts.MODEL_KEYS)
    force = bool(job.get("force"))
    measure_only = bool(job.get("measure_only"))

    cache_path = str(settings.measured_path(part, end_treatment, out_root))
    _log("[%s] prepare 开始：%s（口径 %s，%s，force=%s）"
         % (part, os.path.basename(step), end_treatment,
            "仅测量" if measure_only else "%d 类模型" % len(wanted), force))
    _log("[%s] 测量缓存：%s" % (part, cache_path))

    measured = giant_adapter.measure(
        step,
        part=part,
        end_treatment=end_treatment,
        cache_path=cache_path,
        force=force,
        log=_log,
    )
    cached = bool(getattr(measured, "cached", False))
    _log("[%s] 测量完成（缓存命中=%s）" % (part, cached))

    if measure_only:
        seconds = round(time.monotonic() - started, 3)
        _log("[%s] 仅测量完成（不导出模型），%.1f s" % (part, seconds))
        return {
            "models": {},
            "measured_path": cache_path,
            "seconds": seconds,
            "cached": cached,
            "measure_only": True,
        }

    models_dir = settings.part_paths(out_root, part)["models"]
    _log("[%s] 导出模型 → %s" % (part, models_dir))
    exported = giant_adapter.export_models(
        part,
        measured,
        str(models_dir),
        models=wanted,
        log=_log,
        # 用中文键命名：与 orchestrator 的 00_models/<中文键>.stp 同一口径
        naming="key",
    ) or {}
    paths = giant_adapter.step_paths(exported)

    models: dict[str, str] = {}
    for key in wanted:
        path = paths.get(key)
        if not path:
            # 与 orchestrator._model_path 的回落一致：给出预期路径，让下游报「文件不存在」
            path = os.path.join(str(models_dir), "%s.stp" % key)
        models[key] = os.path.abspath(path)

    skipped = {
        key: record.get("reason")
        for key, record in exported.items()
        if isinstance(record, dict) and record.get("status") != "ok"
    }
    for key, reason in skipped.items():
        _log("[%s] ! %s 未导出：%s" % (part, key, reason))

    seconds = round(time.monotonic() - started, 3)
    _log("[%s] prepare 完成：%d/%d 类模型，%.1f s"
         % (part, len(paths), len(wanted), seconds))
    result = {
        "models": models,
        "measured_path": cache_path,
        "seconds": seconds,
        "cached": cached,
    }
    if skipped:
        result["skipped"] = skipped
    return result


def _write_result(job: dict, result: dict) -> None:
    result_path = job.get("result_path")
    if not result_path:
        raise ValueError("job 缺少 result_path")
    result_path = os.path.abspath(os.fspath(result_path))
    parent = os.path.dirname(result_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = result_path + ".part"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, result_path)  # 半截 json 永不会被父进程当成结果


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    job = None
    try:
        job = _job_from_argv(argv)
        result = run_job(job)
        _write_result(job, result)
        return 0
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - 任何异常都要让父进程看到 traceback + 退出码 1
        traceback.print_exc()
        # 有 result_path 就顺手写个失败标记（父进程仍以退出码/缺失 json 判断）
        try:
            if job and job.get("result_path"):
                _write_result(job, {"status": "error", "message": traceback.format_exc()})
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
