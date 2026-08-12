"""特殊天文现象播报模块测试。"""
import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

from app.services.astronomy import (
    METEOR_SHOWERS,
    AstronomyService,
    _days_from_peak,
    _in_window,
    _moonlight_factor,
    _parse_kp_token,
    _parse_kyoto_kp,
    _parse_noaa_kp,
    aurora_event,
    aurora_visibility,
    meteor_shower_events,
    special_events,
)
from tests.helpers import make_day, make_hour


def _night_day(cloud_cover, date_str="2026-08-13", year=2026, month=8, day=13):
    """构造某天的小时数据（20 点后与凌晨云量可调）。"""
    base = datetime(year, month, day, 0, 0)
    hours = []
    for i in range(24):
        h = base + timedelta(hours=i)
        cloud = cloud_cover if (h.hour >= 20 or h.hour < 3) else 30.0
        hours.append(make_hour(h, cloud_cover=cloud))
    return make_day(date=date_str, hours=hours)


# ------------------------------------------------------------------ 流星雨

def test_meteor_shower_table_has_major_showers():
    names = [s.name for s in METEOR_SHOWERS]
    assert "英仙座流星雨" in names
    assert "双子座流星雨" in names
    assert "象限仪座流星雨" in names
    assert "猎户座流星雨" in names


def test_days_from_peak_zero_at_peak():
    assert _days_from_peak(date(2026, 8, 13), (8, 13)) == 0
    # 极大次日（target 在极大之后），返回 -1（即已过一天）
    assert _days_from_peak(date(2026, 8, 14), (8, 13)) == -1
    # 极大前一日，返回 +1（极大即将到来）
    assert _days_from_peak(date(2026, 8, 12), (8, 13)) == 1


def test_in_window_cross_year():
    # 象限仪座流星雨：12-28 ~ 01-12（跨年）
    quad = next(s for s in METEOR_SHOWERS if s.name == "象限仪座流星雨")
    assert _in_window(date(2026, 1, 5), quad.start, quad.end)
    assert _in_window(date(2026, 12, 30), quad.start, quad.end)
    assert not _in_window(date(2026, 6, 1), quad.start, quad.end)
    assert not _in_window(date(2026, 2, 1), quad.start, quad.end)


def test_perseids_peak_clear_night_high_score():
    """2026-08-13 英仙座极大夜 + 低云量 + 新月 -> 高观测指数。"""
    events = meteor_shower_events(_night_day(cloud_cover=15.0), date(2026, 8, 13))
    perseids = [e for e in events if e.title == "英仙座流星雨"]
    assert perseids, "极大夜应包含英仙座流星雨"
    assert perseids[0].score >= 70
    assert perseids[0].level in ("高", "中")


def test_cloudy_night_lowers_meteor_score():
    clear = meteor_shower_events(_night_day(cloud_cover=15.0), date(2026, 8, 13))
    cloudy = meteor_shower_events(_night_day(cloud_cover=95.0), date(2026, 8, 13))
    s_clear = max((e.score for e in clear), default=0)
    s_cloudy = max((e.score for e in cloudy), default=0)
    assert s_cloudy < s_clear


def test_outside_active_window_no_meteor_event():
    """2 月中旬没有大型流星雨活跃。"""
    day = _night_day(cloud_cover=15.0, date_str="2026-02-15", year=2026, month=2, day=15)
    events = meteor_shower_events(day, date(2026, 2, 15))
    assert all(e.kind != "meteor_shower" for e in events) or not events


def test_moonlight_factor_brighter_is_worse():
    assert _moonlight_factor(95) < _moonlight_factor(30)
    assert _moonlight_factor(5) == 1.0


# ------------------------------------------------------------------ 极光

def test_aurora_visibility_high_latitude():
    status, _, score = aurora_visibility(5.0, 62.0)
    assert status in ("头顶可见", "天空可见")
    assert score >= 60


def test_aurora_visibility_low_latitude_china():
    """深圳（22°N）即便 Kp=8 也基本无缘极光。"""
    status, _, score = aurora_visibility(8.0, 22.5)
    assert status == "基本无缘"
    assert score <= 15


def test_aurora_higher_kp_higher_score():
    _, _, s2 = aurora_visibility(2.0, 45.0)
    _, _, s8 = aurora_visibility(8.0, 45.0)
    assert s8 > s2


def test_aurora_event_none_kp():
    ev = aurora_event(None, 30.0, _night_day(cloud_cover=20.0), "测试源", "当前")
    assert ev.kind == "aurora"
    assert ev.score == 0
    assert "暂缺" in ev.summary


