#!/usr/bin/env python3
"""晴屿 · 智能天气桌面应用

独立桌面应用：pywebview 渲染精致界面（无浏览器工具栏、无 Web 服务），
Python 侧通过 js_api 提供全部数据，兼容 Python 3.9+。

运行：
    python weather_desktop.py               # 启动桌面窗口
    python weather_desktop.py --city 上海    # 指定城市启动
    python weather_desktop.py --cli         # 无界面：打印一次天气报告
    python weather_desktop.py --selftest    # 自检：验证数据链路（不弹窗）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config import Settings
from app.models import Location, WeatherReport
from app.services.geolocation import GeoLocator
from app.services.recommendation import RecommendationEngine
from app.services.report import build_report
from app.services.warnings import WarningService
from app.services.weather import WeatherClient

UI_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "ui", "dashboard.html")

WEATHER_EMOJI = {
    "clear": "☀️", "partly_cloudy": "⛅", "cloudy": "☁️", "fog": "🌫",
    "drizzle": "🌦", "rain": "🌧", "snow": "🌨", "shower": "🌦",
    "thunderstorm": "⛈", "unknown": "🌡",
}
CATEGORY = {
    0: "clear", 1: "clear", 2: "partly_cloudy", 3: "cloudy", 45: "fog",
    48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle",
    57: "drizzle", 61: "rain", 63: "rain", 65: "rain", 66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 80: "shower", 81: "shower",
    82: "shower", 85: "snow", 86: "snow", 95: "thunderstorm", 96: "thunderstorm",
    99: "thunderstorm",
}
DESC_CN = {
    "clear": "晴", "partly_cloudy": "多云间晴", "cloudy": "多云", "fog": "雾",
    "drizzle": "毛毛雨", "rain": "雨", "snow": "雪", "shower": "阵雨",
    "thunderstorm": "雷暴", "unknown": "未知",
}
REC_ICON = {
    "sunrise": "🌅", "sunset": "🌇", "stargazing": "🌌",
    "rainbow": "🌈", "clouds": "☁️", "snow": "❄️", "moon": "🌙",
}
REC_NAME = {
    "sunrise": "朝霞 / 日出", "sunset": "晚霞 / 日落", "stargazing": "星空观测",
    "rainbow": "彩虹观测", "clouds": "云海 / 流云", "snow": "雪景", "moon": "赏月",
}
DIRS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]


# 运行时设置默认值（可在「⚙ 设置」中修改，持久化到 weather_desktop_settings.json）
DEFAULT_RUNTIME_SETTINGS: dict = {
    "open_meteo_models": "",   # 预报模型：""=默认混合 / cma_grapes_global=中国气象局
    "show_official": True,     # 显示中国天气网官方实况/预报
    "nmc_warnings": True,      # 中央气象台在线预警
    "typhoon_warnings": True,  # 台风路径判定预警
}


def local_now(loc: Location) -> datetime:
    """取目标城市时区的当前时刻（naive），用于与当地预报小时对比。

    避免用本机（如上海）时间对比国外城市（如纽约）的预报小时导致错位。
    """
    tz = getattr(loc, "timezone", "") or ""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)
    except Exception:
        try:
            import pytz
            return datetime.now(pytz.timezone(tz)).replace(tzinfo=None)
        except Exception:
            return datetime.now()


def _cat(code):
    return CATEGORY.get(code, "unknown")


def sunset_azimuth(latitude: float, day_of_year: int) -> float:
    """估算日落方位角（自正北顺时针，0-360°）。"""
    decl = 23.44 * math.sin(2 * math.pi * (284 + day_of_year) / 365.0)
    x = math.sin(math.radians(decl)) / math.cos(math.radians(latitude))
    x = max(-1.0, min(1.0, x))
    delta = math.degrees(math.asin(x))
    return (270.0 + delta) % 360.0


def dir_text(az: float) -> str:
    return DIRS[int(((az + 22.5) % 360) // 45)]


def wind_dir_text(deg) -> str:
    """将风向角度转换为中文方位（自正北顺时针）。"""
    if deg is None:
        return "--"
    return DIRS[int(((float(deg) + 22.5) % 360) // 45)]


async def _empty_warnings():
    return []


def _dedupe_warnings(warnings):
    seen = set()
    out = []
    for warning in warnings:
        key = (warning.source, warning.title)
        if key not in seen:
            seen.add(key)
            out.append(warning)
    return out


def _warning_to_ui(warning):
    return {
        "level": warning.level,
        "title": warning.title,
        "desc": warning.description or "",
        "source": warning.source,
        "issued_at": warning.issued_at or "",
        "url": warning.url or "",
    }


class _AsyncRunner:
    """在后台复用一个事件循环，避免 pywebview 每次调用都 asyncio.run。"""

    def __init__(self):
        self._ready = threading.Event()
        self._loop = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="weather-async")
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("天气异步运行时启动失败")

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        loop.close()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=90)
        except Exception:
            future.cancel()
            raise

    def close(self):
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
            self._loop = None


class DesktopAPI:
    """暴露给前端的 Python 数据桥（js_api）。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._location: Optional[Location] = None
        self._fahrenheit = False
        self._runtime: dict = self._load_runtime_settings()
        self._history: list[str] = self._load_history()
        self._last_report: Optional[WeatherReport] = None
        self._report_cache: dict[tuple, tuple[float, WeatherReport]] = {}
        self._async_runner = _AsyncRunner()

    # ---------------------------------------------------------- 历史
    def _hist_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "weather_desktop_history.json")

    def _load_history(self) -> list[str]:
        try:
            with open(self._hist_path(), encoding="utf-8") as f:
                data = json.load(f)
            return [str(c) for c in data][:5] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save_history(self, city: str):
        city = str(city).strip()
        if not city:
            return
        hist = [c for c in self._history if c != city]
        hist.insert(0, city)
        self._history = hist[:5]
        try:
            with open(self._hist_path(), "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ---------------------------------------------------------- 运行时设置
    def _settings_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "weather_desktop_settings.json")

    def _load_runtime_settings(self) -> dict:
        out = dict(DEFAULT_RUNTIME_SETTINGS)
        try:
            with open(self._settings_path(), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in DEFAULT_RUNTIME_SETTINGS:
                    if k in data:
                        out[k] = data[k]
        except (OSError, ValueError):
            pass
        return out

    def _save_runtime_settings(self):
        try:
            with open(self._settings_path(), "w", encoding="utf-8") as f:
                json.dump(self._runtime, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def get_settings(self) -> dict:
        return dict(self._runtime)

    def set_setting(self, key: str, value) -> dict:
        """更新运行时设置并持久化，返回最新设置。"""
        if key not in DEFAULT_RUNTIME_SETTINGS:
            return {"ok": False, "settings": dict(self._runtime)}
        if key == "open_meteo_models":
            value = str(value or "").strip()
            if value not in ("", "cma_grapes_global", "best_match"):
                value = ""
            self._runtime[key] = value
        else:
            self._runtime[key] = bool(value)
        self._save_runtime_settings()
        return {"ok": True, "settings": dict(self._runtime)}

    # ---------------------------------------------------------- 数据
    async def _fetch(self, loc: Location) -> Optional[dict]:
        models = self._runtime.get("open_meteo_models") or None
        cache_key = (
            round(loc.latitude, 4), round(loc.longitude, 4),
            bool(self._fahrenheit), models or "default",
            bool(self._runtime.get("show_official", True)),
            bool(self._runtime.get("nmc_warnings", True)),
            bool(self._runtime.get("typhoon_warnings", True)),
        )
        cached = self._report_cache.get(cache_key)
        ttl = max(0.0, float(getattr(self._settings, "weather_cache_seconds", 180.0)))
        if cached and time.monotonic() - cached[0] < ttl:
            self._last_report = cached[1]
            return self._to_ui_dict(cached[1], loc)
        weather = WeatherClient(self._settings, models=models)
        warnings_svc = WarningService(self._settings, weather)
        try:
            rec_engine = RecommendationEngine()
            report = await build_report(
                weather, warnings_svc, rec_engine, self._settings,
                override_location=loc,
                fahrenheit=self._fahrenheit,
                include_glow=False,
                nmc_enabled=self._runtime.get("nmc_warnings", True),
                typhoon_enabled=self._runtime.get("typhoon_warnings", True),
                official_enabled=self._runtime.get("show_official", True),
                models=models,
            )
            if report is None:
                return None
            self._last_report = report
            self._report_cache[cache_key] = (time.monotonic(), report)
            return self._to_ui_dict(report, loc)
        finally:
            await warnings_svc.aclose()
            await weather.aclose()

    def _to_ui_dict(self, r: WeatherReport, loc: Location) -> dict:
        today = r.daily[0] if r.daily else None
        now = local_now(loc)
        # 取预报中离当前时刻最近的一小时，用于展示「当前」湿度/风况/体感
        hour = None
        if today and today.hours:
            best = None
            for h in today.hours:
                delta = abs((h.time - now).total_seconds())
                if best is None or delta < best[0]:
                    best = (delta, h)
            hour = best[1] if best else None
        doy = now.timetuple().tm_yday
        az_sunset = sunset_azimuth(loc.latitude, doy)
        az_sunrise = (360.0 - az_sunset) % 360.0

        t = {}
        if today:
            t = {
                "temp_max": today.temp_max, "temp_min": today.temp_min,
                "emoji": WEATHER_EMOJI[_cat(today.weather_code)],
                "desc": DESC_CN[_cat(today.weather_code)],
                "humidity": hour.humidity if hour else None,
                "wind_speed": hour.wind_speed if hour else None,
                "wind_gusts": hour.wind_gusts if hour else None,
                "wind_direction": wind_dir_text(hour.wind_direction) if hour else "--",
                "feels_like": hour.apparent_temperature if hour else None,
                "precip_probability": today.precip_probability_max,
                "uv": today.uv_index_max,
                "sunrise": str(today.sunrise or "")[-5:] or "--:--",
                "sunset": str(today.sunset or "")[-5:] or "--:--",
                "aqi": None,
            }
            aq = r.air_quality
            if aq and aq.aqi is not None:
                t["aqi"] = {
                    "value": aq.aqi, "level": aq.level,
                    "pm25": aq.pm25, "pm10": aq.pm10,
                    "o3": aq.ozone, "no2": aq.no2, "so2": aq.so2, "co": aq.co,
                }

        daily = []
        for d in r.daily:
            daily.append({
                "date": d.date, "emoji": WEATHER_EMOJI[_cat(d.weather_code)],
                "desc": DESC_CN[_cat(d.weather_code)],
                "tmax": d.temp_max, "tmin": d.temp_min,
                "pop": d.precip_probability_max,
            })

        trend = []
        pts = []
        for d in r.daily:
            for h in d.hours:
                if h.time >= now and h.temperature is not None:
                    pts.append(h)
            if len(pts) >= 12:
                break
        for h in pts[:12]:
            trend.append({
                "hour": f"{h.time.hour:02d}:00",
                "t": h.temperature,
                "emoji": WEATHER_EMOJI[_cat(h.weather_code)],
            })

        s = r.sunset
        sunset = {
            "probability": s.probability if s else 0.0,
            "level": s.level if s else "低",
            "sunset_time": (s.sunset_time[-5:] if s and s.sunset_time else "--:--"),
            "cloud_cover": s.cloud_cover_at_sunset if s else None,
            "tips": s.tips if s else "",
        }

        moon = None
        if r.moon:
            moon = {
                "phase": r.moon.phase,
                "illuminated": r.moon.illuminated,
                "score": r.moon.score,
                "level": r.moon.level,
                "best_time": r.moon.best_time or "",
                "moonrise": r.moon.moonrise or "",
                "cloud_cover": r.moon.cloud_cover,
                "reason": r.moon.reason or "",
                "tips": r.moon.tips or "",
            }
        rain_at = None
        if today and today.hours:
            from app.services.weather import next_rain_time
            _rain = next_rain_time(today.hours, now=now)
            if _rain is not None:
                rain_at = f"{_rain:%H:%M}"

        life = [
            {"icon": idx.icon, "name": idx.name, "level": idx.level, "advice": idx.advice}
            for idx in (r.life_indices or [])
        ]

        glow_map = [
            {"name": g.name, "lat": g.latitude, "lon": g.longitude,
             "sunset": g.sunset_probability, "sunset_level": g.sunset_level,
             "sunset_time": g.sunset_time or "",
             "sunrise": g.sunrise_score, "sunrise_level": g.sunrise_level,
             "sunrise_time": g.sunrise_time or ""}
            for g in (r.glow_map or [])
        ]

        return {
            "location": r.location,
            "generated_at": (r.generated_at or "")[11:16] or "--:--",
            "unit": "f" if self._fahrenheit else "c",
            "lat": round(loc.latitude, 4),
            "lon": round(loc.longitude, 4),
            "province": loc.admin1 or "",
            "city2": loc.admin2 or "",
            "today": t,
            "daily": daily,
            "trend": trend,
            "glow_map": glow_map,
            "sunset": sunset,
            "moon": moon,
            "astro": [
                {"icon": a.icon, "kind": a.kind, "title": a.title, "when": a.when or "",
                 "score": a.score, "level": a.level, "summary": a.summary or "",
                 "detail": a.detail or "", "best_time": a.best_time or ""}
                for a in (r.astro_events or [])
            ],
            "life": life,
            "news": self._news_bulletin(r),
            "rain_at": rain_at,
            "sunmap": {
                "lat": loc.latitude,
                "az_sunrise": az_sunrise, "az_sunset": az_sunset,
                "dir_sunrise": dir_text(az_sunrise), "dir_sunset": dir_text(az_sunset),
            },
            "warnings": [
                {"level": w.level, "title": w.title, "desc": w.description or "",
                 "source": w.source, "issued_at": w.issued_at or "", "url": w.url or ""}
                for w in r.warnings
            ],
            "official": r.official,
            "recommendations": [
                {"icon": REC_ICON.get(rc.category, "✨"),
                 "name": REC_NAME.get(rc.category, rc.title),
                 "level": rc.level, "score": rc.score, "summary": rc.summary or "",
                 "day_label": rc.day_label or ""}
                for rc in r.recommendations
            ],
            "history": self._history,
            "settings": dict(self._runtime),
        }

    def _news_bulletin(self, r: WeatherReport) -> str:
        from app.services.news import build_news_bulletin
        return build_news_bulletin(r)

    def get_glow_map(self) -> list[dict]:
        """单独获取全国霞光地图；前端在首屏渲染后调用，不阻塞地点切换。"""
        return self._sync(self._get_glow_map())

    async def _get_glow_map(self) -> list[dict]:
        from app.services.glow_map import GlowMapService

        models = self._runtime.get("open_meteo_models") or None
        glow = GlowMapService(self._settings, models=models)
        try:
            items = await glow.compute()
            return [
                {"name": g.name, "lat": g.latitude, "lon": g.longitude,
                 "sunset": g.sunset_probability, "sunset_level": g.sunset_level,
                 "sunset_time": g.sunset_time or "", "sunrise": g.sunrise_score,
                 "sunrise_level": g.sunrise_level, "sunrise_time": g.sunrise_time or ""}
                for g in items
            ]
        finally:
            await glow.aclose()

    def refresh_warnings(self) -> list[dict]:
        """仅刷新实时灾害预警，供前端定时轮询，不重拉全国霞光地图。"""
        return self._sync(self._refresh_warnings())

    async def _refresh_warnings(self) -> list[dict]:
        if self._location is None:
            return []
        loc = self._location
        weather = WeatherClient(self._settings, models=self._runtime.get("open_meteo_models") or None)
        svc = WarningService(self._settings, weather)
        try:
            rules = []
            if self._last_report:
                rules = svc.detect_rules(self._last_report.daily, now=local_now(loc))
            import asyncio
            tasks = [
                svc.fetch_official(loc.latitude, loc.longitude),
                svc.fetch_nmc(
                    loc.admin1 or "", loc.admin2 or "", loc.admin3 or "",
                    max_age_hours=self._settings.warning_max_age_hours,
                ) if self._runtime.get("nmc_warnings", True) else _empty_warnings(),
                svc.detect_typhoon(loc.latitude, loc.longitude)
                if self._runtime.get("typhoon_warnings", True) else _empty_warnings(),
            ]
            qweather, nmc, typhoon = await asyncio.gather(*tasks)
            warnings = _dedupe_warnings([*rules, *qweather, *nmc, *typhoon])
            return [_warning_to_ui(w) for w in warnings]
        finally:
            await svc.aclose()
            await weather.aclose()

    # ---------------------------------------------------------- js_api
    # 注意：pywebview 的 js_api 调用是同步的（不支持 async 方法），
    # 故此处对外提供同步包装，内部转 async 执行。
    def _sync(self, coro):
        return self._async_runner.run(coro)

    def locate(self) -> dict:
        """IP 定位当前城市并返回全部数据。"""
        return self._sync(self._locate())

    async def _locate(self) -> dict:
        loc = await GeoLocator().locate()
        if loc is None:
            raise RuntimeError("IP 定位失败，请手动搜索城市")
        loc = await self._enrich_location(loc)
        self._location = loc
        self._save_history(loc.name)
        return await self._fetch(loc)

    async def _enrich_location(self, loc: Location) -> Location:
        """把 IP 定位的英文城市名转为中文，并补充省/市（用于预警过滤与展示）。

        IP 定位服务（ipinfo / geolocation-db）返回英文名（如 Shenzhen）且无省市信息，
        这里用 Open-Meteo 地理编码把城市名中文化并补全 admin1/admin2；
        坐标保留 IP 定位结果（更贴近用户当前位置）。
        """
        weather = WeatherClient(self._settings)
        try:
            enriched = await weather.geocode(loc.name)
            if enriched is not None:
                enriched.latitude = loc.latitude
                enriched.longitude = loc.longitude
                enriched.timezone = loc.timezone or enriched.timezone
                return enriched
        except Exception as e:
            logging.getLogger("desktop").warning("城市名中文化失败: %s", e)
        finally:
            await weather.aclose()
        return loc

    def search(self, city: str) -> dict:
        return self._sync(self._search(city))

    async def _search(self, city: str) -> dict:
        weather = WeatherClient(self._settings)
        try:
            loc = await weather.geocode(str(city).strip())
        finally:
            await weather.aclose()
        if loc is None:
            raise RuntimeError(f"未找到城市「{city}」的天气数据，可尝试搜索邻近大城市（如省城）")
        self._location = loc
        self._save_history(loc.name)
        return await self._fetch(loc)

    def refresh(self) -> dict:
        return self._sync(self._refresh())

    async def _refresh(self) -> dict:
        if self._location is None:
            return await self._locate()
        self._invalidate_report_cache(self._location)
        return await self._fetch(self._location)

    def _invalidate_report_cache(self, loc: Location) -> None:
        prefix = (round(loc.latitude, 4), round(loc.longitude, 4))
        for key in list(self._report_cache):
            if key[:2] == prefix:
                self._report_cache.pop(key, None)

    def toggle_unit(self) -> dict:
        return self._sync(self._toggle_unit())

    async def _toggle_unit(self) -> dict:
        self._fahrenheit = not self._fahrenheit
        if self._location is None:
            return await self._locate()
        return await self._fetch(self._location)

    def get_state(self) -> dict:
        return {"history": self._history}

    def close(self):
        """关闭桌面 API 的后台异步运行时。"""
        self._async_runner.close()

    def open_url(self, url: str) -> dict:
        """在系统默认浏览器中打开外部链接（用于预警详情页跳转）。"""
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False}
        try:
            webbrowser.open(url)
            return {"ok": True}
        except Exception as e:
            logging.getLogger("desktop").warning("打开链接失败: %s", e)
            return {"ok": False}


# ---------------- CLI / 自检 ----------------
async def cli_run(city: Optional[str]) -> int:
    settings = Settings()
    api = DesktopAPI(settings)
    try:
        data = await api._search(city) if city else await api._locate()
        t = data["today"]
        loc_txt = f"{data['location']}"
        if data.get("province"):
            loc_txt += f"（{data['province']}" + (f" {data['city2']}" if data.get("city2") and data.get("city2") != data.get("province") else "") + "）"
        if data.get("lat") is not None:
            loc_txt += f" {data['lat']:.4f}N, {data['lon']:.4f}E"
        print(f"\n【{loc_txt}】{t['desc']} {t['emoji']}")
        if t['temp_min'] is not None and t['temp_max'] is not None:
            print(f"气温：{t['temp_min']:.0f}°C ~ {t['temp_max']:.0f}°C")
        else:
            print("气温：数据获取中")
        if t['precip_probability'] is not None:
            print(f"降水：{t['precip_probability']:.0f}%")
        s = data["sunset"]
        m = data["sunmap"]
        print(f"晚霞概率：{s['probability']:.0f}%（{s['level']}） 日落 {s['sunset_time']}"
              f"  方位 {m['dir_sunset']} {m['az_sunset']:.0f}°")
        if t["aqi"]:
            print(f"空气质量：{t['aqi']['level']} AQI {t['aqi']['value']:.0f}"
                  + (f" PM2.5 {t['aqi']['pm25']}" if t["aqi"]["pm25"] is not None else ""))
        if data["warnings"]:
            print("预警：")
            for w in data["warnings"]:
                print(f"  [{w['level']}] {w['title']}")
        if data["recommendations"]:
            print("观赏推荐：")
            for rc in data["recommendations"][:5]:
                day = f"（{rc['day_label']}）" if rc.get("day_label") else ""
                print(f"  · {rc['icon']} {rc['name']}{day} [{rc['level']}] {rc['score']}")
        m = data.get("moon")
        if m:
            print(f"赏月：月相「{m['phase']}」照明 {m['illuminated']:.0f}%  指数 {m['score']:.0f}%（{m['level']}）"
                  + (f"  最佳时段 {m['best_time']}" if m.get("best_time") else ""))
        if data.get("astro"):
            print("特殊天象：")
            for a in data["astro"][:4]:
                print(f"  · {a['icon']} {a['title']}（{a['when'] or '--'}）[{a['level']}] {a['score']}  {a['summary']}")
        if data.get("life"):
            print("生活指数：" + "  ".join(
                f"{x['icon']}{x['name']}:{x['level']}" for x in data["life"]))
        if data.get("rain_at"):
            print(f"带伞提醒：预计 {data['rain_at']} 起有降雨")
        trend_txt = " ".join(f"{p['hour']} {p['t']:.0f}°" for p in data['trend'])
        print(f"趋势：{trend_txt}")
        return 0
    finally:
        api.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="晴屿 · 智能天气桌面应用")
    parser.add_argument("--city", type=str, default=None, help="指定城市（跳过自动定位）")
    parser.add_argument("--cli", action="store_true", help="无界面模式：打印一次天气报告")
    parser.add_argument("--selftest", action="store_true", help="自检数据链路（不弹窗）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.cli or args.selftest:
        return asyncio.run(cli_run(args.city))

    settings = Settings()
    api = DesktopAPI(settings)

    url = UI_HTML
    if args.city:
        url += "?city=" + args.city

    try:
        import webview

        webview.create_window(
            "晴屿 · 智能天气",
            url,
            js_api=api,
            width=1240,
            height=920,
            min_size=(1000, 720),
        )
        webview.start()
        api.close()
        return 0
    except ImportError:
        print("=" * 52)
        print("缺少 pywebview（独立窗口组件），无法启动窗口。")
        print("请执行：python -m pip install pywebview")
        print("=" * 52)
        input("按回车键退出…")
        return 1
    except Exception as e:
        print(f"[晴屿] 窗口启动失败：{e}")
        input("按回车键退出…")
        return 1


if __name__ == "__main__":
    sys.exit(main())
