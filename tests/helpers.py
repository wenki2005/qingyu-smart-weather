"""测试数据工厂（供各测试模块引用）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.models import DailyWeather, HourlyWeather


def make_hour(t: datetime, **kw) -> HourlyWeather:
    defaults = dict(
        time=t,
        temperature=25.0,
        apparent_temperature=26.0,
        humidity=55.0,
        precip_probability=10.0,
        precipitation=0.0,
        weather_code=1,
        cloud_cover=30.0,
        wind_speed=3.0,
        visibility=20000.0,
        surface_pressure=1013.0,
    )
    defaults.update(kw)
    return HourlyWeather(**defaults)


def make_day(
    date: str = "2026-08-08",
    weather_code: int = 1,
    temp_max: float = 30.0,
    temp_min: float = 20.0,
    sunrise: Optional[str] = "2026-08-08T05:20",
    sunset: Optional[str] = "2026-08-08T19:20",
    uv_index_max: float = 6.0,
    precip_probability_max: float = 10.0,
    hours: list[HourlyWeather] | None = None,
) -> DailyWeather:
    base = datetime(2026, 8, 8, 0, 0)
    if hours is None:
        hours = [make_hour(base + timedelta(hours=i)) for i in range(24)]
    return DailyWeather(
        date=date,
        weather_code=weather_code,
        temp_max=temp_max,
        temp_min=temp_min,
        sunrise=sunrise,
        sunset=sunset,
        uv_index_max=uv_index_max,
        precip_probability_max=precip_probability_max,
        hours=hours,
    )
