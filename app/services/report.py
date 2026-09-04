"""天气数据汇总为完整报告（供桌面界面使用）。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from ..config import Settings
from ..models import Location, WeatherReport
from .astronomy import build_astronomy
from .glow_map import GlowMapService
from .life import LifeIndexService
from .moon import compute_moon_viewing
from .official import OfficialWeather
from .recommendation import RecommendationEngine, merge_recommendations
from .sunset import compute_sunset_probability
from .warnings import WarningService
from .weather import WeatherClient

logger = logging.getLogger(__name__)


async def _safe(coro, default, timeout: Optional[float] = None):
    """单个增强数据源失败或超时时不阻断主天气报告。"""
    try:
        if timeout and timeout > 0:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro
    except Exception as exc:
        logger.warning("增强数据源获取失败: %s", exc)
        return default


async def build_report(
    weather: WeatherClient,
    warnings_svc: WarningService,
    rec_engine: RecommendationEngine,
    settings: Settings,
    override_location: Optional[Location] = None,
    fahrenheit: bool = False,
    include_glow: bool = False,
    nmc_enabled: bool = True,
    typhoon_enabled: bool = True,
    official_enabled: bool = True,
    models: Optional[str] = None,
) -> Optional[WeatherReport]:
    """拉取天气数据并组装完整报告。

    当前地点的天气、空气质量、官方数据、天象和预警在天气预报成功后并发获取。
    霞光地图是全国级增强数据，调用方应在主报告之外单独缓存/刷新。
    """
    location: Optional[Location] = override_location
    if location is None:
        location = await weather.resolve_location()
    if location is None:
        logger.error("无法解析位置（city=%s）", settings.city)
        return None

    daily = await weather.get_forecast(location, fahrenheit=fahrenheit)
    if not daily:
        logger.error("天气预报数据为空")
        return None

    report = WeatherReport(
        location=location.name,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        daily=daily,
    )
    today = daily[0]

    async def fetch_official() -> Optional[dict]:
        if not official_enabled or not getattr(location, "wcn_code", None):
            return None
        official = OfficialWeather()
        try:
            return await official.fetch(location)
        finally:
            await official.aclose()

    async def fetch_typhoon() -> list:
        if not typhoon_enabled:
            return []
        return await warnings_svc.detect_typhoon(location.latitude, location.longitude)

    async def fetch_nmc() -> list:
        if not nmc_enabled:
            return []
        return await warnings_svc.fetch_nmc(
            location.admin1 or "",
            location.admin2 or "",
            location.admin3 or "",
            max_age_hours=getattr(settings, "warning_max_age_hours", 12.0),
        )

    enhancement_timeout = getattr(settings, "enhancement_timeout_seconds", 6.0)
    aq_task = _safe(weather.get_air_quality(location), None, enhancement_timeout)
    official_task = _safe(fetch_official(), None, enhancement_timeout)
    astro_task = _safe(build_astronomy(location.latitude, daily), [], enhancement_timeout)
    typhoon_task = _safe(fetch_typhoon(), [], enhancement_timeout)
    nmc_task = _safe(fetch_nmc(), [], enhancement_timeout)
    qweather_task = _safe(
        warnings_svc.fetch_official(location.latitude, location.longitude), [], enhancement_timeout
    )
    (
        report.air_quality,
        report.official,
        report.astro_events,
        typhoon_warnings,
        nmc_warnings,
        qweather_warnings,
    ) = await asyncio.gather(
        aq_task,
        official_task,
        astro_task,
        typhoon_task,
        nmc_task,
        qweather_task,
    )

    # 晚霞使用空气质量作为天空通透度修正。
    report.sunset = compute_sunset_probability(
        today, today.sunset, today.hours, air_quality=report.air_quality
    )
    extra_hours = daily[1].hours if len(daily) > 1 else None
    report.moon = compute_moon_viewing(today, extra_hours=extra_hours)

    try:
        from zoneinfo import ZoneInfo
        local_now = datetime.now(ZoneInfo(location.timezone)).replace(tzinfo=None)
    except Exception:
        local_now = datetime.now()
    report.warnings = warnings_svc.detect_rules(daily, now=local_now)
    report.warnings.extend(typhoon_warnings)
    report.warnings.extend(nmc_warnings)
    report.warnings.extend(qweather_warnings)

    groups = [rec_engine.recommend(today, day_label="今天")]
    if len(daily) > 1:
        groups.append(rec_engine.recommend(daily[1], day_label="明天"))
    report.recommendations = merge_recommendations(groups)

    next_day = daily[1] if len(daily) > 1 else None
    report.life_indices = LifeIndexService().compute(today, next_day)

    # 兼容 CLI/第三方调用；桌面端传 False，把全国图移到 get_glow_map()。
    if include_glow:
        glow = GlowMapService(settings, models=models)
        try:
            report.glow_map = await glow.compute()
        finally:
            await glow.aclose()

    return report
