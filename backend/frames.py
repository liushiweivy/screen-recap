# -*- coding: utf-8 -*-
"""截帧筛选：感知哈希去重 + 均匀抽稀。纯函数为主，不碰 ffmpeg（便于单测）。

录屏的特点：大量时间画面静止（讲一页 PPT 几分钟），场景切换截帧后仍可能
有一堆近似重复的帧（同人讲话只有表情变），直接喂分析既浪费又稀释信息。
平均哈希去重把「看起来差不多」的帧只留第一张。
"""
from __future__ import annotations

from pathlib import Path

_HASH_SIZE = 8  # 8x8 平均哈希 = 64 bit


def average_hash(path: Path) -> int:
    """8x8 灰度均值哈希，返回 64 位整数。与 PIL 解耦困难，保留 IO 边界。"""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("L").resize((_HASH_SIZE, _HASH_SIZE))
        pixels = list(im.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, p in enumerate(pixels):
        if p >= avg:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dedupe_frames(frames: list[tuple[float, Path]],
                  threshold: int = 5,
                  hash_fn=average_hash) -> list[tuple[float, Path]]:
    """去掉与已保留帧哈希距离 ≤ threshold 的帧（保留先到先得，时间升序输入）。"""
    kept: list[tuple[float, Path]] = []
    hashes: list[int] = []
    for ts, path in frames:
        h = hash_fn(path)
        if any(hamming(h, kh) <= threshold for kh in hashes):
            continue
        kept.append((ts, path))
        hashes.append(h)
    return kept


def pick_even(frames: list[tuple[float, Path]],
              limit: int) -> list[tuple[float, Path]]:
    """超限后均匀抽稀：保留首尾，中间等距取。len <= limit 原样返回。"""
    if len(frames) <= limit or limit <= 0:
        return list(frames)
    if limit == 1:
        return [frames[0]]
    step = (len(frames) - 1) / (limit - 1)
    return [frames[round(i * step)] for i in range(limit)]


def select_frames(frames: list[tuple[float, Path]], limit: int,
                  dedup_threshold: int = 5,
                  hash_fn=average_hash) -> list[tuple[float, Path]]:
    """组合策略：先去重，仍超限再均匀抽稀。"""
    return pick_even(dedupe_frames(frames, dedup_threshold, hash_fn), limit)
