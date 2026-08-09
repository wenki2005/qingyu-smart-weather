"""中国城市 / 国外城市地理编码（免费、无需 Key）。

问题背景：
- Open-Meteo 的 geocoding 索引对中国县城/县级市覆盖不全、歧义名排序不佳
  （如「桐乡」「平遥」查不到，「青田」命中湖南同名地点、「鹤山」命中广东梅州）；
- Open-Meteo 中文索引对国外城市覆盖差（「东京」命中江苏/浙江同名小镇，没有日本东京）。

解决方案（多级兜底）：
1. 国外城市：内置常见世界城市「中文名 -> 英文名」映射，用 Open-Meteo 英文名解析；
2. 国内县级市/县：weather.com.cn 城市搜索消歧（权威、覆盖全国县市）得到正确
   城市名 + 省份，再优先查**内置县级行政区划坐标库**（app/data/china_counties.json，
   DataV 质心，含县/县级市及其地级市归属）；
3. 县级库没有的：Open-Meteo 按「名称 / 去后缀 / 拼音」查询，取 country=CN 且
   admin1 与 weather.com.cn 省份一致的结果；
4. weather.com.cn 不可用时回退 Open-Meteo 原生地理编码。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import httpx

from ..models import Location

logger = logging.getLogger(__name__)

WCN_SEARCH_URL = "http://toy1.weather.com.cn/search"
OM_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

_PROV_SUFFIXES = (
    "壮族自治区", "回族自治区", "维吾尔自治区", "特别行政区",
    "自治区", "省", "市",
)
_CITY_SUFFIXES = ("市", "县", "区", "镇", "街道")

# 常见世界城市：中文名 -> Open-Meteo 英文查询名
FOREIGN_CITIES: dict[str, str] = {
    "东京": "Tokyo", "东京都": "Tokyo", "大阪": "Osaka", "京都": "Kyoto",
    "名古屋": "Nagoya", "札幌": "Sapporo", "福冈": "Fukuoka", "冲绳": "Okinawa",
    "横滨": "Yokohama", "神户": "Kobe", "广岛": "Hiroshima",
    "首尔": "Seoul", "釜山": "Busan", "平壤": "Pyongyang",
    "纽约": "New York City", "洛杉矶": "Los Angeles", "旧金山": "San Francisco",
    "华盛顿": "Washington, D.C.", "芝加哥": "Chicago", "波士顿": "Boston",
    "西雅图": "Seattle", "迈阿密": "Miami", "休斯顿": "Houston",
    "拉斯维加斯": "Las Vegas", "多伦多": "Toronto", "温哥华": "Vancouver",
    "蒙特利尔": "Montreal", "墨西哥城": "Mexico City",
    "伦敦": "London", "巴黎": "Paris", "柏林": "Berlin", "罗马": "Rome",
    "马德里": "Madrid", "巴塞罗那": "Barcelona", "米兰": "Milan",
    "维也纳": "Vienna", "阿姆斯特丹": "Amsterdam", "布鲁塞尔": "Brussels",
    "苏黎世": "Zurich", "日内瓦": "Geneva", "里斯本": "Lisbon",
    "斯德哥尔摩": "Stockholm", "奥斯陆": "Oslo", "哥本哈根": "Copenhagen",
    "赫尔辛基": "Helsinki", "都柏林": "Dublin", "布拉格": "Prague",
    "布达佩斯": "Budapest", "华沙": "Warsaw", "雅典": "Athens",
    "伊斯坦布尔": "Istanbul", "莫斯科": "Moscow", "圣彼得堡": "Saint Petersburg",
    "基辅": "Kyiv", "迪拜": "Dubai", "阿布扎比": "Abu Dhabi",
    "多哈": "Doha", "特拉维夫": "Tel Aviv", "耶路撒冷": "Jerusalem",
    "开罗": "Cairo", "悉尼": "Sydney", "墨尔本": "Melbourne",
    "堪培拉": "Canberra", "奥克兰": "Auckland", "惠灵顿": "Wellington",
    "新加坡": "Singapore", "曼谷": "Bangkok", "吉隆坡": "Kuala Lumpur",
    "雅加达": "Jakarta", "马尼拉": "Manila", "河内": "Hanoi",
    "胡志明市": "Ho Chi Minh City", "仰光": "Yangon", "金边": "Phnom Penh",
    "孟买": "Mumbai", "新德里": "New Delhi", "德里": "Delhi",
    "班加罗尔": "Bangalore", "加尔各答": "Kolkata", "卡拉奇": "Karachi",
    "德黑兰": "Tehran", "利雅得": "Riyadh", "圣保罗": "Sao Paulo",
    "里约热内卢": "Rio de Janeiro", "布宜诺斯艾利斯": "Buenos Aires",
    "圣地亚哥": "Santiago", "利马": "Lima", "波哥大": "Bogota",
    "约翰内斯堡": "Johannesburg", "开普敦": "Cape Town", "内罗毕": "Nairobi",
    "台北": "Taipei", "台中": "Taichung", "高雄": "Kaohsiung",
    "香港": "Hong Kong", "澳门": "Macau",
}

_COUNTIES_INDEX: Optional[dict] = None


def _norm_province(s: Optional[str]) -> str:
    """规范化省份名：去「省/市/自治区」等后缀。"""
    if not s:
        return ""
    s = str(s).strip()
    for suf in _PROV_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s


def _strip_city(name: str) -> str:
    """去掉城市名后缀（市/县/区/镇/街道）。"""
    name = str(name).strip()
    for suf in _CITY_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def _is_cjk(text: str) -> bool:
    """是否含中文汉字。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _counties() -> dict:
    """加载县级坐标库索引：{(去后缀名, 省份): {name, p, c, lat, lon}}。"""
    global _COUNTIES_INDEX
    if _COUNTIES_INDEX is None:
        idx: dict = {}
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "data", "china_counties.json")
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for e in data.get("counties", []):
                key = (_strip_city(e["name"]), _norm_province(e.get("p")))
                if key not in idx:
                    idx[key] = e
        except Exception as exc:
            logger.warning("加载县级坐标库失败: %s", exc)
        _COUNTIES_INDEX = idx
    return _COUNTIES_INDEX


