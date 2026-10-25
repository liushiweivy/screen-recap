# Screen Recap 录屏分析 设计规格（SPEC）

> 本文件是本项目唯一事实源。可直接编辑；改完对 Claude 说「按 SPEC 实现」即驱动代码对齐。
> 实现完成后对应任务会被勾选、变更记录会追加。规格与代码冲突时以本文件为准。

## 1. 目标与范围

**目标**：给一个屏幕录像视频，自动产出「AI 总结 + 关键帧画廊 + 完整语音转写」，回答「这段录屏大概在聊什么」。

**核心约束（省 token 设计）**：视频/图片一律不进大模型。重活全部本地完成（whisper 转写、ffmpeg 截帧、RapidOCR 画面文字识别），只把「转写文本 + 帧画面 OCR 文本」发给 LLM 做一次文本总结。一个 5 分钟录屏的 LLM 成本约几千 tokens；转写超长时自动 map-reduce。

**范围内**：

- Web 界面上传录屏视频（拖拽/点选），后台异步分析，前端轮询进度
- 本地 whisper 语音转写（GPU/CPU 自动选择，支持 auto/zh/en/ja/ko）
- ffmpeg 场景切换智能截帧 + 感知哈希去重 + 均匀抽稀（≤12 张）
- RapidOCR 本地识别每张帧的屏幕文字（代码/PPT/聊天记录等）
- LLM 一次文本调用出结构化总结（主题/概括/要点/章节/关键词）
- 零硬编码机器路径，全部走环境变量，克隆即用
- 无 LLM key 也能用：本地三件套（转写/截帧/OCR）照常返回，仅无 AI 总结

**范围外**：

- 实时录屏采集（只分析已有视频文件）
- 多用户/账号体系（本机单用户工具）
- 视频画面直接进多模态 LLM（违背省 token 设计，不做）
- 把分析结果接入任何带权限的自动化链路（提示注入为已接受残余风险，见第 6 节）

## 2. 需求

### 用户故事与验收标准

- [x] US-1 上传视频开始分析
  - AC-1.1 支持 mp4/mkv/mov/avi/webm/flv/ts/m4v 扩展名白名单，其他返回 400
  - AC-1.2 支持语言参数 auto/zh/en/ja/ko 白名单，其他返回 400
  - AC-1.3 超过上传上限（默认 2048MB，可调）返回 413 且不残留半成品文件
  - AC-1.4 空文件返回 400
  - AC-1.5 成功返回 `{"success": true, "job_id": "<12位hex>"}`
- [x] US-2 跟踪分析进度
  - AC-2.1 轮询 `/api/jobs/{job_id}` 返回 status（queued/running/done/error）、当前步骤名、百分比进度
  - AC-2.2 job_id 非法格式（非 12 位小写 hex）返回 400；不存在返回 404
  - AC-2.3 失败任务返回可读 error，且磁盘半成品（上传原件 + 产出目录）被清理
- [x] US-3 查看分析结果
  - AC-3.1 完成后 result 含：AI 总结（topic/summary/key_points/chapters/keywords）、关键帧画廊（时间戳 + OCR 文本 + 可点开大图）、完整转写
  - AC-3.2 帧图通过 `/frames/{job_id}/{name}` 提供，双重白名单（job_id 格式 + `scene|uni_XXXX.jpg` 文件名）防目录穿越
  - AC-3.3 前端所有动态文本（转写/OCR/总结）经 esc() 转义渲染，防存储型 XSS
- [x] US-4 无声/无 key 等降级场景不致命
  - AC-4.1 无音轨视频跳过转写，仅画面分析，任务不失败
  - AC-4.2 转写异常降级为仅画面分析
  - AC-4.3 未配置 LLM key 时本地三件套照常返回，analysis=None 且 analysis_error 说明原因
- [x] US-5 可移植性
  - AC-5.1 机器无 ffmpeg 时自动回落 imageio-ffmpeg 内置二进制（探测顺序：SR_FFMPEG → PATH → imageio-ffmpeg）
  - AC-5.2 whisper 模型首次运行自动下载（走 hf-mirror 镜像）
  - AC-5.3 有 CUDA 用 GPU（float16），失败自动回落 CPU int8
- [x] US-6 一键启动
  - AC-6.1 `start.bat` 首跑自动建 venv 装依赖并起服务 :8870
  - AC-6.2 launch.ini 齐全，可被桌面项目启动台自动发现
