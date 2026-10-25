# Screen Recap 项目指引

## SPEC 驱动开发

- `SPEC.md` 是本项目唯一事实源：用户改 SPEC 后说「按 SPEC 实现」，先 diff 规格与代码差异，列实现计划再动手
- 实现完成后：勾选 SPEC.md 第 7 节对应任务、在第 8 节追加变更记录（日期|变更|状态）
- 规格与代码冲突时以 SPEC.md 为准，除非变更记录注明有意偏离

## 关键红线

- `requirements.txt` 中 av 必须锁 18.1.0（av 19 删了 metadata_errors，faster-whisper 直接 TypeError）
- `SR_SCENE_TH=0.1`、`SR_DEDUP_HAMMING=2` 是实测参数，勿乱调（依据见 SPEC.md 第 6 节）
- `data/llm.key` 为本地密钥文件，gitignored，绝不入 git
- 测试：`.venv\Scripts\python.exe -m pytest tests/ -q`，全离线零 token
