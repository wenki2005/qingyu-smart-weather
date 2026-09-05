"""天气观赏推荐引擎。

根据未来 24h 的小时级天气，为不同自然景观（日出、日落、星空、彩虹、
云景、雪景、赏月）计算观赏指数，并在每日推送中给出「今日最佳观赏」建议。
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from ..constants import (
    VIEWING_CLOUD,
    VIEWING_MOON,
    VIEWING_RAINBOW,
    VIEWING_SNOW,
    VIEWING_STAR,
    VIEWING_SUNRISE,
    VIEWING_SUNSET,
)
from ..models import DailyWeather, ViewingRecommendation
from .moon import compute_moon_viewing
from .sunset import compute_sunset_probability
from .weather import hour_window

# 等级阈值
STRONG = 75
NORMAL = 50


class RecommendationEngine:
    """观赏推荐引擎。"""

    def recommend(
        self, daily: DailyWeather, day_label: str = "", air_quality=None, latitude=None
    ) -> list[ViewingRecommendation]:
        """对某一天生成全部观赏推荐，按分数降序。"""
        out: list[ViewingRecommendation] = []
        out.append(self._sunrise(daily))
        out.append(self._sunset(daily, air_quality=air_quality, latitude=latitude))
        out.append(self._stargazing(daily))
        out.append(self._rainbow(daily))
        out.append(self._cloud_scene(daily))
        out.append(self._snow(daily))
        out.append(self._moon(daily))
        out = [r for r in out if r is not None]
        for r in out:
            r.day_label = day_label
        out.sort(key=lambda r: r.score, reverse=True)
        return out

    # ------------------------------------------------------------------ #
    def _sunrise(self, daily: DailyWeather) -> Optional[ViewingRecommendation]:
        sunrise = daily.sunrise
        if not sunrise:
            return None
        try:
            sunrise_dt = datetime.fromisoformat(sunrise)
        except ValueError:
            return None
        window = hour_window(daily.hours, sunrise_dt, delta_minutes=60)
        if not window:
            return None
        avg_cloud = _avg([h.cloud_cover for h in window])
        precip_prob = _avg([h.precip_probability for h in window])
        cloud_pts = 100.0 * math.exp(-0.5 * ((avg_cloud - 25.0) / 30.0) ** 2) if avg_cloud is not None else 50.0
        precip_pts = max(0.0, 100.0 - (precip_prob or 0.0) * 1.0)
        score = round(cloud_pts * 0.7 + precip_pts * 0.3)
        return ViewingRecommendation(
            category=VIEWING_SUNRISE,
            title="朝霞 / 日出",
            score=score,
            level=_level(score),
            summary=(
                f"日出 {sunrise[-5:]}，云量 {avg_cloud:.0f}%" if avg_cloud is not None else f"日出 {sunrise[-5:]}"
            ),
            best_time=sunrise[-5:],
            tips="提前 20 分钟到达，朝霞常在日出前一刻最绚烂。",
        )

    def _sunset(
        self, daily: DailyWeather, air_quality=None, latitude=None
    ) -> Optional[ViewingRecommendation]:
        info = compute_sunset_probability(
            daily, daily.sunset, daily.hours, air_quality=air_quality, latitude=latitude
        )
        return ViewingRecommendation(
            category=VIEWING_SUNSET,
            title="晚霞 / 日落",
            score=round(info.probability),
            level=_level(info.probability),
            summary=info.reason,
            best_time=info.sunset_time[-5:] if info.sunset_time else None,
            tips=info.tips,
        )

    def _stargazing(self, daily: DailyWeather) -> Optional[ViewingRecommendation]:
        if not daily.hours:
            return None
        # 晚间 20:00-24:00（若含次日凌晨 0-3 点的小时数据一并纳入）
        night = [h for h in daily.hours if h.time.hour >= 20 or h.time.hour < 3]
        if not night:
            return None
        avg_cloud = _avg([h.cloud_cover for h in night])
        precip = _avg([h.precip_probability for h in night])
        if avg_cloud is not None and avg_cloud >= 60:
            return ViewingRecommendation(
                category=VIEWING_STAR,
                title="星空观测",
                score=15,
                level="一般",
                summary="夜间云量过多，基本无缘星空。",
                tips="改日再约，或关注实时云图。",
            )
        cloud_pts = 100.0 * math.exp(-0.5 * (avg_cloud / 25.0) ** 2) if avg_cloud is not None else 60.0
        precip_pts = max(0.0, 100.0 - (precip or 0.0) * 1.2)
        score = round(cloud_pts * 0.8 + precip_pts * 0.2)
        return ViewingRecommendation(
            category=VIEWING_STAR,
            title="星空观测",
            score=score,
            level=_level(score),
            summary=f"夜间平均云量 {avg_cloud:.0f}%" if avg_cloud is not None else "夜间云量适中",
            best_time="20:00 - 次日 03:00",
            tips="远离城市光污染，后半夜月亮落下后效果更佳。",
        )

    def _rainbow(self, daily: DailyWeather) -> Optional[ViewingRecommendation]:
        rain = [h for h in daily.hours if h.weather_code in (61, 63, 65, 80, 81, 82, 95)]
        if not rain:
            return None
        # 彩虹需要"雨 + 阳光"，考察降雨时段附近是否有多云/晴空
        sunny_nearby = False
        for h in rain:
            idx = daily.hours.index(h)
            around = daily.hours[max(0, idx - 2): idx + 3]
            if any(
                x.weather_code in (0, 1, 2, 3) and (x.cloud_cover or 0) < 70
                for x in around
            ):
                sunny_nearby = True
                break
        score = 70 if sunny_nearby else 35
        return ViewingRecommendation(
            category=VIEWING_RAINBOW,
            title="彩虹观测",
            score=score,
            level=_level(score),
            summary="降雨间歇伴有阳光，有彩虹机会" if sunny_nearby else "有阵雨但阳光条件一般",
            tips="雨后向西看（背对太阳），双层彩虹概率更高。",
        )

    def _cloud_scene(self, daily: DailyWeather) -> Optional[ViewingRecommendation]:
        """云海 / 流云景观：高湿度 + 较高云量 + 无强降水。"""
        avg_cloud = _avg([h.cloud_cover for h in daily.hours])
        avg_hum = _avg([h.humidity for h in daily.hours])
        max_precip = _max([h.precipitation for h in daily.hours])
        if avg_cloud is None or avg_hum is None:
            return None
        if avg_cloud < 45:
            return None
        score = 0
        score += min(50.0, avg_cloud * 0.5)
        score += min(35.0, max(0.0, (avg_hum - 55.0)) * 0.9)
        if max_precip is None or max_precip < 0.1:
            score += 15
        score = round(min(100.0, score))
        if score < NORMAL:
            return None
        return ViewingRecommendation(
            category=VIEWING_CLOUD,
            title="云海 / 流云",
            score=score,
            level=_level(score),
            summary=f"平均湿度 {avg_hum:.0f}%、云量 {avg_cloud:.0f}%",
            tips="山顶或高层视野，湿度高时更容易出现云海。",
        )

    def _snow(self, daily: DailyWeather) -> Optional[ViewingRecommendation]:
        snow = [h for h in daily.hours if h.weather_code in (71, 73, 75, 77, 85, 86)]
        if not snow:
            return None
        amount = _max([h.precipitation for h in snow]) or 0.0
        score = round(min(100.0, 60 + amount * 8))
        return ViewingRecommendation(
            category=VIEWING_SNOW,
            title="雪景",
            score=score,
            level=_level(score),
            summary=f"预计降雪，累计雪量约 {amount:.1f}mm",
            tips="初雪后 2-4 小时是拍照黄金期，记得防滑保暖。",
        )

    def _moon(self, daily: DailyWeather) -> Optional[ViewingRecommendation]:
        """赏月指南：月相照明度 + 夜间云量 -> 赏月指数。"""
        info = compute_moon_viewing(daily)
        if info.illuminated < 8.0:
            return ViewingRecommendation(
                category=VIEWING_MOON,
                title="赏月",
                score=round(min(info.score, 20.0)),
                level="一般",
                summary=f"今晚月相为「{info.phase}」，照明度仅 {info.illuminated:.0f}%，基本无缘赏月",
                best_time=info.best_time,
                tips="月缺之夜，更适合观星；关注明晚月相。",
            )
        return ViewingRecommendation(
            category=VIEWING_MOON,
            title="赏月",
            score=round(info.score),
            level=_level(info.score),
            summary=info.reason,
            best_time=info.best_time,
            tips=info.tips,
        )


def merge_recommendations(groups: list[list[ViewingRecommendation]]) -> list[ViewingRecommendation]:
    """合并多天推荐，按类别去重：同类别仅保留分数最高的一条。

    避免「今天/明天」同类观赏项目（晚霞、朝霞、星空等）在推送/界面中重复出现。
    """
    best: dict[str, ViewingRecommendation] = {}
    for group in groups:
        for r in group:
            prev = best.get(r.category)
            if prev is None or r.score > prev.score:
                best[r.category] = r
    return sorted(best.values(), key=lambda r: r.score, reverse=True)


def _level(score: float) -> str:
    if score >= STRONG:
        return "强烈推荐"
    if score >= NORMAL:
        return "推荐"
    return "一般"


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None


def _max(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return max(clean) if clean else None
