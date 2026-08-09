"""生活指数模块测试。"""
from datetime import datetime, timedelta

from app.services.life import LifeIndexService
from tests.helpers import make_day, make_hour


def _day(temp_max=30.0, temp_min=20.0, pop=10.0, uv=6.0, **hour_kw):
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i)) for i in range(24)]
    return make_day(temp_max=temp_max, temp_min=temp_min,
                    uv_index_max=uv, precip_probability_max=pop, hours=hours)


def _all(life):
    return {x.name: x for x in life}


def test_dressing_hot():
    life = _all(LifeIndexService().compute(_day(temp_max=34.0, temp_min=28.0)))
    assert life["穿衣"].level == "炎热"


def test_dressing_cold():
    life = _all(LifeIndexService().compute(_day(temp_max=6.0, temp_min=-2.0)))
    assert life["穿衣"].level in ("冷", "寒冷")


def test_umbrella_when_rain():
    base = datetime(2026, 8, 8, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), precip_probability=80.0,
                       precipitation=0.8 if i == 12 else 0.0) for i in range(24)]
    day = make_day(precip_probability_max=80.0, hours=hours)
    life = _all(LifeIndexService().compute(day))
    assert life["带伞"].level in ("必带", "建议带")


def test_carwash_discouraged_when_rain_tomorrow():
    base = datetime(2026, 8, 8, 0, 0)
    today = make_day(hours=[make_hour(base + timedelta(hours=i)) for i in range(24)])
    tomorrow = make_day(
        date="2026-08-09",
        precip_probability_max=90.0,
        hours=[make_hour(base + timedelta(days=1, hours=i), precipitation=0.5) for i in range(24)],
    )
    life = _all(LifeIndexService().compute(today, tomorrow))
    assert life["洗车"].level == "不适宜"


def test_sunscreen_strong_uv():
    life = _all(LifeIndexService().compute(_day(uv=9.0)))
    assert life["防晒"].level == "极强"


def test_cold_high_swing():
    life = _all(LifeIndexService().compute(_day(temp_max=25.0, temp_min=8.0)))
    assert life["感冒"].level == "易发"


def test_all_indices_present():
    life = LifeIndexService().compute(_day())
    names = {x.name for x in life}
    assert {"穿衣", "带伞", "洗车", "防晒", "运动", "晾晒", "感冒"} <= names
