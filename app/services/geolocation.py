"""IP 地理定位服务（多数据源自动降级）。

定位来源按优先级尝试：
1. ipinfo.io         —— 免费、无需 Key、支持全球
2. geolocation-db.com —— 免费备份源

定位结果仅用于确定「当前所在城市」，不采集或保存个人精确位置。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from ..models import Location

logger = logging.getLogger(__name__)


class GeoLocator:
    def __init__(self, timeout: float = 8.0):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0 (SmartWeatherPush)"},
            )
        return self._client

    async def locate(self) -> Optional[Location]:
        """返回 IP 定位结果，全部数据源失败返回 None。"""
        for name, fn in (
            ("ipinfo", self._via_ipinfo),
            ("geolocation-db", self._via_geolocation_db),
        ):
            try:
                loc = await fn()
                if loc is not None:
                    logger.info("IP 定位成功（%s）: %s", name, loc.name)
                    return loc
            except Exception as e:
                logger.debug("IP 定位源 %s 失败: %s", name, e)
        logger.warning("所有 IP 定位源均失败")
        return None

    async def _via_ipinfo(self) -> Optional[Location]:
        client = await self._http()
        resp = await client.get("https://ipinfo.io/json")
        resp.raise_for_status()
        data = resp.json()
        loc = data.get("loc", "")
        lat_s, lon_s = loc.split(",")
        city = data.get("city") or data.get("region") or "未知"
        return Location(
            name=city,
            latitude=float(lat_s),
            longitude=float(lon_s),
            timezone=data.get("timezone") or "Asia/Shanghai",
            country=data.get("country"),
        )

    async def _via_geolocation_db(self) -> Optional[Location]:
        client = await self._http()
        resp = await client.get("https://geolocation-db.com/json/")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("latitude") or not data.get("longitude"):
            return None
        return Location(
            name=data.get("city") or "未知",
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            timezone="Asia/Shanghai",
            country=data.get("country_code"),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
