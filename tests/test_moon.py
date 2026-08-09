"""赏月指南模块测试。"""
from datetime import date, datetime, timedelta

from app.services.moon import compute_moon_viewing, moon_phase, moonrise_estimate
from tests.helpers import make_day, make_hour


def _night_day(cloud_cover, date_str="2026-08-28"):
    """构造一个满月之夜的测试日（2026-08-28 前后为满月期）。"""
    base = datetime(2026, 8, 28, 0, 0)
    hours = []
    for i in range(24):
        h = base + timedelta(hours=i)
        cloud = cloud_cover if (h.hour >= 20 or h.hour < 3) else 30.0
        hours.append(make_hour(h, cloud_cover=cloud))
    return make_day(date=date_str, hours=hours)


def test_moon_phase_known_full_moon():
    """2024-04-23 为满月，照明度应接近 100%。"""
    name, illum = moon_phase(date(2024, 4, 23))
    assert name == "满月"
    assert illum >= 95


def test_moon_phase_known_new_moon():
    """2024-04-08 为新月，照明度应接近 0。"""
    name, illum = moon_phase(date(2024, 4, 8))
    assert name == "新月"
    assert illum <= 5


def test_moon_illumination_range():
    name, illum = moon_phase(date(2026, 8, 9))
    assert 0 <= illum <= 100
    assert name in ("新月", "娥眉月", "上弦月", "盈凸月", "满月", "亏凸月", "下弦月", "残月")


def test_moonrise_estimate_format():
    mr = moonrise_estimate(date(2026, 8, 9))
    assert mr is not None
    hh, mm = mr.split(":")
    assert 0 <= int(hh) < 24
    assert 0 <= int(mm) < 60


def test_clear_full_moon_high_score():
    day = _night_day(cloud_cover=15.0)
    info = compute_moon_viewing(day)
    assert info.illuminated >= 90
    assert info.score >= 70
    assert info.level == "高"


def test_cloudy_night_lower_score():
    clear = compute_moon_viewing(_night_day(cloud_cover=15.0))
    cloudy = compute_moon_viewing(_night_day(cloud_cover=90.0))
    assert cloudy.score < clear.score
    assert cloudy.score <= 40


def test_near_new_moon_low_score():
    """新月夜（照明度 <8%）赏月指数应被强制压低。"""
    base = datetime(2026, 8, 12, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), cloud_cover=10.0) for i in range(24)]
    day = make_day(date="2026-08-12", hours=hours)
    info = compute_moon_viewing(day)
    assert info.illuminated < 8
    assert info.score <= 20
    assert info.level == "低"


def test_crescent_night_moderate_score():
    """娥眉月/残月夜（照明度 8-25%）评分应显著低于满月。"""
    base = datetime(2026, 8, 9, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), cloud_cover=10.0) for i in range(24)]
    day = make_day(date="2026-08-09", hours=hours)
    crescent = compute_moon_viewing(day)
    full = compute_moon_viewing(_night_day(cloud_cover=10.0))
    assert crescent.score < full.score
    assert crescent.score < 60


def test_extra_hours_include_early_morning():
    day = _night_day(cloud_cover=15.0, date_str="2026-08-28")
    extra_base = datetime(2026, 8, 29, 0, 0)
    extra = [make_hour(extra_base + timedelta(hours=i), cloud_cover=10.0) for i in range(3)]
    info = compute_moon_viewing(day, extra_hours=extra)
    assert info.cloud_cover is not None
