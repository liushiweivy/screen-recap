# -*- coding: utf-8 -*-
"""截帧筛选纯逻辑：哈希去重 / 均匀抽稀 / 组合策略。不碰真实图片（hash_fn 注入）。"""
from pathlib import Path

import pytest

from backend.frames import dedupe_frames, hamming, pick_even, select_frames


def _frames(n: int):
    return [(float(i), Path(f"f{i:02d}.jpg")) for i in range(n)]


def _hash_by_name(mapping):
    return lambda path: mapping[path.name]


class TestHamming:
    def test_identical_is_zero(self):
        assert hamming(0b1010, 0b1010) == 0

    def test_counts_differing_bits(self):
        assert hamming(0b0000, 0b1111) == 4
        assert hamming(0, 1 << 63) == 1


class TestDedupe:
    def test_near_duplicates_dropped_first_kept(self):
        frames = _frames(4)
        # f1 与 f0 差 1 bit(去)；f2 差 6 bit(留)；f3 与 f2 差 1 bit(去)
        far = 0b111111 << 40
        hashes = {"f00.jpg": 0, "f01.jpg": 1, "f02.jpg": far, "f03.jpg": far | 1}
        out = dedupe_frames(frames, threshold=5, hash_fn=_hash_by_name(hashes))
        assert [p.name for _, p in out] == ["f00.jpg", "f02.jpg"]

    def test_all_distinct_kept(self):
        frames = _frames(3)
        # 三张两两 hamming=12 > 阈值
        hashes = {"f00.jpg": 0b111111, "f01.jpg": 0b111111 << 20,
                  "f02.jpg": 0b111111 << 40}
        out = dedupe_frames(frames, threshold=5, hash_fn=_hash_by_name(hashes))
        assert len(out) == 3

    def test_empty(self):
        assert dedupe_frames([], hash_fn=_hash_by_name({})) == []


class TestPickEven:
    def test_under_limit_unchanged(self):
        assert pick_even(_frames(3), 12) == _frames(3)

    def test_over_limit_keeps_ends_evenly(self):
        out = pick_even(_frames(25), 5)
        idx = [int(p.stem[1:]) for _, p in out]
        assert idx == [0, 6, 12, 18, 24]  # 首尾都在，等距

    def test_limit_one_keeps_first(self):
        out = pick_even(_frames(9), 1)
        assert [p.name for _, p in out] == ["f00.jpg"]

    def test_zero_limit_returns_all(self):
        assert len(pick_even(_frames(7), 0)) == 7


class TestSelectFrames:
    def test_dedupe_then_pick(self):
        frames = _frames(30)
        # 前 20 张全同 hash，后 10 张同另一个远 hash → 去重后剩 2 张，不再抽稀
        hashes = {f"f{i:02d}.jpg": (i // 20) * (0b111111 << 40) for i in range(30)}
        out = select_frames(frames, limit=12, dedup_threshold=5,
                            hash_fn=_hash_by_name(hashes))
        assert [p.name for _, p in out] == ["f00.jpg", "f20.jpg"]

    def test_still_over_limit_after_dedupe_picks_even(self):
        frames = _frames(20)
        # 每帧 3 个分散比特位：两两 hamming=6 > 阈值 5，保证互不近似
        hashes = {f"f{i:02d}.jpg": (1 << i) | (1 << (20 + i)) | (1 << (40 + i))
                  for i in range(20)}
        out = select_frames(frames, limit=6, dedup_threshold=5,
                            hash_fn=_hash_by_name(hashes))
        assert len(out) == 6
        assert out[0][1].name == "f00.jpg" and out[-1][1].name == "f19.jpg"
