# -*- coding: utf-8 -*-
"""LLM 分析逻辑：切块 / prompt 构造 / JSON 容错解析 / map-reduce。chat_fn 注入，零 token。"""
import pytest

from backend.llm import (build_analyze_prompt, build_reduce_prompt,
                         chunk_transcript, extract_json, analyze)


def _segs(texts):
    return [{"start": float(i * 10), "end": float(i * 10 + 8), "text": t}
            for i, t in enumerate(texts)]


def _ts(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


class TestChunkTranscript:
    def test_short_single_chunk(self):
        segs = _segs(["你好", "世界"])
        assert len(chunk_transcript(segs, limit=100)) == 1

    def test_splits_on_budget_without_splitting_segment(self):
        segs = _segs(["甲" * 30, "乙" * 30, "丙" * 30])
        chunks = chunk_transcript(segs, limit=50)
        assert [len(c) for c in chunks] == [1, 1, 1]
        assert chunks[0][0]["text"].startswith("甲")

    def test_keeps_time_order(self):
        segs = _segs([f"段{i}" for i in range(10)])
        chunks = chunk_transcript(segs, limit=30)
        starts = [s["start"] for c in chunks for s in c]
        assert starts == sorted(starts)

    def test_hard_cap_max_chunks(self):
        segs = _segs(["长" * 100 for _ in range(50)])
        assert len(chunk_transcript(segs, limit=100)) <= 12

    def test_cap_none_returns_all(self):
        segs = _segs(["长" * 100 for _ in range(50)])
        assert len(chunk_transcript(segs, limit=100, cap=None)) > 12


class TestPrompts:
    def test_analyze_prompt_contains_transcript_and_ocr(self):
        segs = _segs(["今天聊聊杭州旅游"])
        frames = [{"ts": 5.0, "ts_label": "00:05", "file": "frames/a.jpg",
                   "ocr": "西湖 断桥 雷峰塔"}]
        p = build_analyze_prompt(segs, frames, _ts)
        assert "今天聊聊杭州旅游" in p
        assert "西湖 断桥 雷峰塔" in p
        assert "JSON" in p and "chapters" in p

    def test_analyze_prompt_handles_empty(self):
        p = build_analyze_prompt([], [], _ts)
        assert "无语音" in p and "无截帧" in p

    def test_reduce_prompt_embeds_partials(self):
        p = build_reduce_prompt([{"topic": "甲"}, {"topic": "乙"}])
        assert "甲" in p and "乙" in p


class TestExtractJson:
    def test_plain(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence(self):
        raw = '好的，分析如下：\n```json\n{"a": 1}\n```\n以上。'
        assert extract_json(raw) == {"a": 1}

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            extract_json("完全不是 JSON")


class TestAnalyze:
    def test_short_single_call(self):
        calls = []

        def fake_chat(prompt):
            calls.append(prompt)
            return '{"topic": "测试", "summary": "s"}'

        out = analyze(_segs(["短内容"]), [], _ts, chat_fn=fake_chat)
        assert out["topic"] == "测试"
        assert len(calls) == 1  # 短转写只调一次，省 token

    def test_long_map_reduce(self):
        calls = []
        big = _segs(["甲" * 200 for _ in range(40)])  # 超 6000 字预算

        def fake_chat(prompt):
            calls.append(prompt)
            if "分块要点" in prompt:
                return '{"topic": "合并", "summary": "total"}'
            return '{"topic": "块", "summary": "part"}'

        out = analyze(big, [], _ts, chat_fn=fake_chat)
        assert out["topic"] == "合并"
        assert len(calls) > 1  # map 多次 + reduce 一次
        assert any("分块要点" in c for c in calls)
        assert "transcript_truncated" not in out

    def test_over_cap_marks_truncated(self):
        # 超 MAX_CHUNKS 硬顶：尾部被丢弃必须在结果里显式标记，不能静默丢
        huge = _segs(["长" * 100 for _ in range(1000)])  # ~19 块 > 12 硬顶
        map_calls = []

        def fake_chat(prompt):
            if "分块要点" in prompt:
                return '{"topic": "合并", "summary": "total"}'
            map_calls.append(prompt)
            return '{"topic": "块", "summary": "part"}'

        out = analyze(huge, [], _ts, chat_fn=fake_chat)
        assert out["transcript_truncated"] is True
        assert len(map_calls) == 12  # map 只跑硬顶数量的块
