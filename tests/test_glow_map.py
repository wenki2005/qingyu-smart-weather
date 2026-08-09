"""测试霞光地图服务的单城计算逻辑（mock 天气数据）。"""
import pytest

from app.models import Location
from app.services.glow_map import GlowMapService
from tests.helpers import make_day


@pytest.mark.asyncio
async def test_glow_map_one_city(settings):
    svc = GlowMapService(settings)
    today = make_day()                              # 2026-08-08，日落 19:20
    tomorrow = make_day(date="2026-08-09")          # 次日上午日出 05:20

    async def fake_get_daily_glow(loc):
        return [today, tomorrow]

    svc._weather.get_daily_glow = fake_get_daily_glow
    city = await svc._one(Location(name="杭州", latitude=30.29, longitude=120.16))
    assert city is not None
    assert city.name == "杭州"
    assert 0 <= city.sunset_probability <= 100
    assert 0 <= city.sunrise_score <= 100
    assert city.sunset_time == "19:20"
    assert city.sunrise_time == "05:20"
    await svc.aclose()


@pytest.mark.asyncio
async def test_glow_map_skips_failed_city(settings):
    svc = GlowMapService(settings)

    async def fail(loc):
        raise RuntimeError("network error")

    svc._weather.get_daily_glow = fail
    city = await svc._one(Location(name="北京", latitude=39.9, longitude=116.4))
    assert city is None
    await svc.aclose()
