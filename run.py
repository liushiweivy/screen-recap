# -*- coding: utf-8 -*-
"""启动入口：reload 锁 False（Windows 上 reload 换 worker 会卡死留孤儿进程）。"""
import uvicorn

from backend import config

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host=config.HOST, port=config.PORT,
                reload=False, log_level="info")