- [ ] US-7 结果导出（复制/下载 Markdown 报告）
- [ ] US-8 历史任务列表页（当前任务状态仅存内存 + 磁盘 result.json，无索引页）

## 3. 架构

```
┌─────────────────────────── 浏览器 :8870 ───────────────────────────┐
│ static/index.html（单文件前端，拖拽上传 + 轮询进度 + 结果画廊）       │
└──────────────┬─────────────────────────────────────────────────────┘
               │ POST /api/analyze (multipart)   GET /api/jobs/{id}
               ▼
┌────────────────────── FastAPI (backend/app.py) ────────────────────┐
│ 上传校验（扩展名/大小/语言白名单）  帧图白名单  静态托管               │
└──────────────┬─────────────────────────────────────────────────────┘
               │ jobs.create_job() 入队
               ▼
┌────────────────── jobs.py：单 worker 后台线程 ─────────────────────┐
│ 内存字典 + Condition 队列 + TTL GC；串行跑防止并发把机器打满           │
└──────────────┬─────────────────────────────────────────────────────┘
               │ pipeline.run(video, job_id, language, progress)
               ▼
┌────────────────── pipeline.py：分析流水线 ─────────────────────────┐
│                                                                    │
│  视频 ──► ffmpegx 探测时长/抽 16k 单声道 WAV                          │
│             │                                                      │
│             ▼                                                      │
│          stt.py  faster-whisper 本地转写（懒加载单例，GPU→CPU 回落）   │
│             │  transcript {text, segments[], language}             │
│             ▼                                                      │
│  视频 ──► ffmpegx 场景切换截帧（scene>0.1 + 首帧必采，showinfo 回读    │
│             │      时间戳）；产帧 <3 张则均匀截帧兜底                  │
│             ▼                                                      │
│          frames.py  平均哈希去重(hamming≤2) + 均匀抽稀(≤12 张)        │
│             │                                                      │
│             ▼                                                      │
│          ocr.py  RapidOCR 本地识别每帧画面文字（ONNX，CPU 可用）       │
│             │  frame_items[{ts, file, ocr}]                        │
│             ▼                                                      │
│          llm.py  唯一 token 消耗点：只发文本                          │
│             短 → 一次调用；长 → 6000字/块 map-reduce（≤12 块硬顶）     │
│             │  严格 JSON 输出 + 容错解析                              │
│             ▼                                                      │
│  result.json 落盘 data/out/<job_id>/；音频/上传原件跑完即删            │
└────────────────────────────────────────────────────────────────────┘
```

### 模块职责表

| 模块 | 职责 | 关键说明 |
|------|------|----------|
| `backend/app.py` | FastAPI 入口 | 上传→建档→轮询→帧图→静态托管；全部输入白名单校验 |
| `backend/config.py` | 全局配置 | 全走环境变量（`SR_*` 前缀），零硬编码路径；key 只从 env 或 `data/llm.key` 读 |
| `backend/jobs.py` | 任务登记 | 单 worker 串行 + 内存字典 + TTL 清理（默认 12h，仅清 done/error） |
| `backend/pipeline.py` | 分析流水线编排 | 抽音频→STT→截帧→OCR→LLM；各步降级不致命；结果落盘 |
| `backend/ffmpegx.py` | ffmpeg 封装 | 可移植定位三级探测；stderr 守护线程抽干防 PIPE 塞死；时长从 `ffmpeg -i` stderr 解析 |
| `backend/frames.py` | 截帧筛选 | 8x8 平均哈希去重 + 均匀抽稀，纯函数为主便于单测 |
| `backend/stt.py` | 本地语音转写 | faster-whisper 懒加载单例；CUDA/cuBLAS/cuDNN 路径自动挂接；CPU int8 兜底 |
| `backend/ocr.py` | 画面文字识别 | RapidOCR（ONNX）懒加载单例；超大图先压边长 4096；解码失败返回空不炸流水线 |
| `backend/llm.py` | AI 分析 | OpenAI chat 兼容端点可换厂商；map-reduce 控制调用次数；`extract_json` 容错 |
| `static/index.html` | 单文件前端 | 深色鎏金风；拖拽上传、进度条、帧画廊、转写折叠；动态值全过 esc() |
| `run.py` / `start.bat` / `run-server.bat` | 启动链 | uvicorn reload=False（Windows reload 换 worker 会卡死留孤儿）；bat 纯 ASCII |

## 4. 接口契约

### POST /api/analyze

