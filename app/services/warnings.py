"""特殊天气检测与官方预警接入。

双通道设计：
1. 内置规则引擎（默认启用）：基于 Open-Meteo 小时预报数据，用阈值规则
   检测大风、高温、低温、强降雨、大雾、强紫外线、雷暴、降雪等特殊天气；
2. 官方预警（可选）：接入和风天气 QWeather 预警 API（需 API Key），
   提供国家级气象台发布的正式预警信息。

设计目标：预警能力可插拔，未配置 QWeather 时系统仍具备完整兜底能力。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from ..config import Settings
from ..models import DailyWeather, WeatherWarning
from .nmc_alarms import NmcAlarmProvider
from .typhoon import NmcTyphoonClient
from .weather import WeatherClient

logger = logging.getLogger(__name__)


def _next_hours(
    daily: list[DailyWeather], limit: int, now: Optional[datetime] = None
) -> list:
    """按城市本地当前时间取未来 limit 小时，规则预警不扫描整个预报周期。"""
    hours = [hour for day in daily for hour in day.hours]
    hours.sort(key=lambda hour: hour.time)
    if now is not None:
        end = now + timedelta(hours=limit)
        return [hour for hour in hours if now <= hour.time < end]
    return hours[:limit]


def _local_now(timezone: str) -> datetime:
    """返回目标城市时区的本地无时区时间，用于对齐 Open-Meteo 小时预报。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
    except Exception:
        return datetime.now()


