"""天气新闻播报稿测试。"""

from app.models import AirQuality, LifeIndex, MoonInfo, SunsetInfo, WeatherReport, WeatherWarning
from app.services.news import build_news_bulletin
from tests.helpers import make_day


def _report():
    return WeatherReport(
        location="北京",
        generated_at="2026-08-08T06:00:00",
        daily=[make_day()],
        sunset=SunsetInfo(sunset_time="2026-08-08T19:20", probability=82.0, level="高", reason="云量适中"),
        moon=MoonInfo(date="2026-08-08", phase="满月", illuminated=98.0, score=88.0, level="高",
                      best_time="20:00 - 23:00", reason="满月之夜"),
        warnings=[WeatherWarning(title="高温提醒", level="橙色")],
        air_quality=AirQuality(aqi=42.0, level="优", pm25=12.0),
        life_indices=[LifeIndex(name="穿衣", icon="👕", level="舒适", advice="长袖衬衫")],
    )


def test_bulletin_contains_city_and_weather():
    text = build_news_bulletin(_report())
    assert "北京" in text
    assert "天气" in text
    assert "晴" in text


def test_bulletin_mentions_moon_and_warning():
    text = build_news_bulletin(_report())
    assert "满月" in text
    assert "高温提醒" in text


def test_bulletin_has_broadcast_tone():
    text = build_news_bulletin(_report())
    assert "观众朋友们" in text
    assert "感谢收听" in text
