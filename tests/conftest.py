"""pytest 共享配置。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 保证可从项目根导入 app 包
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(
        city="北京",
        latitude=39.9075,
        longitude=116.39723,
        timezone="Asia/Shanghai",
    )
