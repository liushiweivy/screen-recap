# -*- coding: utf-8 -*-
"""全局配置：全部走环境变量，零硬编码机器路径（别人克隆即用）。

LLM 兼容任意 OpenAI chat 协议端点：默认火山方舟 Coding Plan，
换厂商只需设 SR_LLM_URL / SR_LLM_MODEL / SR_LLM_KEY 三个变量。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SR_DATA_DIR", BASE_DIR / "data"))
UP_DIR = DATA_DIR / "up"      # 上传的原始视频
OUT_DIR = DATA_DIR / "out"    # 每个 job 的产出（音频/帧/结果）
STATIC_DIR = BASE_DIR / "static"

for d in (UP_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = int(os.environ.get("SR_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
JOB_TTL_SEC = int(os.environ.get("SR_JOB_TTL_H", "12")) * 3600

# ---- LLM（OpenAI chat 兼容）----
# key 只从环境变量或 data/llm.key 本地文件读，不入源码
LLM_URL = os.environ.get(
    "SR_LLM_URL", "https://ark.cn-beijing.volces.com/api/coding/v1/chat/completions")
LLM_MODEL = os.environ.get("SR_LLM_MODEL", "ark-code-latest")
LLM_MAX_TOKENS = int(os.environ.get("SR_LLM_MAX_TOKENS", "4096"))
LLM_TIMEOUT = int(os.environ.get("SR_LLM_TIMEOUT", "120"))


def load_llm_key() -> str:
    key = os.environ.get("SR_LLM_KEY", "").strip()
    if key:
        return key
    key_file = DATA_DIR / "llm.key"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    return ""  # 未配置时仍可跑本地部分（转写+截帧+OCR），仅无 AI 总结


# ---- whisper ----
# 默认 large-v3-turbo（质量接近 large-v3 约 8 倍速，~1.6GB 首次自动下载）；
# 弱机可 SR_STT_MODEL=small / base
STT_MODEL = os.environ.get("SR_STT_MODEL", "large-v3-turbo")

# ---- 截帧 ----
MAX_FRAMES = int(os.environ.get("SR_MAX_FRAMES", "12"))     # 喂给分析的帧上限
# ffmpeg 场景切换阈值。实测：纯色 PPT 硬切页 scene 分只有 ~0.1（0.25 会漏切页
# 丢整页内容）；宁低勿高——多产帧有去重+抽稀兜底，漏切的信息补不回来。
SCENE_THRESHOLD = float(os.environ.get("SR_SCENE_TH", "0.1"))
FRAME_WIDTH = int(os.environ.get("SR_FRAME_WIDTH", "960"))  # 帧缩放宽度（OCR/展示够用）
# 平均哈希去重阈值。实测：同版式不同文字的 PPT 页 hamming 只有 3-4，
# 阈值 5 会把真内容页当重复丢掉（丢整章信息）；真·近似重复帧（微动）距离 0-1。
DEDUP_HAMMING = int(os.environ.get("SR_DEDUP_HAMMING", "2"))

HOST = os.environ.get("SR_HOST", "127.0.0.1")  # 开局域网访问显式 SR_HOST=0.0.0.0
PORT = int(os.environ.get("SR_PORT", "8870"))
