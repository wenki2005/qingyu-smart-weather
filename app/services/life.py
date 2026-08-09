"""生活指数服务。

借鉴同类天气推送项目的通用做法（穿衣/带伞/洗车/防晒/运动/晾晒/感冒），
基于 Open-Meteo 已有数据（气温、降水、湿度、风、紫外线）计算每日生活指数，
无需额外数据源。
"""
from __future__ import annotations

from typing import Optional

from ..constants import (
    LIFE_CARWASH,
    LIFE_COLD,
    LIFE_DRESS,
    LIFE_DRYING,
    LIFE_EXERCISE,
    LIFE_SUNSCREEN,
    LIFE_UMBRELLA,
)
from ..models import DailyWeather, LifeIndex

ICONS = {
    LIFE_DRESS: "👕",
    LIFE_UMBRELLA: "☂️",
    LIFE_CARWASH: "🚗",
    LIFE_SUNSCREEN: "🧴",
    LIFE_EXERCISE: "🏃",
    LIFE_DRYING: "🧺",
    LIFE_COLD: "🤧",
}
NAMES = {
    LIFE_DRESS: "穿衣",
    LIFE_UMBRELLA: "带伞",
    LIFE_CARWASH: "洗车",
    LIFE_SUNSCREEN: "防晒",
    LIFE_EXERCISE: "运动",
    LIFE_DRYING: "晾晒",
    LIFE_COLD: "感冒",
}


