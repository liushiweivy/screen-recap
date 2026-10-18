# 录屏分析 · Screen Recap

通用屏幕录像分析 agent：给一个录屏视频，自动提取里面的对话、截取关键画面、
分析这个视频大概在聊什么。

**省 token 设计**：视频/图片一律不进大模型。全程本地完成重活——

```
视频 → ffmpeg 抽音频 → 本地 whisper 转写对话（GPU/CPU 自动）
     → 场景切换智能截帧（首帧必采 + 感知哈希去重 + 均匀抽稀，≤12 张）
     → 本地 OCR 读出每张帧上的屏幕文字（代码/PPT/聊天记录…）
     → 只把「转写文本 + 帧画面文字」发给 LLM 出一次总结
```

一个 5 分钟录屏的 LLM 成本 ≈ 几千 tokens（一次文本调用）；
转写超长时自动 map-reduce（按 6000 字切块，块间合并）。

## 快速开始

1. 双击 `start.bat`（首次运行自动建 venv 装依赖，较大请耐心；
   whisper 模型首次分析时自动下载 ~1.6GB，走 hf-mirror 镜像）
2. 打开 http://localhost:8870 ，拖入录屏视频
3. 等进度条跑完：AI 总结（主题/要点/章节/关键词）+ 关键帧画廊 + 完整转写

## 配置 AI 总结（可选）

不配 key 也能用：本地三件套（转写/截帧/OCR）照常出，仅无 AI 总结。

配 key 二选一：

- 环境变量 `SR_LLM_KEY`
- 项目下 `data/llm.key` 文件写入 key（**勿提交 git**）

默认走火山方舟 Coding Plan（`ark-code-latest`）。换任意 OpenAI chat 兼容端点：

```bat
set SR_LLM_URL=https://your-provider/v1/chat/completions
set SR_LLM_MODEL=your-model
set SR_LLM_KEY=your-key
```

## 全部环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `SR_LLM_URL` / `SR_LLM_MODEL` / `SR_LLM_KEY` | 方舟 coding | LLM 端点/模型/密钥 |
| `SR_STT_MODEL` | `large-v3-turbo` | whisper 模型；弱机改 `small` 或 `base` |
| `SR_FFMPEG` | 自动探测 | ffmpeg 路径；不装则自动用 imageio-ffmpeg 内置二进制 |
| `SR_MAX_FRAMES` | `12` | 喂分析的帧数上限 |
| `SR_SCENE_TH` | `0.1` | 场景切换检测阈值（实测纯色 PPT 切页仅 ~0.1，勿调高） |
| `SR_DEDUP_HAMMING` | `2` | 帧去重阈值（同版式不同文字 PPT 页距离 3-4，勿调到 5） |
| `SR_MAX_UPLOAD_MB` | `2048` | 上传大小上限 |
| `SR_HOST` / `SR_PORT` | `127.0.0.1` / `8870` | 监听地址；局域网共享设 `0.0.0.0` |

## 技术要点（移植/踩坑备忘）

- **ffmpeg 可移植性**：探测顺序 `SR_FFMPEG` → PATH → imageio-ffmpeg 内置，
  别人机器零 ffmpeg 安装也能跑；时长解析用 `ffmpeg -i` 的 stderr（ffprobe 不一定有）
- **av 必须锁 18.1.0**：av 19 删了 `metadata_errors` 参数，faster-whisper 1.2.x 直接 TypeError
- **mjpeg `-strict unofficial`**：ffmpeg 7.x 对非全范围 YUV 编码 jpg 默认报错 -22
- **场景检测要加 `eq(n,0)`**：首帧 scene 分恒 0，不加永远丢开头画面
- whisper GPU：pip 版 nvidia-cublas/cudnn 需挂 PATH/LD_LIBRARY_PATH（代码自动处理
  Windows/Linux 两种布局），失败回落 CPU int8
- 帧图/结果按 job 存 `data/out/<job_id>/`，上传原件与中间音频跑完即删，
  任务失败时半成品目录一并清理

## 安全说明

- 上传校验：扩展名白名单 + 大小上限 + 语言参数白名单；帧图接口双重白名单防目录穿越
- 前端所有动态值过 `esc()` 转义，防存储型 XSS（转写/OCR 文本来自视频内容，不可信）
- **提示注入为已接受的残余风险**：转写文本会被拼进 LLM prompt，恶意录屏理论上可以
  尝试指挥模型。缓解措施：输出被约束为固定 JSON 结构、仅本地展示、无任何工具调用能力。
  不要把它包成带权限的自动化链路的一环。

## 测试

```bat
.venv\Scripts\python.exe -m pytest tests/ -q
```

38 项全离线（帧筛选纯逻辑 / LLM prompt 与 map-reduce / API 打桩流水线），零 token 消耗。
