# -*- coding: utf-8 -*-
"""FastAPI 入口：上传 → 后台分析 → 轮询结果。静态托管前端与任务帧图。"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, jobs

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Screen Recap", version="1.0.0")

_EXT_RE = re.compile(r"^\.(mp4|mkv|mov|avi|webm|flv|ts|m4v)$", re.I)
_JOB_RE = re.compile(r"^[0-9a-f]{12}$")


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...), language: str = Form("auto")):
    suffix = Path(file.filename or "").suffix.lower()
    if not _EXT_RE.match(suffix):
        raise HTTPException(400, f"不支持的视频格式 {suffix or '(无扩展名)'}，"
                                 "支持 mp4/mkv/mov/avi/webm/flv/ts/m4v")
    if language not in ("auto", "zh", "en", "ja", "ko"):
        raise HTTPException(400, f"不支持的语言 {language}（auto/zh/en/ja/ko）")
    up_path = config.UP_DIR / f"{uuid.uuid4().hex[:12]}{suffix}"
    size = 0
    too_big = False
    # 注意：超限必须先 break 让 with 关掉句柄再删文件——
    # Windows 下 open() 不带 FILE_SHARE_DELETE，持句柄 unlink 必 PermissionError
    with up_path.open("wb") as f:
        while chunk := await file.read(8 * 1024 * 1024):
            size += len(chunk)
            if size > config.MAX_UPLOAD_BYTES:
                too_big = True
                break
            f.write(chunk)
    if too_big:
        up_path.unlink(missing_ok=True)
        raise HTTPException(413, f"文件超过 {config.MAX_UPLOAD_BYTES // 2**20}MB 上限")
    if size == 0:
        up_path.unlink(missing_ok=True)
        raise HTTPException(400, "空文件")
    job_id = jobs.create_job(up_path, language)
    return {"success": True, "job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if not _JOB_RE.match(job_id):
        raise HTTPException(400, "非法 job id")
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在或已过期")
    return {"success": True, "job": {
        "id": job["id"], "status": job["status"], "step": job["step"],
        "progress": job["progress"], "error": job["error"],
        "result": job["result"],
    }}


@app.get("/frames/{job_id}/{name}")
def frame_file(job_id: str, name: str):
    """任务帧图：白名单校验防目录穿越。"""
    if not _JOB_RE.match(job_id) or not re.match(r"^(scene|uni)_\d{4}\.jpg$", name):
        raise HTTPException(400, "非法帧路径")
    path = config.OUT_DIR / job_id / "frames" / name
    if not path.is_file():
        raise HTTPException(404, "帧不存在")
    return FileResponse(path)


@app.get("/api/health")
def health():
    return {"success": True, "llm_configured": bool(config.load_llm_key()),
            "max_upload_mb": config.MAX_UPLOAD_BYTES // 2**20}


app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")
