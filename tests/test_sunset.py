from datetime import datetime, timedelta

from app.services.sunset import compute_sunset_probability
from tests.helpers import make_day, make_hour


def _sunset_day(cloud_cover, precip_prob=0.0, weather_code=1):
    base = datetime(2026, 8, 8, 0, 0)
    hours = [
        make_hour(base + timedelta(hours=i), cloud_cover=cloud_cover, precip_probability=precip_prob)
        for i in range(24)
    ]
    return make_day(hours=hours, weather_code=weather_code)


def test_ideal_clouds_give_high_probability():
    """云量 35%（理想值）时晚霞概率应较高。"""
    day = _sunset_day(cloud_cover=35.0)
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability >= 75
    assert info.level == "高"


def test_overcast_gives_low_probability():
    """全阴天（云量 95%）时晚霞概率应很低。"""
    day = _sunset_day(cloud_cover=95.0)
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability <= 40
    assert info.level in ("低", "中")


def test_clear_sky_moderate_probability():
    """晴空（云量 5%）无云反射，概率适中偏低。"""
    day = _sunset_day(cloud_cover=5.0)
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability < 75


def test_rain_suppresses_probability():
    """高降水概率会显著压低晚霞得分。"""
    day = _sunset_day(cloud_cover=35.0, precip_prob=95.0)
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability < 60


def test_thunderstorm_outside_sunset_window_does_not_suppress():
    """早上的雷暴不应误伤晚霞评分。"""
    day = _sunset_day(cloud_cover=35.0, weather_code=95)
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability > 25


def test_thunderstorm_in_sunset_window_hard_suppresses():
    """日落窗口内的雷暴仍应强制压低晚霞概率。"""
    day = _sunset_day(cloud_cover=35.0, weather_code=1)
    sunset_hour = 19
    day.hours[sunset_hour] = make_hour(
        day.hours[sunset_hour].time,
        cloud_cover=35.0,
        weather_code=95,
    )
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability <= 25
    assert info.level == "低"


def test_missing_sunset_returns_zero():
    day = make_day(sunset=None)
    info = compute_sunset_probability(day, day.sunset, day.hours)
    assert info.probability == 0.0


def test_layered_clouds_are_used_for_sunset_score():
    """中高云适中而低云较少时，评分应高于低云遮挡场景。"""
    base = datetime(2026, 8, 8, 0, 0)
    clear_upper = [
        make_hour(base + timedelta(hours=i), cloud_cover=70.0,
                  cloud_cover_low=10.0, cloud_cover_mid=45.0, cloud_cover_high=50.0)
        for i in range(24)
    ]
    blocked = [
        make_hour(base + timedelta(hours=i), cloud_cover=70.0,
                  cloud_cover_low=95.0, cloud_cover_mid=45.0, cloud_cover_high=50.0)
        for i in range(24)
    ]
    good = compute_sunset_probability(make_day(hours=clear_upper), "2026-08-08T19:20", clear_upper)
    bad = compute_sunset_probability(make_day(hours=blocked), "2026-08-08T19:20", blocked)
    assert good.probability > bad.probability


def test_air_pollution_reduces_sunset_score():
    from app.models import AirQuality
    day = _sunset_day(cloud_cover=35.0)
    clean = compute_sunset_probability(day, day.sunset, day.hours, AirQuality(aqi=25, pm25=10))
    polluted = compute_sunset_probability(day, day.sunset, day.hours, AirQuality(aqi=180, pm25=180))
    assert polluted.probability < clean.probability
