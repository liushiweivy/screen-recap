# -*- coding: utf-8 -*-
"""测试全局：data 目录隔离到 tmp，避免测试读写真实产出。"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UP_DIR", tmp_path / "up")
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    config.UP_DIR.mkdir(parents=True, exist_ok=True)
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
