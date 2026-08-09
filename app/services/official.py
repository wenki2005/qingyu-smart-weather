"""中国天气网官方天气（实况 + 今日预报），用于与中国气象局官方数据校准。

数据源：d1.weather.com.cn（中国天气网，免费、无需 Key、覆盖全国县市）。
需要城市代码（Location.wcn_code，由 cn_geocoder 从 weather.com.cn 搜索获得）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import httpx

from ..models import Location

logger = logging.getLogger(__name__)

SK_URL = "http://d1.weather.com.cn/sk_2d/{code}.html"          # 实况
DZ_URL = "http://d1.weather.com.cn/dingzhi/{code}.html"        # 今日/多日预报


def _extract_json(text: str) -> Optional[dict]:
    """从 `var xxx ={...}; ...` 形式的 JS 中提取**第一个** JSON 对象。"""
    try:
        start = text.find("{")
        if start < 0:
            return None
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
        return obj
    except Exception as e:
        logger.warning("weather.com.cn 响应解析失败: %s", e)
        return None


class OfficialWeather:
    """中国天气网官方天气客户端。"""

    def __init__(self, timeout: float = 8.0):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "http://www.weather.com.cn/"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except RuntimeError:
                pass
            self._client = None

    async def fetch(self, loc: Location) -> Optional[dict]:
        """拉取官方实况与今日预报；无城市代码或失败返回 None。"""
        code = getattr(loc, "wcn_code", None)
        if not code:
            return None
        try:
            client = await self._http()
            ts = str(int(time.time() * 1000))
            sk = await client.get(SK_URL.format(code=code), params={"_": ts})
            sk.raise_for_status()
            sk_data = _extract_json(sk.text)
            dz = await client.get(DZ_URL.format(code=code), params={"_": ts})
            dz.raise_for_status()
            dz_data = _extract_json(dz.text)
            return self._compose(loc, sk_data, dz_data)
        except Exception as e:
            logger.warning("获取官方天气失败 (%s): %s", getattr(loc, "name", ""), e)
            return None

    @staticmethod
    def _compose(loc: Location, sk: Optional[dict], dz: Optional[dict]) -> dict:
        """组装为前端可直接使用的结构。"""
        out: dict = {"city": loc.name, "current": None, "today": None}
        if sk:
            out["current"] = {
                "temp": sk.get("temp"),
                "wind_dir": sk.get("WD"),
                "wind_level": sk.get("WS"),
                "humidity": sk.get("SD"),
                "time": sk.get("time"),
            }
        if dz:
            wi = (dz or {}).get("weatherinfo") or {}
            out["today"] = {
                "temp": wi.get("temp"),          # 今日最高
                "tempn": wi.get("tempn"),        # 今日最低
                "weather": wi.get("weather"),
                "wind": wi.get("wd") or wi.get("wind"),
                "fctime": wi.get("fctime"),
            }
        return out
