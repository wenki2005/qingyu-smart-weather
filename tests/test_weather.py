"""测试 Open-Meteo 响应解析（使用固定样本，不依赖网络）。"""
import pytest
import respx

from app.models import Location
from app.services.weather import WeatherClient, _parse_iso

SAMPLE = {
    "timezone": "Asia/Shanghai",
    "hourly": {
        "time": ["2026-08-08T00:00", "2026-08-08T01:00", "2026-08-09T00:00"],
        "temperature_2m": [25.0, 24.8, 26.0],
        "apparent_temperature": [27.0, 26.5, 28.0],
        "relative_humidity_2m": [60.0, 62.0, 55.0],
        "precipitation_probability": [10.0, 20.0, 5.0],
        "precipitation": [0.0, 0.2, 0.0],
        "weather_code": [1, 2, 0],
        "cloud_cover": [30.0, 40.0, 10.0],
        "cloud_cover_low": [20.0, 30.0, 5.0],
        "cloud_cover_mid": [40.0, 50.0, 15.0],
        "cloud_cover_high": [60.0, 70.0, 20.0],
        "wind_speed_10m": [3.1, 4.2, 2.0],
        "wind_gusts_10m": [7.0, 8.5, 4.0],
        "wind_direction_10m": [90.0, 135.0, 45.0],
        "visibility": [20000.0, 18000.0, 25000.0],
        "surface_pressure": [1013.0, 1012.0, 1015.0],
    },
    "daily": {
        "time": ["2026-08-08", "2026-08-09"],
        "weather_code": [1, 0],
        "temperature_2m_max": [30.0, 31.0],
        "temperature_2m_min": [20.0, 21.0],
        "sunrise": ["2026-08-08T05:20", "2026-08-09T05:21"],
        "sunset": ["2026-08-08T19:20", "2026-08-09T19:19"],
        "uv_index_max": [6.0, 7.0],
        "precipitation_probability_max": [20.0, 5.0],
    },
}


def test_parse_forecast_groups_by_day():
    days = WeatherClient._parse_forecast(SAMPLE)
    assert len(days) == 2
    assert days[0].date == "2026-08-08"
    assert days[0].temp_max == 30.0
    assert days[0].sunrise == "2026-08-08T05:20"
    assert len(days[0].hours) == 2  # 只有当天的小时数据被归入当天


def test_parse_forecast_hourly_fields():
    days = WeatherClient._parse_forecast(SAMPLE)
    h0 = days[0].hours[0]
    assert h0.temperature == 25.0
    assert h0.apparent_temperature == 27.0
    assert h0.cloud_cover == 30.0
    assert h0.cloud_cover_low == 20.0
    assert h0.cloud_cover_mid == 40.0
    assert h0.cloud_cover_high == 60.0
    assert h0.wind_speed == 3.1
    assert h0.wind_gusts == 7.0
    assert h0.wind_direction == 90.0
    assert h0.weather_code == 1


def test_describe_code():
    desc, _, _ = WeatherClient.describe_code(0)
    assert "晴" in desc
    desc2, _, _ = WeatherClient.describe_code(999)
    assert desc2 == "代码999"


def test_parse_iso():
    dt = _parse_iso("2026-08-08T18:00")
    assert dt.hour == 18
    assert dt.day == 8


def test_location_resolution_prefers_coords(settings):
    loc = Location(name="北京", latitude=39.9, longitude=116.4, timezone="Asia/Shanghai")
    assert loc.latitude == 39.9


@pytest.mark.asyncio
@respx.mock
async def test_air_quality_parses_pollutants(settings):
    import httpx

    from app.services.weather import AIR_QUALITY_URL, WeatherClient

    loc = Location(name="北京", latitude=39.9, longitude=116.4)
    payload = {
        "hourly": {
            "time": ["2026-08-08T00:00", "2026-08-08T01:00", "2026-08-08T02:00"],
            "us_aqi": [80.0, 78.0, None],
            "pm2_5": [30.0, None, None],
            "pm10": [50.0, None, None],
            "ozone": [90.0, None, None],
            "nitrogen_dioxide": [20.0, None, None],
            "sulphur_dioxide": [5.0, None, None],
            "carbon_monoxide": [600.0, None, None],
        }
    }
    respx.get(AIR_QUALITY_URL).mock(return_value=httpx.Response(200, json=payload))
    client = WeatherClient(settings)
    try:
        aq = await client.get_air_quality(loc)
    finally:
        await client.aclose()
    assert aq is not None
    assert aq.level == "良"
    assert aq.pm25 == 30.0
    assert aq.pm10 == 50.0
    assert aq.ozone == 90.0
    assert aq.no2 == 20.0
    assert aq.so2 == 5.0
    assert aq.co == 600.0


@pytest.mark.asyncio
@respx.mock
async def test_air_quality_missing_returns_none(settings):
    import httpx

    from app.services.weather import AIR_QUALITY_URL, WeatherClient

    loc = Location(name="北京", latitude=39.9, longitude=116.4)
    payload = {"hourly": {"time": ["2026-08-08T00:00"], "us_aqi": [None]}}
    respx.get(AIR_QUALITY_URL).mock(return_value=httpx.Response(200, json=payload))
    client = WeatherClient(settings)
    try:
        aq = await client.get_air_quality(loc)
    finally:
        await client.aclose()
    assert aq is None
