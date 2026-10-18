# -*- coding: utf-8 -*-
"""任务登记：单 worker 后台线程跑流水线，内存字典 + TTL 清理。

录屏分析是重活（whisper 分钟级），单 worker 串行防止并发把机器打满；
多余请求自然排队，前端轮询进度即可。
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import config

logger = logging.getLogger("screen-recap.jobs")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_worker_started = False
_queue: list[str] = []
_cv = threading.Condition()


def create_job(video_path: Path, language: str = "auto") -> str:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id, "status": "queued", "step": "排队中", "progress": 0,
            "video": str(video_path), "language": language,
            "result": None, "error": None, "created": time.time(),
        }
        _queue.append(job_id)
        _gc_locked()
    with _cv:
        _cv.notify()
    _ensure_worker()
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def update(job_id: str, **kw) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


def _gc_locked() -> None:
    now = time.time()
    # 运行中/排队中的不 GC：超长任务（CPU 转写几小时录屏）不能被中途删状态
    stale = [jid for jid, j in _jobs.items()
             if j["status"] in ("done", "error")
             and now - j["created"] > config.JOB_TTL_SEC]
    for jid in stale:
        del _jobs[jid]
        shutil.rmtree(config.OUT_DIR / jid, ignore_errors=True)  # 磁盘产出同步清


def _ensure_worker() -> None:
    global _worker_started
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    threading.Thread(target=_worker_loop, daemon=True, name="sr-worker").start()


def _worker_loop() -> None:
    from . import pipeline  # 延迟导入：避免循环依赖 + 测试可单独打桩
    try:
        while True:
            with _cv:
                while True:
                    with _lock:
                        if _queue:
                            job_id = _queue.pop(0)
                            break
                    _cv.wait()
            job = get_job(job_id)
            if not job:
                continue
            update(job_id, status="running")
            terminal = "error"
            try:
                result = pipeline.run(
                    Path(job["video"]), job_id, job.get("language") or "auto",
                    progress=lambda step, pct: update(job_id, step=step, progress=pct))
                update(job_id, status="done", progress=100, step="完成", result=result)
                terminal = "done"
            except Exception as exc:  # noqa: BLE001 - 任何失败都要回到任务状态，不能吞
                update(job_id, status="error", step="失败",
                       error=f"{type(exc).__name__}: {exc}")
                # 失败不留半成品磁盘产出（成功才保留帧/result.json 供展示）
                shutil.rmtree(config.OUT_DIR / job_id, ignore_errors=True)
            finally:
                # 终态删上传原件（最大头的磁盘占用）；帧与 result.json 留作展示
                try:
                    Path(job["video"]).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("删上传原件失败 %s: %s", job["video"], exc)
    finally:
        # 线程若意外死亡（原生库 BaseException），复位标记让下个任务能重启 worker
        with _lock:
            global _worker_started
            _worker_started = False