class LifeIndexService:
    """生活指数计算服务。"""

    def compute(self, daily: DailyWeather, next_day: Optional[DailyWeather] = None) -> list[LifeIndex]:
        """返回今日生活指数列表。"""
        tmax, tmin = daily.temp_max, daily.temp_min
        pop = daily.precip_probability_max
        uv = daily.uv_index_max
        hours = daily.hours

        avg_wind = _avg([h.wind_speed for h in hours])
        max_wind = _max([h.wind_speed for h in hours])
        avg_hum = _avg([h.humidity for h in hours])
        max_precip = _max([h.precipitation for h in hours])
        rain_hours = [h for h in hours if (h.precipitation or 0) > 0.05 or (h.precip_probability or 0) >= 50]

        out = [
            self._dressing(tmin, tmax),
            self._umbrella(pop, rain_hours, max_precip),
            self._carwash(daily, next_day, pop, rain_hours),
            self._sunscreen(uv),
            self._exercise(tmin, tmax, avg_wind, max_wind, rain_hours),
            self._drying(pop, avg_hum, rain_hours),
            self._cold(tmin, tmax),
        ]
        return out

    # ------------------------------------------------------------------ #
    def _dressing(self, tmin, tmax) -> LifeIndex:
        avg = _avg([tmin, tmax])
        if avg is None:
            return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "适中", "气温未知，出门前请再确认天气。")
        if avg >= 30:
            return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "炎热", "短袖、短裤等清凉夏装，注意防晒补水。")
        if avg >= 24:
            return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "热", "薄T恤、短裤即可，早晚可备一件薄外套。")
        if avg >= 18:
            return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "舒适", "长袖衬衫或薄外套，体感舒适。")
        if avg >= 12:
            return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "凉爽", "建议夹克、卫衣等春秋装，注意早晚温差。")
        if avg >= 5:
            return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "冷", "毛衣、厚外套，注意保暖防感冒。")
        return LifeIndex(NAMES[LIFE_DRESS], ICONS[LIFE_DRESS], "寒冷", "羽绒服、棉服等冬装，尽量减少外出。")

    def _umbrella(self, pop, rain_hours, max_precip) -> LifeIndex:
        raining = bool(rain_hours) or (max_precip or 0) > 0.5
        if raining or (pop is not None and pop >= 70):
            return LifeIndex(NAMES[LIFE_UMBRELLA], ICONS[LIFE_UMBRELLA], "必带", "今天有明显降雨，出门请务必带伞。")
        if pop is not None and pop >= 40:
            return LifeIndex(NAMES[LIFE_UMBRELLA], ICONS[LIFE_UMBRELLA], "建议带", "有阵雨可能，包里放把折叠伞更稳妥。")
        if pop is not None and pop >= 20:
            return LifeIndex(NAMES[LIFE_UMBRELLA], ICONS[LIFE_UMBRELLA], "可带", "降水概率不高，视出行时间决定。")
        return LifeIndex(NAMES[LIFE_UMBRELLA], ICONS[LIFE_UMBRELLA], "不用带", "天气晴好，无需带伞。")

    def _carwash(self, daily, next_day, pop, rain_hours) -> LifeIndex:
        rain_today = bool(rain_hours) or (pop is not None and pop >= 40)
        next_rain = False
        if next_day is not None:
            next_rain = (next_day.precip_probability_max or 0) >= 40 or any(
                (h.precipitation or 0) > 0.05 for h in next_day.hours
            )
        if rain_today or next_rain:
            return LifeIndex(NAMES[LIFE_CARWASH], ICONS[LIFE_CARWASH], "不适宜", "近期有降雨，洗车易白费，建议改天。")
        return LifeIndex(NAMES[LIFE_CARWASH], ICONS[LIFE_CARWASH], "适宜", "未来两天无明显降雨，适合洗车。")

    def _sunscreen(self, uv) -> LifeIndex:
        if uv is None:
            return LifeIndex(NAMES[LIFE_SUNSCREEN], ICONS[LIFE_SUNSCREEN], "适中", "紫外线数据缺失，外出可做基础防护。")
        if uv >= 8:
            return LifeIndex(NAMES[LIFE_SUNSCREEN], ICONS[LIFE_SUNSCREEN], "极强", "紫外线极强，务必涂高倍防晒并戴墨镜。")
        if uv >= 5:
            return LifeIndex(NAMES[LIFE_SUNSCREEN], ICONS[LIFE_SUNSCREEN], "较强", "紫外线较强，外出涂抹防晒霜。")
        if uv >= 3:
            return LifeIndex(NAMES[LIFE_SUNSCREEN], ICONS[LIFE_SUNSCREEN], "中等", "适当防晒，正午时段注意遮阳。")
        return LifeIndex(NAMES[LIFE_SUNSCREEN], ICONS[LIFE_SUNSCREEN], "较弱", "紫外线较弱，无需特别防护。")

    def _exercise(self, tmin, tmax, avg_wind, max_wind, rain_hours) -> LifeIndex:
        avg = _avg([tmin, tmax])
        if rain_hours:
            return LifeIndex(NAMES[LIFE_EXERCISE], ICONS[LIFE_EXERCISE], "不宜", "有降雨，建议改在室内锻炼。")
        if avg is None:
            return LifeIndex(NAMES[LIFE_EXERCISE], ICONS[LIFE_EXERCISE], "一般", "天气数据不足，视自身情况安排运动。")
        if max_wind is not None and max_wind >= 10.8:
            return LifeIndex(NAMES[LIFE_EXERCISE], ICONS[LIFE_EXERCISE], "不宜", "风力较大，户外运动请注意安全。")
        if avg >= 33 or avg <= 2:
            return LifeIndex(NAMES[LIFE_EXERCISE], ICONS[LIFE_EXERCISE], "一般", "气温偏极端，建议减少剧烈户外运动。")
        if 18 <= avg <= 26:
            return LifeIndex(NAMES[LIFE_EXERCISE], ICONS[LIFE_EXERCISE], "适宜", "气温舒适，正是户外晨跑/骑行好时机。")
        return LifeIndex(NAMES[LIFE_EXERCISE], ICONS[LIFE_EXERCISE], "较适宜", "天气总体适合运动，注意补水与保暖。")

    def _drying(self, pop, avg_hum, rain_hours) -> LifeIndex:
        if rain_hours or (pop is not None and pop >= 50):
            return LifeIndex(NAMES[LIFE_DRYING], ICONS[LIFE_DRYING], "不适宜", "有降雨，晾晒衣物易被淋湿，建议烘干。")
        if avg_hum is not None and avg_hum >= 80:
            return LifeIndex(NAMES[LIFE_DRYING], ICONS[LIFE_DRYING], "较差", "空气湿度大，衣物干得慢，可放通风处。")
        return LifeIndex(NAMES[LIFE_DRYING], ICONS[LIFE_DRYING], "适宜", "天气干燥少雨，适合晾晒被褥衣物。")

    def _cold(self, tmin, tmax) -> LifeIndex:
        if tmin is None or tmax is None:
            return LifeIndex(NAMES[LIFE_COLD], ICONS[LIFE_COLD], "一般", "气温数据不足，注意根据体感增减衣物。")
        swing = tmax - tmin
        if swing >= 12 or tmax <= 8:
            return LifeIndex(NAMES[LIFE_COLD], ICONS[LIFE_COLD], "易发", "昼夜温差大/天气寒冷，注意添衣预防感冒。")
        if swing >= 8:
            return LifeIndex(NAMES[LIFE_COLD], ICONS[LIFE_COLD], "较易发", "温差较大，体弱者注意及时增减衣物。")
        return LifeIndex(NAMES[LIFE_COLD], ICONS[LIFE_COLD], "少发", "天气平稳，感冒风险较低，保持规律作息。")


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _max(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return max(clean) if clean else None
