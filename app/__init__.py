"""Smart Weather Push - 智能天气推送服务。"""
from __future__ import annotations

import asyncio
import sys

__version__ = "1.0.0"

# Windows 下统一使用 SelectorEventLoop：
# 默认 ProactorEventLoop 在事件循环关闭后，残留连接对象被 GC 时
# 会触发 "Event loop is closed" 的析构报错（Python 3.8-3.12 已知问题），
# 该问题在 GUI 线程/CLI 快速退出场景下尤为常见。
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except (AttributeError, ValueError):
        pass
