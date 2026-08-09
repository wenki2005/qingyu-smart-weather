"""赏月指南服务。

基于月相（近似天文算法）+ 夜间云量 / 降水概率，计算当晚的「赏月指数」，
并给出最佳赏月时段与小贴士。月相算法为业界常用的 synodic 近似法
（以 2000-01-06 18:14 UTC 新月为基准，朔望月周期 29.53058867 天），
无需外部 API，误差通常在 1-2 天以内，足够日常赏月参考。
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional

from ..models import DailyWeather, HourlyWeather, MoonInfo

SYNODIC_MONTH = 29.53058867          # 朔望月（天）
KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14)   # 2000-01-06 18:14 UTC 新月

# 月相名称边界（按月龄天数，以满月 14.77 为中心对称）
PHASE_NAMES = (
    (1.8, "娥眉月"),
    (5.5, "上弦月"),
    (9.2, "盈凸月"),
    (12.7, "满月"),
    (16.1, "亏凸月"),
    (19.5, "下弦月"),
    (23.2, "残月"),
    (26.6, "新月"),
)

LEVEL_HIGH = 75
LEVEL_MEDIUM = 50


def moon_age(target: datetime) -> float:
    """返回目标时刻的月龄（自最近一次新月起的天数，0-29.53）。"""
    delta = target - KNOWN_NEW_MOON
    return delta.total_seconds() / 86400.0 % SYNODIC_MONTH


def moon_phase(target: date) -> tuple[str, float]:
    """返回 (月相中文名, 照明百分比 0-100)。

    简单模型存在 ±1 天漂移，故以照明度做最终矫正：
    照明度 >=97% 一律显示「满月」，<=2% 一律显示「新月」，避免误导。
    """
    age = moon_age(datetime(target.year, target.month, target.day, 12, 0))
    illuminated = (1.0 - math.cos(2.0 * math.pi * age / SYNODIC_MONTH)) / 2.0
    name = "新月"
    for boundary, label in PHASE_NAMES:
        if age < boundary:
            name = label
            break
    if illuminated >= 0.97:
        name = "满月"
    elif illuminated <= 0.02:
        name = "新月"
    return name, round(illuminated * 100.0, 1)


def moonrise_estimate(target: date) -> Optional[str]:
    """约估当地月出时间（HH:MM）。

    近似：新月约在 06:00 升起，每过一天约推迟 50 分钟，
    满月约在 18:00 升起，基本与日落同步。仅供赏月时段参考。
    """
    try:
        age = moon_age(datetime(target.year, target.month, target.day, 12, 0))
    except Exception:
        return None
    minutes = int(360 + age * (24 * 60 / SYNODIC_MONTH))  # 新月 06:00 起算
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _night_hours(daily: DailyWeather, extra_hours: Optional[list[HourlyWeather]] = None) -> list[HourlyWeather]:
    """取当晚 20:00-24:00 与次日凌晨 00:00-03:00 的小时数据。"""
    night = [h for h in daily.hours if h.time.hour >= 20]
    for h in extra_hours or []:
        if h.time.hour < 3:
            night.append(h)
    return night


def compute_moon_viewing(
    daily: DailyWeather,
    extra_hours: Optional[list[HourlyWeather]] = None,
) -> MoonInfo:
    """计算某天晚上的赏月指数。

    daily：目标日期；extra_hours：次日的凌晨小时（覆盖今晚后半夜）。
    """
    try:
        day = date.fromisoformat(daily.date)
    except ValueError:
        day = date.today()
    phase, illum = moon_phase(day)

    night = _night_hours(daily, extra_hours)
    if not night:
        night = daily.hours  # 退化：用全天数据估计

    avg_cloud = _avg([h.cloud_cover for h in night])
    avg_precip = _avg([h.precip_probability for h in night])

    # 照明度得分：满月（≥95% 照明）给满分，新月（<8%）基本无缘赏月
    illum_pts = max(0.0, (illum - 8.0) / 92.0 * 100.0)
    # 云量得分：云越少越好
    cloud_pts = max(0.0, 100.0 - (avg_cloud or 0.0) * 1.0) if avg_cloud is not None else 60.0
    # 降水抑制
    precip_pts = max(0.0, 100.0 - (avg_precip or 0.0) * 1.1) if avg_precip is not None else 80.0

    score = round(illum_pts * 0.55 + cloud_pts * 0.35 + precip_pts * 0.10, 1)
    score = round(max(0.0, min(100.0, score)), 1)
    if illum < 8.0:
        score = min(score, 20.0)
    elif avg_cloud is not None and avg_cloud >= 85:
        score = min(score, 25.0)
    elif avg_precip is not None and avg_precip >= 70:
        score = min(score, 30.0)

    level = _level(score)
    moonrise = moonrise_estimate(day)
    best_time = _best_time(moonrise, illum)
    reason = _build_reason(phase, illum, avg_cloud, avg_precip)
    tips = _tips(level, phase, best_time)
    return MoonInfo(
        date=daily.date,
        phase=phase,
        illuminated=illum,
        score=score,
        level=level,
        reason=reason,
        best_time=best_time,
        cloud_cover=avg_cloud,
        moonrise=moonrise,
        tips=tips,
    )


def _best_time(moonrise: Optional[str], illum: float) -> str:
    """最佳赏月时段（仅考虑入夜后的可观赏性，粗略引导即可）。"""
    if not moonrise:
        return "20:00 - 23:00"
    if illum >= 60:
        # 亮月：入夜后基本都在天上
        if moonrise > "20:00":
            return f"{moonrise} - 次日 01:00"
        return "20:00 - 23:00"
    if "18:00" < moonrise <= "20:00":
        return f"{moonrise} - 23:00"
    # 暗月或月出过早/过晚：月亮入夜后可见性有限
    return "20:00 - 23:00（以实时月出月落为准）"


def _build_reason(phase: str, illum: float, cloud, precip_prob) -> str:
    parts = [f"今晚月相为「{phase}」，月亮照明度 {illum:.0f}%"]
    if cloud is None:
        parts.append("夜间云量数据缺失")
    elif cloud < 30:
        parts.append("夜间云量较少，视野通透")
    elif cloud <= 70:
        parts.append(f"夜间云量约 {cloud:.0f}%，仍有赏月窗口")
    else:
        parts.append(f"夜间云量约 {cloud:.0f}%，可能遮挡月亮")
    if precip_prob is not None and precip_prob >= 40:
        parts.append(f"降水概率 {precip_prob:.0f}%")
    return "；".join(parts)


def _tips(level: str, phase: str, best_time: Optional[str]) -> str:
    if level == "高":
        return f"「{phase}」之夜，{best_time} 前后是黄金时段，带上望远镜或相机，找个开阔高处吧！"
    if level == "中":
        return f"可碰碰运气，{best_time} 前后留意云缝；高层或江边视野更佳。"
    return "今晚赏月条件一般，适合安排其他活动；可关注明晚月相。"


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
