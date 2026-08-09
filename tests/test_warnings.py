from datetime import datetime, timedelta

import pytest

from app.config import Settings
from app.services.warnings import RuleEngine
from tests.helpers import make_day, make_hour


def test_heat_warning():
    day = make_day(temp_max=41.0, temp_min=28.0)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    titles = [w.title for w in warns]
    assert any("高温" in t for t in titles)


def test_high_wind_warning():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), wind_speed=20.0 if i == 12 else 3.0) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    assert any("大风" in w.title for w in warns)


def test_heavy_rain_warning():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), precipitation=15.0 if i == 12 else 0.0) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    assert any("强降雨" in w.title for w in warns)


def test_fog_warning():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), visibility=500.0 if 6 <= i <= 8 else 20000.0) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    assert any("大雾" in w.title for w in warns)


def test_no_warning_in_normal_weather():
    day = make_day(temp_max=28.0, temp_min=18.0)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    assert warns == []


def test_hail_warning_orange():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), weather_code=96 if i == 12 else 1) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    hail = [w for w in warns if "冰雹" in w.title]
    assert hail and hail[0].level == "橙色"


def test_heavy_hail_warning_red():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), weather_code=99 if i == 12 else 1) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    hail = [w for w in warns if "冰雹" in w.title]
    assert hail and hail[0].level == "红色"


def test_strong_wind_warning():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), wind_speed=26.0 if i == 12 else 3.0) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    assert any("强风" in w.title for w in warns)


def test_extreme_wind_is_kuangfeng_not_typhoon():
    """12 级以上大风是「狂风」而非「台风」——只有检测到真实台风路径才显示台风。"""
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), wind_speed=35.0 if i == 12 else 3.0) for i in range(24)]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    titles = [w.title for w in warns]
    assert any("狂风" in t for t in titles)
    assert not any("台风" in t for t in titles)


def test_strong_gust_warning():
    base = datetime(2026, 8, 8, 0, 0)
    # 持续风速 8 级，阵风强烈 -> 强阵风提醒（不标注台风）
    hours = [
        make_hour(base + timedelta(hours=i), wind_speed=18.0, wind_gusts=35.0 if i == 12 else 20.0)
        for i in range(24)
    ]
    day = make_day(hours=hours)
    warns = RuleEngine(Settings(city="北京")).detect([day])
    titles = [w.title for w in warns]
    assert any("强阵风" in t for t in titles)
    assert not any("台风" in t for t in titles)


@pytest.mark.asyncio
async def test_detect_typhoon_when_affected(settings):
    from app.services.typhoon import TyphoonImpact
    from app.services.warnings import WarningService
    from app.services.weather import WeatherClient

    svc = WarningService(settings, WeatherClient(settings))

    class FakeTyphoon:
        async def aclose(self):
            pass

        async def impact(self, lat, lon):
            return [TyphoonImpact(
                name_cn="灿鸿", name_en="CHAN-HOM", number="2615", category="TY",
                category_cn="台风", wind_speed=33.0, min_distance_km=180.0,
                closest_hour=24, closest_time="202608101400", affected=True,
            )]

    svc._typhoon = FakeTyphoon()
    warns = await svc.detect_typhoon(30.29, 120.16)
    assert any("台风「灿鸿」" in w.title for w in warns)
    assert warns[0].level == "橙色"
    await svc.aclose()


@pytest.mark.asyncio
async def test_detect_typhoon_when_far_away(settings):
    from app.services.typhoon import TyphoonImpact
    from app.services.warnings import WarningService
    from app.services.weather import WeatherClient

    svc = WarningService(settings, WeatherClient(settings))

    class FakeTyphoon:
        async def aclose(self):
            pass

        async def impact(self, lat, lon):
            return [TyphoonImpact(
                name_cn="灿鸿", name_en="CHAN-HOM", number="2615", category="TY",
                category_cn="台风", wind_speed=33.0, min_distance_km=2400.0,
                closest_hour=0, closest_time="202608090600", affected=False,
            )]

    svc._typhoon = FakeTyphoon()
    warns = await svc.detect_typhoon(30.29, 120.16)
    assert warns == []
    await svc.aclose()
