"""霞光地图：评估全国主要城市的朝霞 / 晚霞观赏条件。

数据源：Open-Meteo（免费、无 Key）。对每个城市拉取最小预报集，
复用晚霞概率模型与朝霞评分逻辑，输出城市级「霞光」数据供前端地图热力展示。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..config import Settings
from ..models import GlowCity, Location
from .recommendation import RecommendationEngine
from .sunset import compute_sunset_probability
from .weather import WeatherClient

logger = logging.getLogger(__name__)

# 主要城市（直辖市 / 省会 / 计划单列市 / 港澳台）：(名称, 纬度, 经度)
CITIES: list[tuple[str, float, float]] = [
    ("哈尔滨", 45.8038, 126.5349),
    ("长春", 43.8171, 125.3235),
    ("沈阳", 41.8057, 123.4315),
    ("北京", 39.9042, 116.4074),
    ("天津", 39.0842, 117.2009),
    ("石家庄", 38.0428, 114.5149),
    ("太原", 37.8706, 112.5489),
    ("呼和浩特", 40.8414, 111.7519),
    ("济南", 36.6512, 117.1201),
    ("青岛", 36.0671, 120.3826),
    ("郑州", 34.7466, 113.6254),
    ("西安", 34.3416, 108.9398),
    ("兰州", 36.0611, 103.8343),
    ("西宁", 36.6171, 101.7782),
    ("银川", 38.4872, 106.2309),
    ("乌鲁木齐", 43.8256, 87.6168),
    ("南京", 32.0603, 118.7969),
    ("上海", 31.2304, 121.4737),
    ("杭州", 30.2741, 120.1551),
    ("合肥", 31.8206, 117.2272),
    ("武汉", 30.5928, 114.3055),
    ("长沙", 28.2282, 112.9388),
    ("南昌", 28.6820, 115.8579),
    ("福州", 26.0745, 119.2965),
    ("厦门", 24.4798, 118.0894),
    ("广州", 23.1291, 113.2644),
    ("深圳", 22.5431, 114.0579),
    ("南宁", 22.8170, 108.3665),
    ("海口", 20.0440, 110.1999),
    ("贵阳", 26.6470, 106.6302),
    ("昆明", 24.8801, 102.8329),
    ("成都", 30.5728, 104.0668),
    ("重庆", 29.5630, 106.5516),
    ("拉萨", 29.6520, 91.1721),
    ("香港", 22.3193, 114.1694),
    ("澳门", 22.1987, 113.5439),
    ("台北", 25.0330, 121.5654),
]


class GlowMapService:
    """霞光地图服务：批量计算全国主要城市朝霞/晚霞指数。"""

    def __init__(self, settings: Settings, models: Optional[str] = None):
        self._settings = settings
        self._weather = WeatherClient(settings, models=models)
        self._rec = RecommendationEngine()

    async def compute(self) -> list[GlowCity]:
        locs = [
            Location(name=name, latitude=lat, longitude=lon, timezone="Asia/Shanghai")
            for name, lat, lon in CITIES
        ]
        results = await asyncio.gather(
            *[self._one(loc) for loc in locs],
            return_exceptions=True,
        )
        out: list[GlowCity] = [r for r in results if isinstance(r, GlowCity)]
        out.sort(key=lambda c: c.sunset_probability, reverse=True)
        return out

    async def _one(self, loc: Location) -> Optional[GlowCity]:
        try:
            daily = await self._weather.get_daily_glow(loc)
            if not daily:
                return None
            today = daily[0]
            tomorrow = daily[1] if len(daily) > 1 else today
            sunset = compute_sunset_probability(today, today.sunset, today.hours)
            rec = self._rec._sunrise(tomorrow)
            sunrise_score = float(rec.score) if rec else 0.0
            sunrise_level = rec.level if rec else "一般"
            return GlowCity(
                name=loc.name,
                latitude=loc.latitude,
                longitude=loc.longitude,
                sunset_probability=round(sunset.probability, 1),
                sunset_level=sunset.level,
                sunset_time=(sunset.sunset_time or "")[-5:],
                sunrise_score=round(sunrise_score, 1),
                sunrise_level=sunrise_level,
                sunrise_time=(tomorrow.sunrise or "")[-5:] if tomorrow.sunrise else "",
            )
        except Exception as e:
            logger.warning("霞光地图计算 %s 失败: %s", loc.name, e)
            return None

    async def aclose(self) -> None:
        await self._weather.aclose()