class RuleEngine:
    """基于预报数据的特殊天气规则引擎。"""

    def __init__(self, settings: Settings):
        self._s = settings

    def detect(
        self, daily: list[DailyWeather], now: Optional[datetime] = None
    ) -> list[WeatherWarning]:
        warnings: list[WeatherWarning] = []
        today = daily[0] if daily else None
        if today is None:
            return warnings

        s = self._s
        max_temp = today.temp_max
        min_temp = today.temp_min

        # 高温
        if max_temp is not None and max_temp >= s.warn_temp_high:
            warnings.append(WeatherWarning(
                title=f"高温提醒：今日最高 {max_temp:.0f}°C",
                level="橙色",
                description="注意防暑降温，避免长时间户外暴晒，及时补水。",
                source="rules",
            ))
        # 低温
        if min_temp is not None and min_temp <= s.warn_temp_low:
            warnings.append(WeatherWarning(
                title=f"低温提醒：今日最低 {min_temp:.0f}°C",
                level="黄色",
                description="注意保暖，谨防冻伤，出行留意路面结冰。",
                source="rules",
            ))

        # 大风 / 强风：只看未来 24 小时，避免把第三天的风险当成当前预警。
        # 注意：普通大风不标注「台风」，只有检测到真实台风路径（见 WarningService.detect_typhoon）
        # 时才给出台风预警，避免把强对流/季风大风误报为台风。
        max_wind = 0.0
        max_gust = 0.0
        forecast_hours = _next_hours(daily, 24, now)
        for h in forecast_hours:
            if h.wind_speed is not None:
                max_wind = max(max_wind, h.wind_speed)
            if h.wind_gusts is not None:
                max_gust = max(max_gust, h.wind_gusts)
        if max_wind >= s.warn_wind_typhoon:
            warnings.append(WeatherWarning(
                title=f"12 级以上狂风提醒：最大持续风速 {max_wind:.0f} m/s",
                level="红色",
                description="风力已达 12 级以上（狂风），请尽量留在室内，远离门窗与高空坠物区域，做好防风加固。",
                source="rules",
            ))
        elif max_wind >= s.warn_wind_strong:
            warnings.append(WeatherWarning(
                title=f"强风提醒：最大持续风速 {max_wind:.0f} m/s",
                level="橙色",
                description="风力强劲，注意加固室外物品，避免前往海边或空旷地带，减少户外活动。",
                source="rules",
            ))
        elif max_wind >= s.warn_wind_speed:
            warnings.append(WeatherWarning(
                title=f"大风提醒：最大风速 {max_wind:.0f} m/s",
                level="黄色",
                description="风力较大，注意高空坠物，关好门窗，减少骑行。",
                source="rules",
            ))
        if max_gust >= s.warn_wind_gust and max_wind < s.warn_wind_typhoon:
            warnings.append(WeatherWarning(
                title=f"强阵风提醒：最大阵风 {max_gust:.0f} m/s",
                level="橙色",
                description="阵风强烈，注意防范瞬时大风，远离广告牌与临时搭建物。",
                source="rules",
            ))

        # 强降雨 / 大雾 / 雷暴 / 冰雹 / 降雪：同样只看未来 24 小时。
        hit: dict[str, float] = {}
        max_hail_code = 0
        for h in forecast_hours:
            if h.precipitation is not None and h.precipitation >= s.warn_precip_hourly:
                hit.setdefault("precip", 0.0)
                hit["precip"] = max(hit["precip"], h.precipitation)
            if h.visibility is not None and h.visibility <= s.warn_visibility:
                hit.setdefault("fog", 0.0)
            if h.weather_code == 95:
                hit.setdefault("thunder", 0.0)
            if h.weather_code in (96, 99):
                hit.setdefault("hail", 0.0)
                max_hail_code = max(max_hail_code, h.weather_code)
            if h.weather_code in (71, 73, 75, 77, 85, 86):
                hit.setdefault("snow", 0.0)

        if "precip" in hit:
            warnings.append(WeatherWarning(
                title=f"强降雨提醒：小时雨强最高 {hit['precip']:.0f} mm",
                level="橙色",
                description="雨势较强，谨防内涝，避免前往低洼地带。",
                source="rules",
            ))
        if "fog" in hit:
            warnings.append(WeatherWarning(
                title="大雾提醒",
                level="黄色",
                description="能见度低，驾车减速慢行，保持车距。",
                source="rules",
            ))
        if "thunder" in hit:
            warnings.append(WeatherWarning(
                title="雷暴提醒",
                level="橙色",
                description="可能伴有雷暴大风，减少户外活动，远离树木。",
                source="rules",
            ))
        if "hail" in hit:
            if max_hail_code == 99:
                warnings.append(WeatherWarning(
                    title="冰雹提醒：雷暴伴有大冰雹",
                    level="红色",
                    description="可能出现大冰雹，请待在室内远离窗户，车辆尽量停入车库或加盖防护。",
                    source="rules",
                ))
            else:
                warnings.append(WeatherWarning(
                    title="冰雹提醒：雷暴伴有小冰雹",
                    level="橙色",
                    description="可能出现冰雹，注意保护车辆与室外物品，尽量减少户外活动。",
                    source="rules",
                ))
        if "snow" in hit:
            warnings.append(WeatherWarning(
                title="降雪提醒",
                level="黄色",
                description="路面湿滑易结冰，注意交通安全与保暖。",
                source="rules",
            ))

        # 强紫外线（按日级数据）
        if today.uv_index_max is not None and today.uv_index_max >= s.warn_uv_index:
            warnings.append(WeatherWarning(
                title=f"强紫外线提醒：UV 指数 {today.uv_index_max:.0f}",
                level="黄色",
                description="紫外线强烈，外出请涂抹防晒霜并佩戴墨镜。",
                source="rules",
            ))
        # 规则引擎预警统一附上官方预警列表页（点击可跳转查看权威信息）
        for w in warnings:
            if not w.url:
                w.url = "http://www.nmc.cn/publish/alarm.html"
        return warnings


