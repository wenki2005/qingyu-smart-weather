"""台风路径与影响判定。

数据源：中央气象台台风网（typhoon.nmc.cn）免费接口，无需 API Key。
- 列表接口返回当前活跃台风（含编号/中英文名）
- 详情接口返回实况路径 + 中央气象台（BABJ）未来 120h 预报路径
- 通过「位置与路径各时次的大圆距离」判定某地是否受台风影响，
  只有真正处于台风影响半径内才生成「台风」预警，避免把普通大风误报成台风。
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TYPHOON_LIST_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
TYPHOON_VIEW_URL = "http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{sid}"

# NMC 台风等级代码 -> 中文
CATEGORY_CN = {
    "TD": "热带低压",
    "TS": "热带风暴",
    "STS": "强热带风暴",
    "TY": "台风",
    "STY": "强台风",
    "SuperTY": "超强台风",
}

EARTH_R = 6371.0


@dataclass
class TyphoonImpact:
    """一次台风对某地的影响评估。"""
    name_cn: str
    name_en: str
    number: str                    # 如 2615（2026 年第 15 号）
    category: str                  # 等级代码
    category_cn: str
    wind_speed: float              # 当前中心附近最大风速 m/s
    min_distance_km: float         # 距路径最近距离
    closest_hour: Optional[int]    # 最近时次（0=当前，12/24/…=预报小时）
    closest_time: Optional[str]    # 最近时次的时间串（如 202608091400）
    affected: bool                 # 是否在影响半径内


def _parse_jsonp(text: str) -> dict:
    """剥离 JSONP 包装：func(({...})) -> dict（允许末尾多个右括号）。"""
    m = re.search(r"\(\s*(\{.*\})\s*\)+\s*;?$", text, re.S)
    if not m:
        raise ValueError("JSONP 解析失败")
    return json.loads(m.group(1))


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """大圆距离（km）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(math.sqrt(min(1.0, a)))


class NmcTyphoonClient:
    """中央气象台台风客户端。"""

    def __init__(self, settings, timeout: float = 8.0):
        self._s = settings
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "http://typhoon.nmc.cn/"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except RuntimeError:
                pass
            self._client = None

    async def active_storms(self) -> list[dict]:
        """返回活跃台风列表：[{sid, name_cn, name_en, number}]，失败返回空。"""
        try:
            client = await self._http()
            resp = await client.get(self._s.typhoon_list_url)
            resp.raise_for_status()
            data = _parse_jsonp(resp.text)
            out = []
            for row in (data.get("typhoonList") or []):
                if len(row) < 8:
                    continue
                sid, name_en, name_cn, number = row[0], row[1], row[2], row[3]
                status = row[7]
                if status != "start":
                    continue
                out.append({
                    "sid": int(sid),
                    "name_cn": name_cn or "",
                    "name_en": name_en or "",
                    "number": str(number or ""),
                })
            return out
        except Exception as e:
            logger.warning("获取台风列表失败: %s", e)
            return []

    async def _track_points(self, sid: int) -> list[dict]:
        """解析某台风详情，返回路径时次列表（含 BABJ 预报点）。"""
        try:
            client = await self._http()
            resp = await client.get(self._s.typhoon_view_url.format(sid=sid))
            resp.raise_for_status()
            data = _parse_jsonp(resp.text)
            typhoon = data.get("typhoon") or []
            if not typhoon:
                return []
            points = typhoon[8] if len(typhoon) > 8 and isinstance(typhoon[8], list) else []
            parsed = []
            for p in points:
                if not isinstance(p, list) or len(p) < 6:
                    continue
                lon, lat = p[4], p[5]
                if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
                    continue
                parsed.append({
                    "time": p[1] if len(p) > 1 else "",
                    "category": p[3] if len(p) > 3 else "",
                    "lon": float(lon),
                    "lat": float(lat),
                    "wind": float(p[7]) if len(p) > 7 and isinstance(p[7], (int, float)) else 0.0,
                    "hour": 0,
                })
                babj = p[11] if len(p) > 11 and isinstance(p[11], dict) else None
                for fc in (babj.get("BABJ") or []) if babj else []:
                    if not isinstance(fc, list) or len(fc) < 4:
                        continue
                    f_lon, f_lat = fc[2], fc[3]
                    if not isinstance(f_lon, (int, float)) or not isinstance(f_lat, (int, float)):
                        continue
                    parsed.append({
                        "time": fc[1] if len(fc) > 1 else "",
                        "category": fc[7] if len(fc) > 7 else "",
                        "lon": float(f_lon),
                        "lat": float(f_lat),
                        "wind": float(fc[5]) if len(fc) > 5 and isinstance(fc[5], (int, float)) else 0.0,
                        "hour": int(fc[0]) if isinstance(fc[0], (int, float)) else 0,
                    })
            return parsed
        except Exception as e:
            logger.warning("获取台风 %s 路径失败: %s", sid, e)
            return []

    async def impact(self, latitude: float, longitude: float) -> list[TyphoonImpact]:
        """评估所有活跃台风对指定坐标的影响。"""
        storms = await self.active_storms()
        out: list[TyphoonImpact] = []
        for storm in storms:
            pts = await self._track_points(storm["sid"])
            if not pts:
                continue
            best = None
            for p in pts:
                d = distance_km(latitude, longitude, p["lat"], p["lon"])
                if best is None or d < best[0]:
                    best = (d, p)
            if best is None:
                continue
            d, p = best
            cat_cn = CATEGORY_CN.get(p["category"], p["category"] or "热带气旋")
            out.append(TyphoonImpact(
                name_cn=storm["name_cn"] or storm["name_en"] or "无名台风",
                name_en=storm["name_en"] or "",
                number=storm["number"],
                category=p["category"],
                category_cn=cat_cn,
                wind_speed=p["wind"],
                min_distance_km=round(d, 1),
                closest_hour=p["hour"],
                closest_time=p["time"],
                affected=d <= self._s.typhoon_max_range_km,
            ))
        out.sort(key=lambda t: t.min_distance_km)
        return out
