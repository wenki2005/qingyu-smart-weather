"""晚霞观赏概率计算。

原理：晚霞是阳光被低层大气中的云层/气溶胶反射散射形成的，
最佳条件 = 太阳落山前后天空有「适量」中高层云（约 20%-60% 云量）。
云太少 -> 没有云反射，天空乏味；云太多 -> 阳光被遮挡，晚霞不亮。
再叠加降水概率、能见度、湿度等修正因子。

评分维度（满分 100）：
- 中高云结构（权重 0.40）：日落窗口内中云/高云更利于反射，低云过多会遮挡地平线
- 降水条件（权重 0.25）：窗口内降水概率/降水量越低越好
- 能见度（权重 0.15）：能见度 >= 15km 最佳
- 湿度（权重 0.10）与 PM2.5 通透度（权重 0.10）
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from ..models import AirQuality, DailyWeather, HourlyWeather, SunsetInfo
from .weather import hour_window

# 理想云量（百分比），高斯曲线的均值与带宽
IDEAL_CLOUD = 35.0
CLOUD_BAND = 22.0

# 云量超过该值视为遮挡夕阳，进入线性惩罚
CLOUD_HARD_LIMIT = 70.0

# 观赏等级阈值
LEVEL_HIGH = 75
LEVEL_MEDIUM = 50

# 摄影常用太阳高度角定义：金色时刻 -4°~+6°，蓝调时刻 -6°~-4°。
GOLDEN_START_DEG = -4.0
GOLDEN_END_DEG = 6.0
BLUE_START_DEG = -6.0
BLUE_END_DEG = -4.0


def cloud_score(cloud_cover: Optional[float]) -> float:
    """总云量 -> [0,100] 得分，作为没有分层云量时的兼容回退。

    """
    if cloud_cover is None:
        return 55.0
    x = max(0.0, min(100.0, cloud_cover))
    peak = 100.0 * math.exp(-0.5 * ((CLOUD_HARD_LIMIT - IDEAL_CLOUD) / CLOUD_BAND) ** 2)
    if x <= CLOUD_HARD_LIMIT:
        return 100.0 * math.exp(-0.5 * ((x - IDEAL_CLOUD) / CLOUD_BAND) ** 2)
    return peak * max(0.0, (100.0 - x) / (100.0 - CLOUD_HARD_LIMIT))


def cloud_structure_score(hours: list[HourlyWeather], target: Optional[datetime] = None) -> float:
    """按中高云层结构评分，低云过多会遮挡西方地平线。"""
    layered = [h for h in hours if h.cloud_cover_mid is not None or h.cloud_cover_high is not None]
    if not layered:
        return cloud_score(_weighted_average(hours, "cloud_cover", target))
    mid = _weighted_average(layered, "cloud_cover_mid", target)
    high = _weighted_average(layered, "cloud_cover_high", target)
    low = _weighted_average(layered, "cloud_cover_low", target)
    upper = 0.58 * (mid if mid is not None else 35.0) + 0.42 * (high if high is not None else 35.0)
    upper_score = 100.0 * math.exp(-0.5 * ((upper - 48.0) / 30.0) ** 2)
    low_factor = 1.0 if low is None or low <= 45.0 else max(0.12, 1.0 - (low - 45.0) / 70.0)
    return max(0.0, min(100.0, upper_score * low_factor))


def air_quality_score(air_quality: Optional[AirQuality]) -> float:
    """PM2.5 对天空通透度的修正分。"""
    if air_quality is None or air_quality.pm25 is None:
        return 70.0
    pm25 = max(0.0, air_quality.pm25)
    if pm25 <= 35.0:
        return 100.0
    if pm25 <= 75.0:
        return 100.0 - (pm25 - 35.0) * 0.8
    if pm25 <= 150.0:
        return 68.0 - (pm25 - 75.0) * 0.45
    return max(20.0, 34.0 - (pm25 - 150.0) * 0.12)


def precip_penalty(prob: Optional[float], amount: Optional[float]) -> float:
    """降水对观赏的抑制得分 [0,100]，100 = 完全无降水。

    降水概率 >= 70% 时视为「晚霞基本没戏」，得分快速归零。
    """
    if amount is not None and amount > 0.05:
        return max(0.0, 100.0 - amount * 15.0)
    if prob is None:
        return 80.0
    if prob >= 70:
        return max(0.0, 30.0 - (prob - 70.0) * 1.0)
    return max(0.0, 100.0 - prob * 1.1)


def visibility_score(visibility_m: Optional[float]) -> float:
    """能见度得分 [0,100]。15km 以上给满分。"""
    if visibility_m is None:
        return 70.0
    km = visibility_m / 1000.0
    return min(100.0, max(0.0, km / 15.0 * 100.0))


def humidity_score(humidity: Optional[float]) -> float:
    """湿度得分 [0,100]：40-65% 最佳。"""
    if humidity is None:
        return 75.0
    if humidity <= 40:
        return 70.0
    if 40 < humidity <= 65:
        return 100.0
    # >65%：湿度越大越容易浑浊
    return max(40.0, 100.0 - (humidity - 65.0) * 1.2)


def twilight_times(
    sunrise_str: Optional[str],
    sunset_str: Optional[str],
    latitude: Optional[float] = None,
) -> dict[str, Optional[str]]:
    """计算当地金色/蓝调时刻，返回四个“HH:MM-HH:MM”区间。

    采用 NOAA 常用太阳高度角公式，以接口提供的日出/日落校准当地太阳时，
    不依赖额外 API。纬度缺失时用日出/日落前后经验区间兜底。
    """
    if not sunrise_str or not sunset_str:
        return {key: None for key in ("golden_morning", "golden_evening", "blue_morning", "blue_evening")}
    try:
        sunrise = datetime.fromisoformat(sunrise_str)
        sunset = datetime.fromisoformat(sunset_str)
    except ValueError:
        return {key: None for key in ("golden_morning", "golden_evening", "blue_morning", "blue_evening")}
    if latitude is None:
        return {
            "golden_morning": _range(sunrise - timedelta(minutes=30), sunrise + timedelta(minutes=30)),
            "golden_evening": _range(sunset - timedelta(minutes=30), sunset + timedelta(minutes=30)),
            "blue_morning": _range(sunrise - timedelta(minutes=45), sunrise - timedelta(minutes=30)),
            "blue_evening": _range(sunset + timedelta(minutes=30), sunset + timedelta(minutes=45)),
        }
    try:
        day_of_year = sunrise.timetuple().tm_yday
        decl = math.radians(23.44 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0)))
        lat = math.radians(float(latitude))
        cos_lat_dec = math.cos(lat) * math.cos(decl)
        if abs(cos_lat_dec) < 1e-8:
            raise ValueError("极地太阳几何不可用")
        solar_noon = sunrise + (sunset - sunrise) / 2

        def crossing(angle: float, morning: bool) -> datetime:
            altitude = math.radians(angle)
            value = (math.sin(altitude) - math.sin(lat) * math.sin(decl)) / cos_lat_dec
            if not -1.0 <= value <= 1.0:
                raise ValueError("太阳高度角无交点")
            hour_angle = math.degrees(math.acos(value)) / 15.0
            return solar_noon + timedelta(hours=-hour_angle if morning else hour_angle)

        # 理论太阳高度角先计算，再用数据源的日出/日落作为锚点校准，
        # 避免太阳视半径和地平线折射定义差异造成整体偏移。
        reference = -0.833
        raw_morning_ref = crossing(reference, True)
        raw_evening_ref = crossing(reference, False)

        def calibrate(raw: datetime, ref: datetime, anchor: datetime) -> datetime:
            return anchor + (raw - ref)

        gm_start = calibrate(crossing(GOLDEN_START_DEG, True), raw_morning_ref, sunrise)
        gm_end = calibrate(crossing(GOLDEN_END_DEG, True), raw_morning_ref, sunrise)
        ge_start = calibrate(crossing(GOLDEN_END_DEG, False), raw_evening_ref, sunset)
        ge_end = calibrate(crossing(GOLDEN_START_DEG, False), raw_evening_ref, sunset)
        bm_start = calibrate(crossing(BLUE_START_DEG, True), raw_morning_ref, sunrise)
        bm_end = calibrate(crossing(BLUE_END_DEG, True), raw_morning_ref, sunrise)
        be_start = calibrate(crossing(BLUE_END_DEG, False), raw_evening_ref, sunset)
        be_end = calibrate(crossing(BLUE_START_DEG, False), raw_evening_ref, sunset)
        return {
            "golden_morning": _range(gm_start, gm_end),
            "golden_evening": _range(ge_start, ge_end),
            "blue_morning": _range(bm_start, bm_end),
            "blue_evening": _range(be_start, be_end),
        }
    except (TypeError, ValueError, OverflowError):
        return {key: None for key in ("golden_morning", "golden_evening", "blue_morning", "blue_evening")}


def _range(start: datetime, end: datetime) -> str:
    return f"{start:%H:%M}-{end:%H:%M}"


def compute_sunset_probability(
    daily: DailyWeather,
    sunset_str: Optional[str],
    hours: list[HourlyWeather],
    air_quality: Optional[AirQuality] = None,
    latitude: Optional[float] = None,
) -> SunsetInfo:
    """计算某一天晚霞观赏概率。"""
    if not sunset_str:
        return SunsetInfo(probability=0.0, level="低", reason="缺少日落时间数据")

    try:
        sunset_dt = datetime.fromisoformat(sunset_str)
    except ValueError:
        return SunsetInfo(probability=0.0, level="低", reason="日落时间解析失败")

    # 晚霞主要集中在日落前后约 45 分钟，避免 ±90 分钟平均稀释关键时段。
    window = hour_window(hours, sunset_dt, delta_minutes=45)
    if not window:
        # 若预报没有覆盖日落，退化为距离日落最近的最多 3 个小时点。
        window = sorted(hours, key=lambda h: abs((h.time - sunset_dt).total_seconds()))[:3]

    avg_cloud = _weighted_average(window, "cloud_cover", sunset_dt)
    avg_precip_prob = _weighted_average(window, "precip_probability", sunset_dt)
    max_precip = _max([h.precipitation for h in window])
    avg_vis = _weighted_average(window, "visibility", sunset_dt)
    avg_humidity = _weighted_average(window, "humidity", sunset_dt)
    structure = cloud_structure_score(window, sunset_dt)
    pollution = air_quality_score(air_quality)

    # 中高云结构是核心，降水/能见度/污染共同决定实际可见度。
    score = (
        structure * 0.40
        + precip_penalty(avg_precip_prob, max_precip) * 0.25
        + visibility_score(avg_vis) * 0.15
        + humidity_score(avg_humidity) * 0.10
        + pollution * 0.10
    )
    score = round(max(0.0, min(100.0, score)), 1)

    # 只依据日落窗口内的天气码抑制，不能让早上的雷暴误伤晚霞。
    window_codes = {h.weather_code for h in window if h.weather_code is not None}
    if window_codes & {95, 96, 99, 45, 48}:
        score = min(score, 25.0)
        reason = "日落前后有雷暴或大雾，晚霞观赏条件差"
    elif avg_cloud is not None and avg_cloud >= 85:
        score = min(score, 30.0)
        reason = "日落前后云量过多，夕阳基本被遮挡"
    elif avg_precip_prob is not None and avg_precip_prob >= 70:
        score = min(score, 35.0)
        reason = "日落前后降水概率很高，晚霞基本无缘"
    elif air_quality is not None and air_quality.pm25 is not None and air_quality.pm25 >= 150:
        reason = "空气颗粒物偏高，天空通透度受影响"
    else:
        reason = _build_reason(avg_cloud, avg_precip_prob)

    level = _level(score)
    tips = _tips(level, sunset_str)
    twilight = twilight_times(daily.sunrise, sunset_str, latitude)
    return SunsetInfo(
        sunset_time=sunset_str,
        probability=score,
        level=level,
        reason=reason,
        cloud_cover_at_sunset=avg_cloud,
        tips=tips,
        golden_morning=twilight["golden_morning"],
        golden_evening=twilight["golden_evening"],
        blue_morning=twilight["blue_morning"],
        blue_evening=twilight["blue_evening"],
    )


def _build_reason(cloud, precip_prob) -> str:
    parts = []
    if cloud is None:
        parts.append("云量数据缺失")
    elif cloud < 15:
        parts.append("云量偏少，晚霞可能较平淡")
    elif cloud <= 60:
        parts.append("云量适中，适合观赏")
    else:
        parts.append("云量偏多，可能遮挡夕阳")
    if precip_prob is not None and precip_prob >= 40:
        parts.append(f"降水概率 {precip_prob:.0f}%")
    return "；".join(parts)


def _tips(level: str, sunset_str: Optional[str]) -> str:
    time_part = f"日落时间 {sunset_str}" if sunset_str else ""
    if level == "高":
        return f"{time_part}，记得提前到视野开阔的西侧高处，带上相机！" if time_part else "视野开阔的西侧高处是最佳观赏点"
    if level == "中":
        return f"{time_part}，可碰碰运气，选择西向高层或江边观景台。" if time_part else "选择西向高层或江边观景台"
    return "今日晚霞条件一般，更适合安排其他活动。"


def _level(score: float) -> str:
    if score >= LEVEL_HIGH:
        return "高"
    if score >= LEVEL_MEDIUM:
        return "中"
    return "低"


def _weighted_average(
    hours: list[HourlyWeather], attr: str, target: Optional[datetime] = None
) -> Optional[float]:
    """按距目标时刻加权，离日落越近权重越高。"""
    values = [
        (getattr(h, attr), 1.0 / (1.0 + abs((h.time - target).total_seconds()) / 3600.0))
        if target is not None else (getattr(h, attr), 1.0)
        for h in hours
    ]
    clean = [(float(value), weight) for value, weight in values if value is not None]
    if not clean:
        return None
    return sum(value * weight for value, weight in clean) / sum(weight for _, weight in clean)


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _max(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return max(clean) if clean else None