class QWeatherProvider:
    """和风天气官方预警（可选）。"""

    def __init__(self, settings: Settings, api_key: str):
        self._s = settings
        self._api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def fetch(self, latitude: float, longitude: float) -> list[WeatherWarning]:
        """获取官方预警。注意 QWeather 需经 LocationID，这里用经纬度->城市搜索接口。"""
        try:
            # 先经纬度反查 LocationID
            city = await self._locate(latitude, longitude)
            if not city:
                return []
            client = await self._http()
            resp = await client.get(
                f"{self._s.qweather_base_url}/warning/now",
                params={"location": city, "key": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "200":
                logger.warning("QWeather warning API error: %s", data.get("code"))
                return []
            out: list[WeatherWarning] = []
            for item in data.get("warning", []) or []:
                out.append(WeatherWarning(
                    title=item.get("title", "天气预警"),
                    level=item.get("typeName", "预警"),
                    description=item.get("text", ""),
                    source="qweather",
                    issued_at=item.get("pubTime", ""),
                ))
            return out
        except Exception as e:
            logger.warning("fetch QWeather warning failed: %s", e)
            return []

    async def _locate(self, latitude: float, longitude: float) -> Optional[str]:
        client = await self._http()
        resp = await client.get(
            f"{self._s.qweather_base_url}/geo/city/lookup",
            params={
                "location": f"{longitude},{latitude}",
                "key": self._api_key,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        locs = data.get("location") or []
        return locs[0]["id"] if locs else None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class WarningService:
    """预警服务：规则引擎 + 在线灾害预警（中央气象台）+ 台风路径判定 + 可选和风官方预警。"""

    def __init__(self, settings: Settings, weather: WeatherClient):
        self._s = settings
        self._rules = RuleEngine(settings)
        self._qweather: Optional[QWeatherProvider] = None
        if settings.qweather_api_key:
            self._qweather = QWeatherProvider(settings, settings.qweather_api_key)
        self._typhoon: Optional[NmcTyphoonClient] = None
        self._nmc: Optional[NmcAlarmProvider] = None

    def detect_rules(
        self, daily: list[DailyWeather], now: Optional[datetime] = None
    ) -> list[WeatherWarning]:
        return self._rules.detect(daily, now=now)

    async def fetch_official(self, latitude: float, longitude: float) -> list[WeatherWarning]:
        if self._qweather is None:
            return []
        return await self._qweather.fetch(latitude, longitude)

    async def detect_typhoon(self, latitude: float, longitude: float) -> list[WeatherWarning]:
        """基于中央气象台台风路径判定：仅真正受台风影响时给出台风预警。"""
        try:
            if self._typhoon is None:
                self._typhoon = NmcTyphoonClient(self._s)
            impacts = await self._typhoon.impact(latitude, longitude)
        except Exception as e:
            logger.warning("台风影响判定失败: %s", e)
            return []
        out: list[WeatherWarning] = []
        for t in impacts:
            if not t.affected:
                continue
            level, tip = _typhoon_level(t.min_distance_km)
            name = f"台风「{t.name_cn}」" if t.name_cn else f"台风 {t.name_en or ''}".strip()
            time_txt = ""
            if t.closest_hour == 0:
                time_txt = "当前正位于路径附近"
            elif t.closest_time:
                time_txt = f"预计 {_fmt_ty_time(t.closest_time)} 前后最接近"
            out.append(WeatherWarning(
                title=f"{name}影响提醒：最近距离约 {t.min_distance_km:.0f} km",
                level=level,
                description=(
                    f"{t.category_cn}（{t.number or '未编号'}），中心附近最大风速 {t.wind_speed:.0f} m/s；"
                    f"{time_txt}。{tip}"
                ),
                source="typhoon",
                url="http://typhoon.nmc.cn/index.html",
            ))
        return out

    async def fetch_nmc(
        self,
        province: str,
        city: str,
        district: str = "",
        max_age_hours: float = 12.0,
    ) -> list[WeatherWarning]:
        """联网获取与当前省/市/区县匹配、仍在时效内的官方预警。"""
        try:
            if self._nmc is None:
                self._nmc = NmcAlarmProvider(self._s)
            return await self._nmc.fetch(
                province, city, district, max_age_hours=max_age_hours
            )
        except Exception as e:
            logger.warning("获取 NMC 预警失败: %s", e)
            return []

    async def aclose(self) -> None:
        if self._qweather is not None:
            await self._qweather.aclose()
        if self._typhoon is not None:
            await self._typhoon.aclose()
        if self._nmc is not None:
            await self._nmc.aclose()


def _typhoon_level(dist_km: float) -> tuple[str, str]:
    """按距台风路径距离给出预警等级与提示。"""
    if dist_km <= 150:
        return "红色", "已进入台风核心影响区，请待在室内，远离门窗，做好断电与应急准备。"
    if dist_km <= 350:
        return "橙色", "受台风直接影响，风强雨急，尽量减少外出，加固门窗与室外物品。"
    if dist_km <= 550:
        return "黄色", "处于台风影响范围内，注意防范大风与强降雨，关注官方最新路径。"
    return "蓝色", "受台风外围影响，风力有所增大，请留意后续路径变化。"


def _fmt_ty_time(t: str) -> str:
    """202608091400 -> 08-09 14:00"""
    try:
        return f"{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}"
    except (IndexError, TypeError):
        return t
