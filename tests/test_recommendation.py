from datetime import datetime, timedelta

from app.constants import VIEWING_MOON, VIEWING_SNOW, VIEWING_STAR, VIEWING_SUNRISE, VIEWING_SUNSET
from app.services.recommendation import RecommendationEngine, merge_recommendations
from tests.helpers import make_day, make_hour


def _day_with_clouds(cloud_night, cloud_morning, weather_codes=None):
    base = datetime(2026, 8, 8, 0, 0)
    hours = []
    for i in range(24):
        h = base + timedelta(hours=i)
        cloud = cloud_night if (h.hour >= 20 or h.hour < 3) else cloud_morning
        hours.append(
            make_hour(
                h,
                cloud_cover=cloud,
                weather_code=(weather_codes or {}).get(h.hour, 1),
            )
        )
    return make_day(hours=hours)


def test_stargazing_clear_night():
    """晴朗夜晚 -> 星空观测高分。"""
    day = _day_with_clouds(cloud_night=10.0, cloud_morning=30.0)
    recs = RecommendationEngine().recommend(day)
    star = next(r for r in recs if r.category == VIEWING_STAR)
    assert star.score >= 75
    assert star.level == "强烈推荐"


def test_stargazing_cloudy_night():
    """云量大于 60% 的夜晚 -> 星空低分。"""
    day = _day_with_clouds(cloud_night=85.0, cloud_morning=30.0)
    recs = RecommendationEngine().recommend(day)
    star = next(r for r in recs if r.category == VIEWING_STAR)
    assert star.score <= 25


def test_snow_recommendation_appears():
    """降雪天气 -> 出现雪景推荐。"""
    base = datetime(2026, 8, 8, 0, 0)
    codes = {h: (71 if 10 <= h <= 14 else 1) for h in range(24)}
    hours = [
        make_hour(base + timedelta(hours=i), weather_code=codes[i], precipitation=0.5 if 10 <= i <= 14 else 0.0)
        for i in range(24)
    ]
    day = make_day(hours=hours)
    recs = RecommendationEngine().recommend(day)
    snow = next((r for r in recs if r.category == VIEWING_SNOW), None)
    assert snow is not None
    assert snow.score >= 60


def test_sunrise_recommendation_present():
    day = _day_with_clouds(cloud_night=20.0, cloud_morning=20.0)
    recs = RecommendationEngine().recommend(day)
    assert any(r.category == VIEWING_SUNRISE for r in recs)


def test_moon_recommendation_present_on_full_moon():
    """满月之夜 -> 出现赏月推荐。"""
    base = datetime(2026, 8, 28, 0, 0)
    hours = [make_hour(base + timedelta(hours=i), cloud_cover=10.0) for i in range(24)]
    day = make_day(date="2026-08-28", hours=hours)
    recs = RecommendationEngine().recommend(day)
    moon = next(r for r in recs if r.category == VIEWING_MOON)
    assert moon is not None
    assert moon.score >= 70
    assert moon.level == "强烈推荐"


def test_day_label_attached():
    day = _day_with_clouds(cloud_night=20.0, cloud_morning=20.0)
    recs = RecommendationEngine().recommend(day, day_label="今天")
    assert all(r.day_label == "今天" for r in recs)


def test_merge_dedupes_by_category_keeps_best():
    """合并多天推荐时，同类只保留分数最高的一条。"""
    today = _day_with_clouds(cloud_night=10.0, cloud_morning=10.0)
    tomorrow = _day_with_clouds(cloud_night=80.0, cloud_morning=80.0)
    # 构造：today 晚霞高分，tomorrow 晚霞低分
    today = make_day(hours=[make_hour(datetime(2026, 8, 8) + timedelta(hours=i), cloud_cover=35.0) for i in range(24)])
    tomorrow = make_day(
        date="2026-08-09",
        hours=[make_hour(datetime(2026, 8, 9) + timedelta(hours=i), cloud_cover=90.0) for i in range(24)],
    )
    merged = merge_recommendations([
        RecommendationEngine().recommend(today, day_label="今天"),
        RecommendationEngine().recommend(tomorrow, day_label="明天"),
    ])
    cats = [r.category for r in merged]
    assert len(cats) == len(set(cats)), "合并后不应出现重复类别"
    sunset = next(r for r in merged if r.category == VIEWING_SUNSET)
    assert sunset.day_label == "今天"  # 保留高分的一天
