# -*- coding: utf-8 -*-
"""分析流水线：抽音频 → 本地 STT → 智能截帧 → 本地 OCR → 一次文本 LLM 调用。

token 消耗设计：视频/图片一律不进 LLM，只有转写文本 + 帧画面 OCR 文本进 LLM，
且转写超长时 map-reduce（调用次数 ≤ 1 + ceil(转写/6000 字)）。
LLM 未配置 key 时不算失败：本地三件套（转写/截帧/OCR）照常返回，analysis=None。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config, ffmpegx, frames as frame_sel, llm, ocr, stt

logger = logging.getLogger("screen-recap.pipeline")


def run(video_path: Path, job_id: str, language: str = "auto", progress=None) -> dict:
    """完整跑一遍，返回结果 dict（可直接 JSON 序列化）。progress(step, pct) 回报进度。"""
    def report(step: str, pct: int) -> None:
        if progress:
            progress(step, pct)
        logger.info("[job %s] %s (%d%%)", job_id, step, pct)

    out_dir = config.OUT_DIR / job_id
    frame_dir = out_dir / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 探测 + 抽音频
    report("探测视频信息", 2)
    duration = ffmpegx.probe_duration(video_path)

    report("提取音频", 5)
    wav = out_dir / "audio.wav"
    has_audio = True
    try:
        ffmpegx.extract_audio(video_path, wav)
    except RuntimeError as exc:  # 无音轨的视频不致命：跳过转写
        logger.warning("[job %s] 抽音频失败（按无声处理）: %s", job_id, exc)
        has_audio = False

    # 2) 本地 STT
    transcript = {"text": "", "segments": [], "language": "", "duration": duration}
    if has_audio:
        report(f"语音转文字（{stt.device_info()}）", 10)

        def _stt_progress(done_sec: float) -> None:
            pct = 10 + int(45 * min(done_sec / max(duration, 1.0), 1.0))
            report("语音转文字", pct)

        try:
            transcript = stt.transcribe(wav, language=language, progress_cb=_stt_progress)
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001 - 转写失败降级为仅画面分析
            logger.exception("[job %s] STT 失败，降级仅画面", job_id)
            transcript["text"] = ""

    # 3) 智能截帧：场景切换优先，太少则均匀兜底
    report("截取关键帧", 60)
    shots = ffmpegx.extract_scene_frames(video_path, frame_dir)
    if len(shots) < 3:
        logger.info("[job %s] 场景帧仅 %d 张，改均匀截帧", job_id, len(shots))
        for _, p in shots:
            p.unlink(missing_ok=True)
        shots = ffmpegx.extract_uniform_frames(
            video_path, frame_dir, config.MAX_FRAMES, duration)
    shots = frame_sel.select_frames(shots, config.MAX_FRAMES, config.DEDUP_HAMMING)
    logger.info("[job %s] 选定 %d 帧", job_id, len(shots))

    # 4) 本地 OCR（每帧画面文字）
    report("识别画面文字", 70)
    frame_items = []
    for i, (ts, path) in enumerate(shots):
        text = ocr.recognize(path)["text"]
        frame_items.append({
            "ts": ts, "ts_label": stt.fmt_ts(ts),
            "file": f"frames/{path.name}", "ocr": text,
        })
        report("识别画面文字", 70 + int(15 * (i + 1) / max(len(shots), 1)))

    # 5) AI 总结（唯一 token 消耗点；无 key 降级跳过）
    report("AI 分析", 88)
    analysis = None
    analysis_error = ""
    try:
        analysis = llm.analyze(transcript["segments"], frame_items, stt.fmt_ts)
    except RuntimeError as exc:  # 无 key / 接口错误不吞掉本地成果
        analysis_error = str(exc)
        logger.warning("[job %s] AI 分析跳过：%s", job_id, exc)
    except Exception as exc:  # noqa: BLE001
        analysis_error = f"{type(exc).__name__}: {exc}"
        logger.exception("[job %s] AI 分析失败", job_id)

    report("整理结果", 98)
    result = {
        "job_id": job_id,
        "video_name": video_path.name,
        "duration": duration,
        "duration_label": stt.fmt_ts(duration),
        "stt_device": stt.device_info(),
        "language": transcript.get("language", ""),
        "transcript": transcript["text"],
        "segments": transcript["segments"],
        "frames": frame_items,
        "analysis": analysis,
        "analysis_error": analysis_error,
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    # 音频是中间产物，清掉省盘（帧和 result.json 留下展示）
    wav.unlink(missing_ok=True)
    return result
