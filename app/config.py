"""智能天气推送服务 - 配置模块

所有配置均可通过环境变量或 .env 文件覆盖，优先级：环境变量 > .env > 默认值。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    app_name: str = "Smart Weather Push"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # ---------- 位置 ----------
    # 城市名（用于地理编码，示例：北京、上海、广州），优先于经纬度
    city: Optional[str] = "北京"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # 展示用城市名，默认使用 city
    display_city: Optional[str] = None

    # ---------- 时区 ----------
    # IANA 时区，如 Asia/Shanghai、Europe/London
    timezone: str = "Asia/Shanghai"
    # 预报提前天数
    forecast_days: int = 3

    # ---------- Open-Meteo ----------
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_geo_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    open_meteo_timeout: float = 15.0
    # 可选：指定预报模型（Open-Meteo 支持多模型）。
    # 中国地区可填 cma_grapes_global（中国气象局 CMA-GRAPES，更贴近官方预报，
    # 注意该模型无紫外线指数）；留空使用 Open-Meteo 默认最佳混合模型。
    open_meteo_models: Optional[str] = None

    # ---------- 可选：和风天气官方预警（QWeather） ----------
    # 不配置则仅使用内置规则引擎检测特殊天气
    qweather_api_key: Optional[str] = None
    qweather_base_url: str = "https://devapi.qweather.com/v7"

    # ---------- 在线灾害预警（中央气象台，免费无需 Key） ----------
    nmc_alarm_url: str = "http://www.nmc.cn/rest/findAlarm"
    warning_max_age_hours: float = 12.0  # 无有效截止时间的官方预警最大保留时长
    glow_cache_minutes: float = 20.0     # 全国霞光地图缓存时长，避免切换地点重复拉取
    weather_cache_seconds: float = 180.0 # 当前地点报告短缓存；灾害预警另行实时轮询
    enhancement_timeout_seconds: float = 6.0  # 官方/天象增强源单源最大等待时间

    # ---------- 台风路径（中央气象台台风网，免费无需 Key） ----------
    typhoon_list_url: str = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
    typhoon_view_url: str = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{sid}"
    typhoon_max_range_km: float = 700.0   # 距台风路径最近距离超过该值视为不受影响

    # ---------- 预警规则阈值 ----------
    warn_wind_speed: float = 17.2      # 8 级大风阈值(m/s)
    warn_wind_strong: float = 24.5     # 10 级强风阈值(m/s)，视为台风外围影响
    warn_wind_typhoon: float = 32.7    # 12 级台风级大风阈值(m/s)
    warn_wind_gust: float = 32.7       # 阵风阈值(m/s)，触发台风级阵风提醒
    warn_temp_high: float = 40.0       # 高温阈值(°C)
    warn_temp_low: float = -10.0       # 低温阈值(°C)
    warn_precip_hourly: float = 10.0   # 小时降水量(mm) 触发强降雨提示
    warn_visibility: float = 1000.0    # 能见度(m) 触发大雾提示
    warn_uv_index: float = 8.0         # 紫外线指数 触发强紫外线提示

    @property
    def effective_city(self) -> str:
        return self.display_city or self.city or "当前城市"

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()

