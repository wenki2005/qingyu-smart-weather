"""数据模型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class HourlyWeather:
    """某一小时的天气快照。"""
    time: datetime
    temperature: Optional[float] = None
    apparent_temperature: Optional[float] = None   # 体感温度
    humidity: Optional[float] = None
    precip_probability: Optional[float] = None
    precipitation: Optional[float] = None
    weather_code: Optional[int] = None
    cloud_cover: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_gusts: Optional[float] = None   # 阵风（10 分钟最大风速）
    wind_direction: Optional[float] = None  # 风向（度，自正北顺时针）
    visibility: Optional[float] = None
    surface_pressure: Optional[float] = None


@dataclass
class DailyWeather:
    """某一天的天气摘要。"""
    date: str
    weather_code: Optional[int] = None
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    sunrise: Optional[str] = None
    sunset: Optional[str] = None
    uv_index_max: Optional[float] = None
    precip_probability_max: Optional[float] = None
    hours: list[HourlyWeather] = field(default_factory=list)


@dataclass
class Location:
    name: str
    latitude: float
    longitude: float
    timezone: str = "Asia/Shanghai"
    country: Optional[str] = None
    admin1: Optional[str] = None          # 省级行政区（如：浙江、广东），用于预警过滤
    admin2: Optional[str] = None          # 地级市（如：杭州市），用于预警过滤
    wcn_code: Optional[str] = None        # 中国天气网城市代码（如 101210304），用于官方天气/预警


@dataclass
class AirQuality:
    """空气质量（US AQI 分级 + 主要污染物，单位微克/立方米）。"""
    aqi: Optional[float] = None
    level: str = "未知"
    pm25: Optional[float] = None
    pm10: Optional[float] = None
    ozone: Optional[float] = None        # O3
    no2: Optional[float] = None          # NO2
    so2: Optional[float] = None          # SO2
    co: Optional[float] = None           # CO

    @staticmethod
    def level_for(aqi: float) -> str:
        """US AQI -> 中文等级。"""
        if aqi <= 50:
            return "优"
        if aqi <= 100:
            return "良"
        if aqi <= 150:
            return "轻度污染"
        if aqi <= 200:
            return "中度污染"
        if aqi <= 300:
            return "重度污染"
        return "严重污染"


@dataclass
class SunsetInfo:
    """晚霞观赏信息。"""
    sunset_time: Optional[str] = None
    probability: float = 0.0            # 0-100
    level: str = "低"                    # 高 / 中 / 低
    reason: str = ""
    cloud_cover_at_sunset: Optional[float] = None
    tips: str = ""


@dataclass
class MoonInfo:
    """赏月指南信息。"""
    date: str = ""
    phase: str = "新月"                   # 新月/娥眉月/上弦月/盈凸月/满月/亏凸月/下弦月/残月
    illuminated: float = 0.0             # 月亮照明百分比 0-100
    score: float = 0.0                   # 赏月指数 0-100
    level: str = "低"                     # 高 / 中 / 低
    reason: str = ""
    best_time: Optional[str] = None      # 最佳赏月时段
    cloud_cover: Optional[float] = None  # 夜间平均云量
    moonrise: Optional[str] = None       # 月出时间（约估）
    tips: str = ""


@dataclass
class LifeIndex:
    """生活指数（穿衣/带伞/洗车/防晒/运动/晾晒/感冒）。"""
    name: str
    icon: str
    level: str
    advice: str


@dataclass
class GlowCity:
    """霞光地图：单个城市的朝霞/晚霞评估。"""
    name: str
    latitude: float
    longitude: float
    sunset_probability: float = 0.0      # 今晚晚霞概率 0-100
    sunset_level: str = "低"
    sunset_time: str = ""
    sunrise_score: float = 0.0           # 明晨朝霞指数 0-100
    sunrise_level: str = "一般"
    sunrise_time: str = ""


@dataclass
class WeatherWarning:
    """天气预警（官方或规则引擎）。"""
    title: str
    level: str = "提示"                  # 提示 / 黄色 / 橙色 / 红色
    description: str = ""
    source: str = "rules"               # rules=内置规则引擎, typhoon=台风路径, nmc=中央气象台, qweather=和风天气
    issued_at: str = ""
    url: str = ""                        # 详情页链接（前端点击跳转）


@dataclass
class ViewingRecommendation:
    """天气观赏推荐。"""
    category: str                        # 见 constants.VIEWING_*
    title: str
    score: int                           # 0-100
    level: str                           # 强烈推荐 / 推荐 / 一般
    summary: str = ""
    best_time: Optional[str] = None
    tips: str = ""
    day_label: str = ""                  # 今天 / 明天（用于区分跨日同类推荐）


@dataclass
class AstroEvent:
    """特殊天文现象（流星雨 / 极光 / 日月食 / 超级月亮）。"""
    kind: str                        # meteor_shower / aurora / eclipse / supermoon
    title: str
    icon: str
    when: str = ""                   # 时间提示（今晚极大 / 未来 3 天 / X月X日）
    score: int = 0                   # 观测指数 0-100
    level: str = "低"                # 高 / 中 / 低
    summary: str = ""
    detail: str = ""
    source: str = ""
    best_time: Optional[str] = None


@dataclass
class WeatherReport:
    """一次完整的推送内容（组合多个数据源）。"""

    location: str
    generated_at: str
    daily: list[DailyWeather] = field(default_factory=list)
    sunset: Optional[SunsetInfo] = None
    moon: Optional[MoonInfo] = None
    warnings: list[WeatherWarning] = field(default_factory=list)
    recommendations: list[ViewingRecommendation] = field(default_factory=list)
    air_quality: Optional[AirQuality] = None
    life_indices: list[LifeIndex] = field(default_factory=list)
    glow_map: list[GlowCity] = field(default_factory=list)
    official: Optional[dict] = None   # 中国天气网官方实况/今日预报
    astro_events: list[AstroEvent] = field(default_factory=list)   # 特殊天象播报

    def to_message(self) -> dict[str, str]:
        """转换为各渠道推送所需的 {title, content} 结构。"""
        raise NotImplementedError("由子类或格式化模块实现")