上传视频并创建分析任务。

- 入参（multipart/form-data）：
  - `file`：视频文件，扩展名白名单 `.mp4 .mkv .mov .avi .webm .flv .ts .m4v`
  - `language`：表单字段，`auto`（默认）/ `zh` / `en` / `ja` / `ko`
- 出参 200：`{"success": true, "job_id": "4abfb0782e66"}`（job_id 为 12 位小写 hex）
- 错误：
  - 400 不支持的视频格式 / 不支持的语言 / 空文件
  - 413 超过上传上限（默认 2048MB，`SR_MAX_UPLOAD_MB` 可调）

### GET /api/jobs/{job_id}

轮询任务状态。

- 路径参数：`job_id` 必须匹配 `^[0-9a-f]{12}$`，否则 400「非法 job id」
- 出参 200：

```json
{
  "success": true,
  "job": {
    "id": "4abfb0782e66",
    "status": "queued | running | done | error",
    "step": "语音转文字",
    "progress": 42,
    "error": null,
    "result": null
  }
}
```

- `result` 完成后的结构：

```json
{
  "job_id": "…", "video_name": "…", "duration": 301.5, "duration_label": "05:01",
  "stt_device": "cuda/float16 · large-v3-turbo", "language": "zh",
  "transcript": "完整转写文本",
  "segments": [{"start": 0.0, "end": 3.2, "text": "…"}],
  "frames": [{"ts": 12.5, "ts_label": "00:12", "file": "frames/scene_0002.jpg", "ocr": "画面文字"}],
  "analysis": {"topic": "…", "summary": "…", "key_points": ["…"],
               "chapters": [{"time": "00:12", "title": "…"}], "keywords": ["…"]},
  "analysis_error": ""
}
```

  - 超长转写被硬顶截断时 analysis 内附加 `"transcript_truncated": true`
  - 无 key / LLM 失败时 `analysis` 为 null、`analysis_error` 为原因，本地成果仍在
- 错误：404 任务不存在或已过期（TTL 默认 12h，`SR_JOB_TTL_H` 可调）

### GET /frames/{job_id}/{name}

任务帧图。job_id 与 name 双重白名单（name 必须匹配 `^(scene|uni)_\d{4}\.jpg$`），否则 400；文件不存在 404。

### GET /api/health

`{"success": true, "llm_configured": bool, "max_upload_mb": int}`，供前端提示是否已配 key。

### GET /

静态托管 `static/`（html=True），单文件前端。

## 5. 数据模型与存储

**无数据库**。状态分两层：

- **内存任务表**（`jobs._jobs` dict，进程级）：
  `{id, status, step, progress, video, language, result, error, created}`
  - status 流转：`queued → running → done | error`
  - TTL GC：仅清理 done/error 且超龄任务，运行中/排队中不清理（超长 CPU 转写不能被中途删状态）
- **磁盘产出**（`data/` 目录，gitignored）：

```
data/
├── up/                        # 上传原件（终态即删，最大头磁盘占用）
├── out/<job_id>/
│   ├── audio.wav              # 中间产物，跑完即删
│   ├── frames/scene_*.jpg | uni_*.jpg   # 保留供前端画廊展示
│   └── result.json            # 最终结果（结构与第 4 节 result 一致）
└── llm.key                    # LLM 密钥本地文件（gitignored，勿提交）
```

- GC 删除任务时同步 `shutil.rmtree(out/<job_id>)`；任务失败时半成品产出目录一并清理
- 数据根目录可用 `SR_DATA_DIR` 整体换位置；测试通过 conftest monkeypatch 隔离到 tmp

## 6. 关键设计决策

