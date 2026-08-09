"""天气代码与业务常量。"""

from __future__ import annotations

# WMO Weather interpretation codes (WW)
# https://open-meteo.com/en/docs
WMO_WEATHER_CODE: dict[int, tuple[str, str, str]] = {
    0: ("晴", "clear", "🌤"),
    1: ("基本晴朗", "clear", "🌤"),
    2: ("局部多云", "partly_cloudy", "⛅"),
    3: ("多云", "cloudy", "☁️"),
    45: ("雾", "fog", "🌫"),
    48: ("雾凇", "fog", "🌫"),
    51: ("小毛毛雨", "drizzle", "🌦"),
    53: ("毛毛雨", "drizzle", "🌦"),
    55: ("大毛毛雨", "drizzle", "🌦"),
    56: ("冻毛毛雨", "drizzle", "🌦"),
    57: ("强冻毛毛雨", "drizzle", "🌦"),
    61: ("小雨", "rain", "🌧"),
    63: ("中雨", "rain", "🌧"),
    65: ("大雨", "rain", "🌧"),
    66: ("冻雨", "rain", "🌧"),
    67: ("强冻雨", "rain", "🌧"),
    71: ("小雪", "snow", "🌨"),
    73: ("中雪", "snow", "🌨"),
    75: ("大雪", "snow", "❄️"),
    77: ("雪粒", "snow", "❄️"),
    80: ("小阵雨", "shower", "🌦"),
    81: ("阵雨", "shower", "🌧"),
    82: ("强阵雨", "shower", "🌧"),
    85: ("小阵雪", "snow", "🌨"),
    86: ("强阵雪", "snow", "❄️"),
    95: ("雷阵雨", "thunderstorm", "⛈"),
    96: ("雷阵雨伴冰雹", "thunderstorm", "⛈"),
    99: ("强雷暴伴冰雹", "thunderstorm", "⛈"),
}

# 天气分类 -> 中文描述（用于观赏推荐）
CATEGORY_CN: dict[str, str] = {
    "clear": "晴天",
    "partly_cloudy": "多云间晴",
    "cloudy": "多云",
    "fog": "雾",
    "drizzle": "毛毛雨",
    "rain": "雨",
    "snow": "雪",
    "shower": "阵雨",
    "thunderstorm": "雷暴",
}

# 特殊天气英文标签 -> 中文提示词
SPECIAL_WEATHER_CN: dict[str, str] = {
    "大风": "大风",
    "高温": "高温",
    "低温": "低温",
    "强降雨": "强降雨",
    "大雾": "大雾",
    "强紫外线": "强紫外线",
    "雷暴": "雷暴",
    "冰雹": "冰雹",
    "降雪": "降雪",
    "强风": "强风",
    "台风": "台风",
}

# 观赏推荐类别
VIEWING_SUNRISE = "sunrise"
VIEWING_SUNSET = "sunset"
VIEWING_STAR = "stargazing"
VIEWING_RAINBOW = "rainbow"
VIEWING_CLOUD = "clouds"
VIEWING_SNOW = "snow"
VIEWING_MOON = "moon"

# 生活指数类别
LIFE_DRESS = "dressing"        # 穿衣
LIFE_UMBRELLA = "umbrella"     # 带伞
LIFE_CARWASH = "carwash"       # 洗车
LIFE_SUNSCREEN = "sunscreen"   # 防晒
LIFE_EXERCISE = "exercise"     # 运动
LIFE_DRYING = "drying"         # 晾晒
LIFE_COLD = "cold"             # 感冒
