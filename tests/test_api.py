# -*- coding: utf-8 -*-
"""API 与流水线：上传校验 / job 生命周期 / 帧路径白名单 / 流水线编排（重活全打桩）。"""
import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app as app_mod
from backend import config, jobs, pipeline


@pytest.fixture()
def client():
    return TestClient(app_mod.app)


def _fake_video_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 256


class TestUploadValidation:
    def test_rejects_bad_extension(self, client):
        r = client.post("/api/analyze",
                        files={"file": ("a.txt", io.BytesIO(b"hi"), "text/plain")})
        assert r.status_code == 400
        assert "格式" in r.json()["detail"]

    def test_rejects_empty_file(self, client):
        r = client.post("/api/analyze",
                        files={"file": ("a.mp4", io.BytesIO(b""), "video/mp4")})
        assert r.status_code == 400

    def test_rejects_bad_language(self, client):
        r = client.post("/api/analyze",
                        files={"file": ("a.mp4", io.BytesIO(_fake_video_bytes()),
                                        "video/mp4")},
                        data={"language": "klingon"})
        assert r.status_code == 400

    def test_over_limit_413_and_no_leaked_file(self, client, monkeypatch):
        """超限：413 且半成品文件必须删掉（Windows 持句柄 unlink 曾踩坑）。"""
        monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
        before = set(config.UP_DIR.glob("*"))
        r = client.post("/api/analyze",
                        files={"file": ("a.mp4", io.BytesIO(_fake_video_bytes()),
                                        "video/mp4")})
        assert r.status_code == 413
        assert set(config.UP_DIR.glob("*")) == before

    def test_accepts_video_and_creates_job(self, client):
        r = client.post("/api/analyze",
                        files={"file": ("a.mp4", io.BytesIO(_fake_video_bytes()),
                                        "video/mp4")})
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        job = jobs.get_job(job_id)
        assert job is not None and job["status"] in ("queued", "running", "done", "error")


class TestJobEndpoints:
    def test_illegal_job_id(self, client):
        assert client.get("/api/jobs/../../etc").status_code in (400, 404, 422)
        assert client.get("/api/jobs/nothex!!").status_code == 400

    def test_missing_job(self, client):
        assert client.get("/api/jobs/" + "0" * 12).status_code == 404

    def test_frame_path_whitelist(self, client):
        assert client.get("/frames/" + "0" * 12 + "/../secret").status_code in (400, 404)
        assert client.get("/frames/bad!!/scene_0001.jpg").status_code == 400
        assert client.get("/frames/" + "0" * 12 + "/evil.jpg").status_code == 400


