# -*- coding: utf-8 -*-
"""ffmpeg 封装：可移植定位（env → PATH → imageio-ffmpeg 内置二进制）+ 抽音频/探测/截帧。

别人机器上没有 F:\\java\\ffmpeg 也能跑：imageio-ffmpeg pip 包自带 ffmpeg 可执行文件。
ffprobe 不一定存在，时长解析用 `ffmpeg -i` 的 stderr，兼容所有发行版。
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path

from . import config

logger = logging.getLogger("screen-recap.ffmpegx")

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_PTS_RE = re.compile(r"pts_time:([\d.]+)")


def ffmpeg() -> str:
    exe = os_environ_ffmpeg() or shutil.which("ffmpeg") or _imageio_ffmpeg()
    if not exe:
        raise RuntimeError(
            "找不到 ffmpeg：请安装 ffmpeg 并加入 PATH，或 `pip install imageio-ffmpeg`，"
            "或用 SR_FFMPEG 环境变量指定可执行文件路径")
    return exe


def os_environ_ffmpeg() -> str:
    import os
    return os.environ.get("SR_FFMPEG", "").strip()


def _imageio_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 包未装或平台无预编译二进制
        return ""


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    """跑 ffmpeg 家族命令。stderr 必须起守护线程抽干——ffmpeg 输出量大时 PIPE 会塞死。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    err_buf: list[str] = []

    def _drain():
        for line in proc.stderr:  # type: ignore[union-attr]
            try:
                err_buf.append(line.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f"ffmpeg 超时（{timeout}s）：{Path(cmd[1]).name if len(cmd) > 1 else cmd[0]}")
    t.join(timeout=5)
    stderr = "".join(err_buf)
    return subprocess.CompletedProcess(cmd, proc.returncode, b"", stderr)


def run_checked(cmd: list[str], timeout: int = 1800) -> str:
    cp = run(cmd, timeout)
    if cp.returncode != 0:
        tail = (cp.stderr or "")[-400:]
        raise RuntimeError(f"ffmpeg 失败（码 {cp.returncode}）：{tail}")
    return cp.stderr or ""


def probe_duration(path: Path) -> float:
    """时长（秒）。ffmpeg -i 无输出文件时返回码非零，时长在 stderr 里。"""
    cp = run([ffmpeg(), "-hide_banner", "-i", str(path)], timeout=60)
    m = _DURATION_RE.search(cp.stderr or "")
    if not m:
        raise RuntimeError(f"无法解析视频时长：{path.name}")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return round(h * 3600 + mnt * 60 + s, 2)


def extract_audio(src: Path, dst: Path) -> None:
    """抽单声道 16k WAV（whisper 最省内存的输入形态）。"""
    run_checked([ffmpeg(), "-y", "-i", str(src), "-vn",
                 "-ac", "1", "-ar", "16000", str(dst)])


def extract_scene_frames(src: Path, out_dir: Path) -> list[tuple[float, Path]]:
    """场景切换截帧：ffmpeg select 滤镜 + showinfo 回读每帧时间戳。

    返回 [(秒, 帧路径)]，按时间升序。静态录屏（如纯讲 PPT）可能产出很少，
    调用方需用 extract_uniform_frames 兜底。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "scene_%04d.jpg")
    # eq(n,0)：首帧必采（scene 分数对第一帧恒为 0，否则开头画面永远丢）
    vf = (f"select='eq(n\\,0)+gt(scene,{config.SCENE_THRESHOLD})',"
          f"scale={config.FRAME_WIDTH}:-2,showinfo")
    # -strict unofficial：新版 ffmpeg 对非全范围 YUV 编码 mjpeg 默认报错（-22）
    stderr = run_checked([ffmpeg(), "-y", "-i", str(src), "-vf", vf,
                          "-vsync", "vfr", "-strict", "unofficial",
                          "-q:v", "3", pattern],
                         timeout=3600)
    # showinfo 的行与写出文件顺序一致（vfr 模式一一对应）
    times = [float(x) for x in _PTS_RE.findall(stderr)]
    files = sorted(out_dir.glob("scene_*.jpg"))
    if len(times) != len(files):
        # zip 会静默截断导致时间戳错位，必须显式暴露（理论上罕见：解析正则与写文件不对应）
        logger.warning("场景帧时间戳(%d)与文件(%d)数量不一致，按较短者配对", len(times), len(files))
    pairs = list(zip(times, files))
    return [(round(ts, 2), p) for ts, p in pairs]


def extract_uniform_frames(src: Path, out_dir: Path, count: int,
                           duration: float) -> list[tuple[float, Path]]:
    """均匀截帧兜底：场景检测产帧太少时用。一次命令按 fps 抽。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = max(1, count)
    fps = count / max(duration, 1.0)
    pattern = str(out_dir / "uni_%04d.jpg")
    run_checked([ffmpeg(), "-y", "-i", str(src),
                 "-vf", f"fps={fps:.6f},scale={config.FRAME_WIDTH}:-2",
                 "-strict", "unofficial", "-q:v", "3", pattern], timeout=3600)
    files = sorted(out_dir.glob("uni_*.jpg"))[:count]
    step = duration / (count + 1)
    return [(round(step * (i + 1), 2), p) for i, p in enumerate(files)]