async def _om_search(client: httpx.AsyncClient, name: str, count: int = 10) -> list[dict]:
    try:
        resp = await client.get(OM_GEO_URL, params={
            "name": name, "count": count, "language": "zh", "format": "json",
        })
        resp.raise_for_status()
        return resp.json().get("results") or []
    except Exception as e:
        logger.debug("Open-Meteo 查询 %s 失败: %s", name, e)
        return []


def _from_om(r: dict, fallback_name: str) -> Location:
    return Location(
        name=r.get("name") or fallback_name,
        latitude=r["latitude"],
        longitude=r["longitude"],
        timezone=r.get("timezone") or "Asia/Shanghai",
        country=r.get("country"),
        admin1=r.get("admin1"),
        admin2=r.get("admin2"),
    )


async def geocode_cn(client: httpx.AsyncClient, query: str) -> Optional[Location]:
    """中文查询 -> 精确定位（国外映射 / 国内 weather.com.cn + 县级库）。"""
    q = query.strip()

    # 1) 国外城市：内置映射 -> Open-Meteo 英文名
    en = FOREIGN_CITIES.get(q) or FOREIGN_CITIES.get(_strip_city(q))
    if en:
        for r in await _om_search(client, en, count=5):
            if (r.get("country_code") or "").upper() != "CN":
                return _from_om(r, q)

    # 2) 国内：weather.com.cn 消歧
    try:
        candidates = await search_weather_com(client, q)
    except Exception as e:
        logger.warning("weather.com.cn 城市搜索失败: %s", e)
        candidates = []
    if not candidates and _strip_city(q) != q:
        # 「鹤山市」等带后缀查询无结果时，用去后缀名重试（如「鹤山」）
        try:
            candidates = await search_weather_com(client, _strip_city(q))
        except Exception:
            candidates = []
    for cand in candidates[:5]:
        loc = await _coords_for(client, cand)
        if loc is not None:
            logger.info("中文地理编码：%s -> %s/%s (%.4f, %.4f)",
                        q, loc.name, loc.admin1, loc.latitude, loc.longitude)
            return loc

    # 3) 兜底：Open-Meteo 直接查（可命中非中国城市，如「巴黎」）
    for r in await _om_search(client, q, count=10):
        if (r.get("country_code") or "").upper() != "CN":
            return _from_om(r, q)
    return None


