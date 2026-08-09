"""晚霞观赏概率计算。

原理：晚霞是阳光被低层大气中的云层/气溶胶反射散射形成的，
最佳条件 = 太阳落山前后天空有「适量」中高层云（约 20%-60% 云量）。
云太少 -> 没有云反射，天空乏味；云太多 -> 阳光被遮挡，晚霞不亮。
再叠加降水概率、能见度、湿度等修正因子。

评分维度（满分 100）：
- 云量得分（核心，权重 0.45）：落日前 1h 至日落后 30min 窗口内，云量越接近 35% 越高
- 降水抑制（权重 0.35）：窗口内降水概率 / 降水量为 0 最佳
- 能见度（权重 0.10）：能见度 >= 15km 最佳（通透）
- 湿度（权重 0.10）：中低湿度更有助于色彩饱和度
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import DailyWeather, HourlyWeather, SunsetInfo
from .weather import hour_window

# 理想云量（百分比），高斯曲线的均值与带宽
IDEAL_CLOUD = 35.0
CLOUD_BAND = 22.0

# 云量超过该值视为遮挡夕阳，进入线性惩罚
CLOUD_HARD_LIMIT = 70.0

# 观赏等级阈值
LEVEL_HIGH = 75
LEVEL_MEDIUM = 50


def cloud_score(cloud_cover: Optional[float]) -> float:
    """云量 -> [0,100] 得分，35% 附近最高。

    - 云量 <= 70%：高斯曲线，越接近 35% 分越高（有云才反射晚霞）
    - 云量 > 70%：线性衰减，云越多越遮挡阳光，得分趋近 0
    """
    import math

    if cloud_cover is None:
        return 55.0
    x = max(0.0, min(100.0, cloud_cover))
    peak = 100.0 * math.exp(-0.5 * ((CLOUD_HARD_LIMIT - IDEAL_CLOUD) / CLOUD_BAND) ** 2)
    if x <= CLOUD_HARD_LIMIT:
        return 100.0 * math.exp(-0.5 * ((x - IDEAL_CLOUD) / CLOUD_BAND) ** 2)
    return peak * max(0.0, (100.0 - x) / (100.0 - CLOUD_HARD_LIMIT))


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


def compute_sunset_probability(
    daily: DailyWeather,
    sunset_str: Optional[str],
    hours: list[HourlyWeather],
) -> SunsetInfo:
    """计算某一天晚霞观赏概率。"""
    if not sunset_str:
        return SunsetInfo(probability=0.0, level="低", reason="缺少日落时间数据")

    try:
        sunset_dt = datetime.fromisoformat(sunset_str)
    except ValueError:
        return SunsetInfo(probability=0.0, level="低", reason="日落时间解析失败")

    window = hour_window(hours, sunset_dt, delta_minutes=90)
    if not window:
        # 窗口落在预报范围外，退化为当日整体云量估计
        window = hours

    avg_cloud = _avg([h.cloud_cover for h in window])
    avg_precip_prob = _avg([h.precip_probability for h in window])
    max_precip = _max([h.precipitation for h in window])
    avg_vis = _avg([h.visibility for h in window])
    avg_humidity = _avg([h.humidity for h in window])

    # 综合得分（权重：云量 > 降水 > 能见度 > 湿度）
    score = (
        cloud_score(avg_cloud) * 0.45
        + precip_penalty(avg_precip_prob, max_precip) * 0.35
        + visibility_score(avg_vis) * 0.10
        + humidity_score(avg_humidity) * 0.10
    )
    score = round(max(0.0, min(100.0, score)), 1)

    # 硬性抑制规则
    code = daily.weather_code
    if code in (95, 96, 99, 45, 48):
        score = min(score, 25.0)
        reason = "雷暴或大雾天气，晚霞观赏条件差"
    elif avg_cloud is not None and avg_cloud >= 85:
        score = min(score, 30.0)
        reason = "云量过多，夕阳基本被遮挡"
    elif avg_precip_prob is not None and avg_precip_prob >= 70:
        score = min(score, 35.0)
        reason = "降水概率很高，晚霞基本无缘"
    else:
        reason = _build_reason(avg_cloud, avg_precip_prob)

    level = _level(score)
    tips = _tips(level, sunset_str)
    return SunsetInfo(
        sunset_time=sunset_str,
        probability=score,
        level=level,
        reason=reason,
        cloud_cover_at_sunset=avg_cloud,
        tips=tips,
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


def _avg(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _max(vals: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return max(clean) if clean else None