1. **只发文本给 LLM（省 token 核心）**：视频/图片不进大模型。转写 + 帧 OCR 文本已足够回答「在聊什么」，5 分钟录屏成本压到几千 tokens。超长转写按 6000 字切块 map-reduce，块数硬顶 12（约 7 万字），总调用 ≤ 1 + 块数；被截断会在结果里标 `transcript_truncated`。
2. **av 必须锁 18.1.0**：av 19 删了 `metadata_errors` 参数，faster-whisper 1.2.x 直接 TypeError。requirements.txt 已锁死，勿升级。
3. **场景阈值 0.1 勿调高**：实测纯色 PPT 硬切页 scene 分只有约 0.1（0.25 会漏切页丢整页内容）。宁低勿高——多产帧有去重 + 抽稀兜底，漏切的信息补不回来。
4. **去重阈值 2 勿调高**：实测同版式不同文字的 PPT 页平均哈希 hamming 只有 3-4，阈值 5 会把真内容页当重复丢掉（丢整章信息）；真·近似重复帧（微动）距离 0-1。
5. **场景检测必须加 `eq(n,0)`**：首帧 scene 分恒为 0，不加永远丢开头画面。
6. **mjpeg `-strict unofficial`**：ffmpeg 7.x 对非全范围 YUV 编码 jpg 默认报错 -22，截帧两条路径（场景/均匀）都必须带。
7. **HF_HUB_DISABLE_XET=1**：hf-mirror 不代理 Xet CAS（401），stt.py 在 import 前 setdefault 强制走普通 HTTP 分片下载；同时 setdefault HF_ENDPOINT=hf-mirror.com。
8. **ffmpeg 三级探测**：`SR_FFMPEG` → PATH → imageio-ffmpeg 内置二进制，别人机器零 ffmpeg 安装也能跑；时长解析用 `ffmpeg -i` 的 stderr（ffprobe 不一定存在）。
9. **ffmpeg stderr 守护线程抽干**：ffmpeg 输出量大时 PIPE 会塞死子进程，`run()` 起守护线程逐行抽。
10. **单 worker 串行**：录屏分析是重活（whisper 分钟级），串行防并发把机器打满；多余请求自然排队，前端轮询即可。worker 意外死亡时复位标记，下个任务能重启 worker。
11. **Windows 句柄坑**：上传超限必须先 break 让 `with` 关掉句柄再删文件——Windows 下 open() 不带 FILE_SHARE_DELETE，持句柄 unlink 必 PermissionError。
12. **uvicorn reload=False**：Windows 上 reload 换 worker 会卡死留孤儿进程。
13. **LLM 端点可换厂商**：任意 OpenAI chat 兼容端点，换厂商只需 `SR_LLM_URL` / `SR_LLM_MODEL` / `SR_LLM_KEY` 三个变量。key 只从环境变量或 `data/llm.key`（gitignored）读，不入源码；具体取值见本机配置与 README 环境变量表。
14. **降级哲学**：无音轨→跳过转写；转写异常→仅画面分析；无 key/LLM 失败→本地三件套照常返回（analysis=None，analysis_error 记原因）。任何单点失败不吞掉已有成果。
15. **提示注入为已接受残余风险**：转写文本会被拼进 LLM prompt，恶意录屏理论上可尝试指挥模型。缓解：输出约束为固定 JSON 结构、仅本地展示、无任何工具调用能力。不要把本服务包成带权限的自动化链路的一环。
16. **前端 XSS 防护**：转写/OCR/总结文本均来自视频内容（不可信），前端所有动态值过 esc() 转义；帧图接口双重白名单防目录穿越。

## 7. 任务清单

- [x] FastAPI 上传/轮询/帧图/健康检查接口（含全部白名单校验）
- [x] 单 worker 后台任务队列 + TTL GC + 失败清理
- [x] ffmpeg 封装（三级探测/stderr 抽干/场景截帧/均匀截帧/时长探测）
- [x] 截帧筛选（平均哈希去重 + 均匀抽稀）
- [x] faster-whisper 本地转写（GPU/CPU 自动、懒加载、hf-mirror 下载）
- [x] RapidOCR 帧画面文字识别
- [x] LLM 分析（短直调/长 map-reduce/JSON 容错/无 key 降级）
- [x] 单文件前端（上传/进度/总结/帧画廊/转写/XSS 转义）
- [x] 一键启动链（start.bat 建 venv + run-server.bat，纯 ASCII bat）
- [x] 38 项全离线测试（帧筛选纯逻辑 / LLM prompt 与 map-reduce / API 打桩流水线），零 token 消耗
- [x] README（快速开始/环境变量表/踩坑备忘/安全说明）
- [x] 推送 GitHub 公开仓 liushiweivy/screen-recap（密钥与产出目录已 gitignore）
- [ ] 结果导出为 Markdown（US-7）
- [ ] 历史任务列表页（US-8）
- [ ] 任务取消接口（当前只能等跑完或关服务）

## 8. 变更记录

| 日期 | 变更 | 状态 |
|------|------|------|
| 2026-10-24 | 创建 SPEC.md（SDD 唯一事实源），追加项目 CLAUDE.md 的 SPEC 驱动开发约定 | 完成 |
