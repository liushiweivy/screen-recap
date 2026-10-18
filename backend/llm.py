# -*- coding: utf-8 -*-
"""AI 分析：唯一的 token 消耗点，且只发文本（转写 + 帧画面 OCR），不发图不发视频。

策略：
- 转写不长 → 一次调用出总结；
- 转写超长 → 按时间切块 map 出每块要点，再 reduce 一次合并（总调用 ≤ 1 + ceil(n/块) 次）；
- 输出严格 JSON，extract_json 容错解析（模型偶尔套 markdown 围栏）。
- 端点任意 OpenAI chat 兼容：SR_LLM_URL / SR_LLM_MODEL / SR_LLM_KEY 可换厂商。
"""
from __future__ import annotations

import json
import re

import requests

from . import config

CHUNK_CHARS = 6000   # 单次分析带的转写上限，超出走 map-reduce
MAX_CHUNKS = 12      # map 块数硬顶（约 7 万字转写），防滥用


def chat(prompt: str) -> str:
    key = config.load_llm_key()
    if not key:
        raise RuntimeError(
            "未配置 LLM key：设 SR_LLM_KEY 环境变量或写 data/llm.key；"
            "没有 key 也能用本地部分（转写/截帧/OCR），仅无 AI 总结")
    resp = requests.post(
        config.LLM_URL,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json={"model": config.LLM_MODEL,
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": config.LLM_MAX_TOKENS},
        timeout=config.LLM_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"LLM 接口 {resp.status_code}：{resp.text[:200]}")
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def chunk_transcript(segments: list[dict], limit: int = CHUNK_CHARS,
                     cap: int | None = MAX_CHUNKS) -> list[list[dict]]:
    """按字符预算切块（不切段：单段超预算独占一块），保持时间顺序。

    cap 为块数上限（防滥用）；传 None 取全量，调用方自行截断并感知截断发生。"""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    for seg in segments:
        n = len(seg["text"]) + 12  # 含时间戳开销
        if cur and cur_len + n > limit:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(seg)
        cur_len += n
    if cur:
        chunks.append(cur)
    return chunks[:cap] if cap else chunks


def _fmt_segments(segments: list[dict], fmt_ts) -> str:
    return "\n".join(f"[{fmt_ts(s['start'])}] {s['text']}" for s in segments)


def _fmt_frames(frames: list[dict], fmt_ts) -> str:
    lines = []
    for f in frames:
        ocr = (f.get("ocr") or "").strip()
        if ocr:
            lines.append(f"[{fmt_ts(f['ts'])}] 画面文字：{ocr[:300]}")
        else:
            lines.append(f"[{fmt_ts(f['ts'])}] 画面文字：（无）")
    return "\n".join(lines)


_ANALYZE_SCHEMA = """{
  "topic": "一句话主题（15 字内）",
  "summary": "3-5 句整体内容概括",
  "key_points": ["要点1", "要点2", "..."],
  "chapters": [{"time": "mm:ss", "title": "该时间段聊的内容"}],
  "keywords": ["关键词1", "关键词2"]
}"""


def build_analyze_prompt(segments: list[dict], frames: list[dict], fmt_ts,
                         chunk_note: str = "") -> str:
    """单次分析 prompt。segments 为（已切好的）一块，frames 全量带。"""
    transcript = _fmt_segments(segments, fmt_ts) or "（无语音或未能识别）"
    frame_text = _fmt_frames(frames, fmt_ts) or "（无截帧）"
    return (
        "你是一个录屏视频分析助手。下面是一段屏幕录像的两类信息：\n"
        "1) 语音转写（带时间戳）——视频里人说的话；\n"
        "2) 关键帧画面文字（带时间戳）——从视频关键帧 OCR 出的屏幕文字（代码/PPT/聊天等）。\n"
        "请综合两者分析这段录屏大概在聊什么。" + chunk_note + "\n\n"
        "【语音转写】\n" + transcript + "\n\n"
        "【关键帧画面文字】\n" + frame_text + "\n\n"
        "严格只输出如下结构的 JSON（不要 markdown 围栏，不要任何其他文字）：\n"
        + _ANALYZE_SCHEMA +
        "\nchapters 按时间顺序 3-8 个，time 用转写/截帧里出现过的真实时间点。"
    )


def build_reduce_prompt(partials: list[dict]) -> str:
    """map-reduce 的合并 prompt：各块 JSON 要点合成最终 JSON。"""
    blob = json.dumps(partials, ensure_ascii=False, indent=1)
    return (
        "下面是同一段录屏按时间顺序分块分析出的多份 JSON 要点，"
        "请合并成一份完整、去重、按时间有序的最终分析。\n\n"
        "【分块要点】\n" + blob + "\n\n"
        "严格只输出如下结构的 JSON（不要 markdown 围栏，不要任何其他文字）：\n"
        + _ANALYZE_SCHEMA
    )


_JSON_RE = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> dict:
    """从模型输出里抠第一个完整 JSON 对象（容错围栏/前言）。"""
    m = _JSON_RE.search(text or "")
    if not m:
        raise ValueError(f"模型输出不含 JSON：{(text or '')[:120]}")
    return json.loads(m.group(0))


def analyze(segments: list[dict], frames: list[dict], fmt_ts,
            chat_fn=chat) -> dict:
    """分析入口：短则一次调用，长则 map-reduce。chat_fn 可注入便于单测（零 token）。"""
    all_chunks = chunk_transcript(segments, cap=None)
    truncated = len(all_chunks) > MAX_CHUNKS  # 超硬顶丢弃的尾部要让用户知道
    chunks = all_chunks[:MAX_CHUNKS]
    if len(chunks) <= 1:
        raw = chat_fn(build_analyze_prompt(segments, frames, fmt_ts))
        return extract_json(raw)
    partials = []
    for i, chunk in enumerate(chunks):
        note = f"\n注意：这是全长 {len(chunks)} 段中的第 {i + 1} 段，只总结本段即可。"
        raw = chat_fn(build_analyze_prompt(chunk, frames, fmt_ts, chunk_note=note))
        partials.append(extract_json(raw))
    result = extract_json(chat_fn(build_reduce_prompt(partials)))
    if truncated:
        result["transcript_truncated"] = True
    return result
