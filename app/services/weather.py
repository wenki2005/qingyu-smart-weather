"""Open-Meteo 天气数据客户端。

- 地理编码：支持中文/英文城市名解析为经纬度
- 预报：小时级 + 日级数据（温度、降水、云量、风速、能见度、紫外线、日出日落等）
- 数据源免费、无需 API Key、覆盖全球
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from ..config import Settings
from ..constants import WMO_WEATHER_CODE
from ..models import AirQuality, DailyWeather, HourlyWeather, Location
from .cn_geocoder import _is_cjk, geocode_cn

logger = logging.getLogger(__name__)

AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

HOURLY_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "visibility",
    "surface_pressure",
]

DAILY_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "sunrise",
    "sunset",
    "uv_index_max",
    "precipitation_probability_max",
]


class WeatherClient:
    """Open-Meteo 天气客户端。"""

    def __init__(
        self,
        settings: Settings,
        client: Optional[httpx.AsyncClient] = None,
        models: Optional[str] = None,
    ):
        self._settings = settings
        self._client = client
        self._models = models  # 运行时模型覆盖（设置功能），None 用 settings.open_meteo_models

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.open_meteo_timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except RuntimeError:
                # Windows 上 asyncio.run 结束后 loop 已关闭，忽略
                pass
            self._client = None

    # ------------------------------------------------------------------ #
    # 地理编码
    # ------------------------------------------------------------------ #
    async def geocode(self, city: str) -> Optional[Location]:
        """将城市名解析为坐标，返回 None 表示未找到。

        中文查询优先走 weather.com.cn + Open-Meteo 双源（`cn_geocoder`），
        解决 Open-Meteo 对县城/县级市覆盖不全、歧义名解析错误的问题；
        英文/拼音查询仍走 Open-Meteo 原生地理编码。
        """
        if not city:
            return None
        client = await self._http()
        if _is_cjk(city):
            loc = await geocode_cn(client, city)
            if loc is not None:
                return loc
            # weather.com.cn 不可用或未匹配到坐标时，回退 Open-Meteo
        params = {
            "name": city,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
        resp = await client.get(self._settings.open_meteo_geo_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return None
        r = results[0]
        return Location(
            name=r.get("name") or city,
            latitude=r["latitude"],
            longitude=r["longitude"],
            timezone=r.get("timezone") or self._settings.timezone,
            country=r.get("country"),
            admin1=r.get("admin1"),
            admin2=r.get("admin2"),
        )

    async def resolve_location(self) -> Optional[Location]:
        """按配置解析位置：优先显式经纬度，否则按城市名地理编码。"""
        s = self._settings
        if s.latitude is not None and s.longitude is not None:
            return Location(
                name=s.effective_city,
                latitude=s.latitude,
                longitude=s.longitude,
                timezone=s.timezone,
            )
        return await self.geocode(s.city) if s.city else None

    # ------------------------------------------------------------------ #
    # 预报获取
    # ------------------------------------------------------------------ #
    async def get_forecast(
        self,
        location: Location,
        forecast_days: Optional[int] = None,
        fahrenheit: bool = False,
    ) -> list[DailyWeather]:
        """获取未来 N 天逐小时预报并按天分组。

        fahrenheit=True 时温度使用华氏度（Open-Meteo 参数 temperature_unit）。
        """
        days = forecast_days or self._settings.forecast_days
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "hourly": ",".join(HOURLY_FIELDS),
            "daily": ",".join(DAILY_FIELDS),
            "timezone": location.timezone,
            "forecast_days": days,
        }
        if fahrenheit:
            params["temperature_unit"] = "fahrenheit"
        models = self._models or self._settings.open_meteo_models
        if models:
            params["models"] = models
        client = await self._http()
        resp = await client.get(self._settings.open_meteo_base_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        return self._parse_forecast(data)

    async def get_daily_glow(self, location: Location) -> list[DailyWeather]:
        """获取霞光评估所需的最小预报集（云量/降水/能见度/湿度 + 日出日落）。

        用于霞光地图：批量评估全国主要城市的朝霞/晚霞条件，减少传输与解析开销。
        """
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "hourly": (
                "weather_code,cloud_cover,precipitation_probability,precipitation,"
                "visibility,relative_humidity_2m"
            ),
            "daily": "sunrise,sunset",
            "timezone": location.timezone,
            "forecast_days": 2,
        }
        models = self._models or self._settings.open_meteo_models
        if models:
            params["models"] = models
        client = await self._http()
        resp = await client.get(self._settings.open_meteo_base_url, params=params)
        resp.raise_for_status()
        return self._parse_forecast(resp.json())

    # ------------------------------------------------------------------ #
    # 空气质量
    # ------------------------------------------------------------------ #
    async def get_air_quality(self, location: Location) -> Optional[AirQuality]:
        """获取当前空气质量（US AQI + 颗粒物/污染物），失败返回 None。"""
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "hourly": (
                "us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide"
            ),
            "timezone": "auto",
            "forecast_days": 1,
        }
        try:
            client = await self._http()
            resp = await client.get(AIR_QUALITY_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            hourly = data.get("hourly", {})
            times = hourly.get("time", [])

            def latest(key: str) -> Optional[float]:
                """取最近一个非空时刻的数值。"""
                vals = hourly.get(key) or []
                for i in range(len(times) - 1, -1, -1):
                    if i < len(vals) and vals[i] is not None:
                        return float(vals[i])
                return None

            aqi = latest("us_aqi")
            if aqi is None:
                return None
            pm25 = latest("pm2_5")
            pm10 = latest("pm10")
            ozone = latest("ozone")
            no2 = latest("nitrogen_dioxide")
            so2 = latest("sulphur_dioxide")
            co = latest("carbon_monoxide")
            return AirQuality(
                aqi=round(aqi, 0),
                level=AirQuality.level_for(aqi),
                pm25=round(pm25, 1) if pm25 is not None else None,
                pm10=round(pm10, 1) if pm10 is not None else None,
                ozone=round(ozone, 1) if ozone is not None else None,
                no2=round(no2, 1) if no2 is not None else None,
                so2=round(so2, 1) if so2 is not None else None,
                co=round(co, 1) if co is not None else None,
            )
        except Exception as e:
            logger.warning("获取空气质量失败: %s", e)
            return None

    @staticmethod
    def _parse_forecast(data: dict) -> list[DailyWeather]:
        """将 Open-Meteo 响应解析为按天分组的模型。"""
        hourly = data.get("hourly", {})
        daily = data.get("daily", {})

        times = hourly.get("time", [])
        by_day: dict[str, dict] = {}
        day_order: list[str] = []

        for idx, t_str in enumerate(times):
            day = t_str[:10]
            if day not in by_day:
                by_day[day] = {"hours": [], "date": day}
                day_order.append(day)
            by_day[day]["hours"].append(
                HourlyWeather(
                    time=_parse_iso(t_str),
                    temperature=_val(hourly.get("temperature_2m"), idx),
                    apparent_temperature=_val(hourly.get("apparent_temperature"), idx),
                    humidity=_val(hourly.get("relative_humidity_2m"), idx),
                    precip_probability=_val(
                        hourly.get("precipitation_probability"), idx
                    ),
                    precipitation=_val(hourly.get("precipitation"), idx),
                    weather_code=_int_val(hourly.get("weather_code"), idx),
                    cloud_cover=_val(hourly.get("cloud_cover"), idx),
                    wind_speed=_val(hourly.get("wind_speed_10m"), idx),
                    wind_gusts=_val(hourly.get("wind_gusts_10m"), idx),
                    wind_direction=_val(hourly.get("wind_direction_10m"), idx),
                    visibility=_val(hourly.get("visibility"), idx),
                    surface_pressure=_val(hourly.get("surface_pressure"), idx),
                )
            )

        result: list[DailyWeather] = []
        d_times = daily.get("time", [])
        for idx, day in enumerate(d_times):
            bucket = by_day.get(day, {"hours": [], "date": day})
            result.append(
                DailyWeather(
                    date=day,
                    weather_code=_int_val(daily.get("weather_code"), idx),
                    temp_max=_val(daily.get("temperature_2m_max"), idx),
                    temp_min=_val(daily.get("temperature_2m_min"), idx),
                    sunrise=_str_val(daily.get("sunrise"), idx),
                    sunset=_str_val(daily.get("sunset"), idx),
                    uv_index_max=_val(daily.get("uv_index_max"), idx),
                    precip_probability_max=_val(
                        daily.get("precipitation_probability_max"), idx
                    ),
                    hours=bucket["hours"],
                )
            )
        return result

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def describe_code(code: Optional[int]) -> tuple[str, str, str]:
        """天气码 -> (中文描述, 分类, emoji)。未知码返回原样。"""
        if code is None:
            return "未知", "unknown", "🌡"
        return WMO_WEATHER_CODE.get(code, (f"代码{code}", "unknown", "🌡"))


def _parse_iso(s: str) -> datetime:
    # "2026-08-08T18:00" -> datetime（本地时间字符串，不带时区）
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M")


def _val(arr, idx):
    if arr is None or idx >= len(arr):
        return None
    v = arr[idx]
    return float(v) if v is not None else None


def _int_val(arr, idx):
    v = _val(arr, idx)
    return int(v) if v is not None else None


def _str_val(arr, idx):
    if arr is None or idx >= len(arr):
        return None
    return str(arr[idx]) if arr[idx] is not None else None


def hour_window(hours: list[HourlyWeather], target: datetime, delta_minutes: int = 120) -> list[HourlyWeather]:
    """取 target 前后 delta 分钟内的所有小时点。"""
    lo = target - timedelta(minutes=delta_minutes)
    hi = target + timedelta(minutes=delta_minutes)
    return [h for h in hours if lo <= h.time <= hi]



def next_rain_time(
    hours: list[HourlyWeather],
    now: Optional[datetime] = None,
    prob_threshold: float = 40.0,
) -> Optional[datetime]:
    """返回自 now 起最近一次可能降雨的小时点（按降水概率阈值），无则 None。

    用于「带伞提醒」：降水概率 >= prob_threshold 且降水量 > 0 视为降雨。
    """
    now = now or datetime.now()
    for h in hours:
        if h.time < now:
            continue
        prob = h.precip_probability or 0.0
        amount = h.precipitation or 0.0
        if prob >= prob_threshold and amount > 0.0:
            return h.time
    return None