async def search_weather_com(client: httpx.AsyncClient, query: str) -> list[dict]:
    """weather.com.cn 城市搜索，返回候选列表。"""
    resp = await client.get(
        WCN_SEARCH_URL,
        params={"cityname": query, "_": str(int(time.time() * 1000))},  # 时间戳防 CDN 缓存空响应
        headers={"User-Agent": "Mozilla/5.0", "Referer": "http://www.weather.com.cn/"},
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    data = json.loads(text) if text.startswith("[") or text.startswith("{") else []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for row in data:
        ref = (row or {}).get("ref", "")
        parts = ref.split("~")
        if len(parts) < 10:
            continue
        out.append({
            "code": parts[0],
            "province_en": parts[1],
            "name": parts[2],
            "pinyin": parts[3],
            "province_cn": parts[9],
        })
    return out


def _county_location(cand: dict, e: dict) -> Location:
    province_cn = cand.get("province_cn") or e.get("p") or ""
    return Location(
        name=cand["name"],
        latitude=e["lat"],
        longitude=e["lon"],
        timezone="Asia/Shanghai",
        country="中国",
        admin1=_norm_province(province_cn) or e.get("p", ""),
        admin2=e.get("c") if e.get("c") and e.get("c") != e.get("name") else None,
        wcn_code=cand.get("code") or None,
    )


async def _coords_for(client: httpx.AsyncClient, cand: dict) -> Optional[Location]:
    """为 weather.com.cn 候选确定坐标：县级库优先，其次 Open-Meteo 同省匹配。"""
    name = cand["name"]
    target_prov = _norm_province(cand.get("province_cn") or "")

    # 1) 内置县级坐标库：仅用于县级条目（有独立地级市归属 c != name）。
    #    地级市/直辖市（c == name）用整体质心偏差大，交给 Open-Meteo 取城区中心。
    idx = _counties()
    for n in (name, name + "市", name + "县", name + "区", name + "镇"):
        e = idx.get((_strip_city(n), target_prov))
        if e is not None and e.get("c") != e.get("name"):
            return _county_location(cand, e)

    # 2) Open-Meteo 同省匹配
    pinyin = cand["pinyin"]
    bare = _strip_city(name)
    targets = [
        name, bare,
        f"{name}市", f"{name}县", f"{name}区", f"{name}镇",
        f"{bare}市", f"{bare}县", f"{bare}区", f"{bare}镇",
        pinyin, f"{pinyin}shi", f"{pinyin}xian", f"{pinyin}zhen",
    ]
    seen: set[str] = set()
    for q in targets:
        q = q.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        for r in await _om_search(client, q):
            if (r.get("country_code") or "").upper() != "CN":
                continue
            if target_prov and _norm_province(r.get("admin1")) != target_prov:
                continue
            return _build_location(cand, r)
    return None


def _build_location(cand: dict, om: dict) -> Location:
    """weather.com.cn 权威名 + Open-Meteo 坐标组装 Location。"""
    return Location(
        name=cand["name"],
        latitude=om["latitude"],
        longitude=om["longitude"],
        timezone=om.get("timezone") or "Asia/Shanghai",
        country="中国",
        admin1=cand.get("province_cn") or om.get("admin1"),
        admin2=om.get("admin2") or om.get("admin1"),
        wcn_code=cand.get("code") or None,
    )