def test_aurora_event_cloud_reduces_score():
    ev_clear = aurora_event(6.0, 55.0, _night_day(cloud_cover=15.0), "测试源", "未来 3 天")
    ev_cloudy = aurora_event(6.0, 55.0, _night_day(cloud_cover=95.0), "测试源", "未来 3 天")
    assert ev_cloudy.score < ev_clear.score


def test_parse_kp_token():
    assert _parse_kp_token("3+") == 3.33
    assert _parse_kp_token("3-") == 2.67
    assert _parse_kp_token("4") == 4.0
    assert _parse_kp_token("x") is None
    assert _parse_kp_token("") is None


def test_parse_noaa_kp():
    sample = (
        '[["2026-08-12 00:00:00","3.33","2.33","G1"],'
        '["2026-08-12 03:00:00","4.67","3.00","G2"]]'
    )
    kp, when = _parse_noaa_kp(sample)
    assert kp == 4.67
    assert "03:00" in when
    assert _parse_noaa_kp("not json") is None


def test_parse_kyoto_kp():
    sample = """YYYYMMDD Kp[8]
20260810 2 3 2+ 3- 3 2+ 2+ 3- 20+  7 15 9 12 15 9 9 12 11
20260811 3 4 5- 4 3+ 4 5 4- 30- 12 15 27 22 12 18 32 22 24
20260812 2 2 3 2 4 5+ 6- 4 31- 7 7 15 9 27 48 39 22 28
"""
    assert _parse_kyoto_kp(sample) == 5.67  # 最近 3 日峰值：6- 即 5.67
    assert _parse_kyoto_kp("no data") is None


def test_parse_kyoto_kp_concatenated_columns():
    """京都 WDC 定宽列：Kp 值之间可能无空格（如 1+1-2-）。"""
    sample = """20260427 3 2 1-1+1 2-1 1-11+ 15  7  3  5  4  6  4  3  6
20260428 0 1-0 0+1-1-1+0  4-  0  3  0  2
20260430 2 1+1-2-2+3-4 5-19+  7  5  3  6  9 12 27 39 14
"""
    # 最近 3 日峰值：04-30 前 8 个 Kp 为 2,1+,1-,2-,2+,3-,4,5- -> 4.67
    assert _parse_kyoto_kp(sample) == 4.67


# ------------------------------------------------------------------ 日月食 / 超级月亮

def test_special_events_within_horizon():
    events = special_events(date(2026, 8, 12))
    titles = [e.title for e in events]
    assert "月偏食" in titles          # 2026-08-28
    assert "超级月亮" in titles        # 2026-11-24 / 12-24
    assert "日偏食" not in titles      # 2027-08-02 超出 180 天
    assert "半影月食" not in titles    # 2027-02-20 超出 180 天


def test_special_events_score_decreases_with_distance():
    near = special_events(date(2026, 8, 12))
    far = special_events(date(2026, 11, 1))
    near_max = max(e.score for e in near if e.title == "超级月亮")
    far_max = max(e.score for e in far if e.title == "超级月亮")
    # 11-01 时 11-24 超级月亮更近，得分应高于 8-12 时的得分
    assert far_max > near_max


# ------------------------------------------------------------------ 服务入口

def test_astronomy_service_baseline_fallback():
    """无网络（两个 Kp 源均失败）时应优雅降级为估算基线。"""
    svc = AstronomyService()
    svc.fetch_kp_forecast = AsyncMock(return_value=None)
    svc.fetch_kp_now = AsyncMock(return_value=None)
    events = asyncio.run(svc.compute(60.0, [_night_day(cloud_cover=15.0)], date(2026, 8, 13)))
    aurora = [e for e in events if e.kind == "aurora"]
    assert aurora
    assert "估算基线" in aurora[0].source
    assert aurora[0].score > 0


def test_astronomy_service_returns_sorted():
    svc = AstronomyService()
    svc.fetch_kp_forecast = AsyncMock(return_value=(6.0, "2026-08-13 00:00:00"))
    svc.fetch_kp_now = AsyncMock(return_value=4.0)
    events = asyncio.run(svc.compute(55.0, [_night_day(cloud_cover=15.0)], date(2026, 8, 13)))
    scores = [e.score for e in events]
    assert scores == sorted(scores, reverse=True)
    assert any(e.kind == "meteor_shower" for e in events)
    assert any(e.kind == "aurora" for e in events)
    assert any(e.kind in ("eclipse", "supermoon") for e in events)