class TestPipeline:
    def _stub_heavy(self, monkeypatch, tmp_path, with_llm=True):
        """ffmpeg/STT/OCR/LLM 全打桩：流水线编排纯内存跑。"""
        monkeypatch.setattr(pipeline.ffmpegx, "probe_duration", lambda p: 30.0)

        def _audio(src, dst):
            dst.write_bytes(b"wav")
        monkeypatch.setattr(pipeline.ffmpegx, "extract_audio", _audio)

        monkeypatch.setattr(pipeline.stt, "transcribe", lambda p, language=None, progress_cb=None: {
            "text": "今天介绍杭州旅游攻略", "language": "zh", "duration": 30.0,
            "segments": [{"start": 0.0, "end": 5.0, "text": "今天介绍杭州旅游攻略"}]})
        monkeypatch.setattr(pipeline.stt, "device_info", lambda: "test-stub")

        def _scene(src, out_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            return [(float(i * 10), out_dir / f"scene_{i:04d}.jpg") for i in range(3)]

        def _scene_write(src, out_dir):
            shots = _scene(src, out_dir)
            for _, p in shots:
                p.write_bytes(b"\xff\xd8\xff")  # 假 jpg 头即可（OCR 已打桩）
            return shots
        monkeypatch.setattr(pipeline.ffmpegx, "extract_scene_frames", _scene_write)
        monkeypatch.setattr(pipeline.frame_sel, "select_frames",
                            lambda shots, limit, th: shots)
        monkeypatch.setattr(pipeline.ocr, "recognize",
                            lambda p: {"text": "西湖 断桥", "lines": []})
        if with_llm:
            monkeypatch.setattr(pipeline.llm, "analyze",
                                lambda segs, frames, fmt: {"topic": "杭州旅游攻略"})
        else:
            def _no_key(segs, frames, fmt):
                raise RuntimeError("未配置 LLM key")
            monkeypatch.setattr(pipeline.llm, "analyze", _no_key)

    def test_pipeline_full(self, monkeypatch, tmp_path):
        self._stub_heavy(monkeypatch, tmp_path)
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")
        progress_log = []
        result = pipeline.run(video, "jobtest00001", "auto",
                              progress=lambda s, p: progress_log.append((s, p)))

        assert result["duration"] == 30.0
        assert result["transcript"] == "今天介绍杭州旅游攻略"
        assert len(result["frames"]) == 3
        assert result["frames"][0]["ocr"] == "西湖 断桥"
        assert result["analysis"] == {"topic": "杭州旅游攻略"}
        assert result["analysis_error"] == ""
        # 进度单调不减
        pcts = [p for _, p in progress_log]
        assert pcts == sorted(pcts)
        # result.json 落盘
        assert (tmp_path / "out" / "jobtest00001" / "result.json").is_file()

    def test_pipeline_without_llm_key_still_returns_local(self, monkeypatch, tmp_path):
        self._stub_heavy(monkeypatch, tmp_path, with_llm=False)
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")
        result = pipeline.run(video, "jobtest00002", "auto")
        assert result["analysis"] is None
        assert "未配置" in result["analysis_error"]
        assert result["transcript"]  # 本地成果不受影响

    def test_pipeline_no_audio_degrades(self, monkeypatch, tmp_path):
        self._stub_heavy(monkeypatch, tmp_path)

        def _no_audio(src, dst):
            raise RuntimeError("no audio stream")
        monkeypatch.setattr(pipeline.ffmpegx, "extract_audio", _no_audio)
        video = tmp_path / "in.mp4"
        video.write_bytes(b"x")
        result = pipeline.run(video, "jobtest00003", "auto")
        assert result["transcript"] == ""
        assert result["analysis"] is not None  # 仍有画面分析

    def test_worker_completes_job(self, client, monkeypatch, tmp_path):
        """端到端：上传 → worker 跑打桩流水线 → 轮询到 done。"""
        monkeypatch.setattr("backend.pipeline.run", lambda video, jid, lang, progress: {
            "job_id": jid, "video_name": video.name, "duration": 1.0,
            "duration_label": "00:01", "stt_device": "stub", "language": "zh",
            "transcript": "t", "segments": [], "frames": [],
            "analysis": {"topic": "x"}, "analysis_error": "",
        })
        r = client.post("/api/analyze",
                        files={"file": ("a.mp4", io.BytesIO(_fake_video_bytes()),
                                        "video/mp4")})
        job_id = r.json()["job_id"]
        for _ in range(50):
            d = client.get("/api/jobs/" + job_id).json()["job"]
            if d["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert d["status"] == "done"
        assert d["result"]["analysis"]["topic"] == "x"

    def test_worker_error_cleans_upload_and_out_dir(self, client, monkeypatch, tmp_path):
        """失败终态：上传原件删除 + out_dir 半成品清理，不白占磁盘。"""
        def _boom(video, jid, lang, progress):
            (config.OUT_DIR / jid).mkdir(parents=True, exist_ok=True)
            (config.OUT_DIR / jid / "partial.tmp").write_text("x")
            raise RuntimeError("stub failure")
        monkeypatch.setattr("backend.pipeline.run", _boom)
        before = set(config.UP_DIR.glob("*"))
        r = client.post("/api/analyze",
                        files={"file": ("a.mp4", io.BytesIO(_fake_video_bytes()),
                                        "video/mp4")})
        job_id = r.json()["job_id"]
        d = None
        for _ in range(50):
            d = client.get("/api/jobs/" + job_id).json()["job"]
            if d["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert d["status"] == "error"
        assert set(config.UP_DIR.glob("*")) == before  # 上传原件已删
        assert not (config.OUT_DIR / job_id).exists()  # 半成品已清
