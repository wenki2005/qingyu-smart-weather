"""天气数据汇总为完整报告（供桌面界面使用）。"""
from __future__ import annotations

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
    """拉取天气数据并组装完整报告。失败返回 None。

    override_location：指定位置（如来自 IP 定位），None 时按配置解析。
    fahrenheit：温度为华氏度时置 True。
    include_glow：是否计算霞光地图（全国城市热力）。
    nmc_enabled / typhoon_enabled：是否启用中央气象台在线预警 / 台风路径判定（设置功能）。
    official_enabled：是否拉取中国天气网官方实况/预报。
    models：Open-Meteo 预报模型运行时覆盖（如 cma_grapes_global）。
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

    # 空气质量（可选能力，失败不阻断）
    report.air_quality = await weather.get_air_quality(location)

    # 官方天气（中国天气网实况 + 今日预报，仅国内城市有代码时启用）
    if official_enabled and getattr(location, "wcn_code", None):
        official = OfficialWeather()
        try:
            report.official = await official.fetch(location)
        except Exception as e:
            logger.warning("官方天气获取失败: %s", e)
        finally:
            await official.aclose()

    # 晚霞（今日）
    today = daily[0]
    report.sunset = compute_sunset_probability(today, today.sunset, today.hours)

    # 赏月指南（今晚 = 今日 20 点后 + 明日凌晨）
    extra_hours = daily[1].hours if len(daily) > 1 else None
    report.moon = compute_moon_viewing(today, extra_hours=extra_hours)

    # 特殊天象播报（流星雨 / 极光 / 日月食 / 超级月亮）
    try:
        report.astro_events = await build_astronomy(location.latitude, daily)
    except Exception as e:
        logger.warning("天象播报计算失败: %s", e)

    # 特殊天气（规则引擎）
    report.warnings = warnings_svc.detect_rules(daily)

    # 台风路径判定（联网，仅真正受台风影响时给出；可在设置中关闭）
    if typhoon_enabled:
        try:
            report.warnings.extend(
                await warnings_svc.detect_typhoon(location.latitude, location.longitude)
            )
        except Exception as e:
            logger.warning("台风判定失败: %s", e)

    # 中央气象台在线预警（免费、联网获取当前有效预警；可在设置中关闭）
    if nmc_enabled:
        try:
            report.warnings.extend(
                await warnings_svc.fetch_nmc(location.admin1 or "", location.admin2 or "")
            )
        except Exception as e:
            logger.warning("NMC 预警拉取失败: %s", e)

    # 和风天气官方预警（可选，配置 Key 后启用）
    try:
        official = await warnings_svc.fetch_official(location.latitude, location.longitude)
        report.warnings.extend(official)
    except Exception as e:
        logger.warning("和风官方预警拉取失败: %s", e)

    # 观赏推荐（今日 + 明日），按类别去重，避免同类重复展示
    groups = [rec_engine.recommend(today, day_label="今天")]
    if len(daily) > 1:
        groups.append(rec_engine.recommend(daily[1], day_label="明天"))
    report.recommendations = merge_recommendations(groups)

    # 生活指数（今日）
    next_day = daily[1] if len(daily) > 1 else None
    report.life_indices = LifeIndexService().compute(today, next_day)

    # 霞光地图（全国主要城市朝霞/晚霞热力，仅桌面界面需要）
    if include_glow:
        glow = GlowMapService(settings, models=models)
        try:
            report.glow_map = await glow.compute()
        except Exception as e:
            logger.warning("霞光地图计算失败: %s", e)
        finally:
            await glow.aclose()

    return report
