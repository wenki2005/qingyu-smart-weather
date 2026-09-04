"""测试中国/国外城市地理编码（weather.com.cn 消歧 + 县级坐标库 + 国外映射）。"""
import httpx
import pytest
import respx

import app.services.cn_geocoder as geo
from app.services.cn_geocoder import (
    _counties,
    _is_cjk,
    _norm_province,
    _strip_city,
    geocode_cn,
)


@pytest.fixture(autouse=True)
def clear_county_cache():
    geo._COUNTIES_INDEX = None
    yield
    geo._COUNTIES_INDEX = None


def test_norm_province():
    assert _norm_province("浙江省") == "浙江"
    assert _norm_province("广东省") == "广东"
    assert _norm_province("广西壮族自治区") == "广西"
    assert _norm_province("内蒙古自治区") == "内蒙古"
    assert _norm_province(None) == ""
    assert _norm_province("浙江") == "浙江"


def test_strip_city():
    assert _strip_city("鹤山市") == "鹤山"
    assert _strip_city("桐乡市") == "桐乡"
    assert _strip_city("青田县") == "青田"
    assert _strip_city("鳌江镇") == "鳌江"
    assert _strip_city("杭州") == "杭州"


def test_is_cjk():
    assert _is_cjk("桐乡")
    assert not _is_cjk("Tokyo")


def test_county_dataset_loaded():
    idx = _counties()
    assert len(idx) > 2000
    # 鹤山市 -> 广东（地级市归属在 c 字段）
    heshan = idx.get(("鹤山", "广东"))
    assert heshan is not None
    assert heshan["c"] == "江门市"
    assert heshan["lat"] != 0
    # 坐标合法性：lat ∈ [-90,90]，lon ∈ [-180,180]（防止经纬度写反）
    bad = [k for k, e in idx.items()
           if not (-90 <= e["lat"] <= 90) or not (-180 <= e["lon"] <= 180)]
    assert not bad, f"坐标非法条目: {bad[:5]}"


@pytest.mark.asyncio
async def test_geocode_cn_uses_county_dataset_for_heshan():
    """鹤山应命中县级库：广东江门市，而不是 Open-Meteo 的广东梅州。"""
    search_payload = '[{"ref":"101281102~guangdong~鹤山~Heshan~鹤山~Heshan~750~529700~HS~广东"}]'
    async with respx.mock:
        respx.get("http://toy1.weather.com.cn/search").mock(
            return_value=httpx.Response(200, text=search_payload)
        )
        client = httpx.AsyncClient()
        try:
            loc = await geo._coords_for(client, {"name": "鹤山", "pinyin": "Heshan",
                                                 "province_cn": "广东", "province_en": "guangdong"})
        finally:
            await client.aclose()
    assert loc is not None
    assert loc.name == "鹤山"
    assert loc.admin1 == "广东"
    assert loc.admin2 == "江门市"


@pytest.mark.asyncio
async def test_coords_for_open_meteo_province_match_when_no_county():
    """县级库没有该名时，Open-Meteo 同省匹配仍可用（青田场景）。"""
    geo._COUNTIES_INDEX = {}
    cand = {"name": "青田", "pinyin": "Qingtian", "province_cn": "浙江", "province_en": "zhejiang"}

    def handler(request):
        q = request.url.params.get("name")
        if q == "青田":
            return httpx.Response(200, json={"results": [
                {"name": "青田", "admin1": "湖南", "admin2": "邵阳市", "country_code": "CN",
                 "latitude": 27.3375, "longitude": 110.89278},
            ]})
        return httpx.Response(200, json={"results": [
            {"name": "Qingtian", "admin1": "浙江", "admin2": "丽水市", "country_code": "CN",
             "latitude": 27.54519, "longitude": 119.45181},
        ]})

    async with respx.mock:
        respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(side_effect=handler)
        client = httpx.AsyncClient()
        try:
            loc = await geo._coords_for(client, cand)
        finally:
            await client.aclose()
    assert loc is not None
    assert loc.name == "青田"
    assert loc.admin1 == "浙江"
    assert abs(loc.latitude - 27.54519) < 1e-4


@pytest.mark.asyncio
async def test_geocode_cn_rejects_unrelated_same_province_result():
    """Open-Meteo 只返回同省但名称无关地点时，不能误定位到该地点。"""
    geo._COUNTIES_INDEX = {}
    search_payload = '[{"ref":"101210304~zhejiang~桐乡~Tongxiang~桐乡~Tongxiang~573~314500~TX~浙江"}]'

    def geo_handler(request):
        q = request.url.params.get("name")
        if q == "Tongxiang":
            return httpx.Response(200, json={"results": [
                {"name": "梧桐", "admin1": "浙江", "admin2": "嘉兴市", "country_code": "CN",
                 "latitude": 30.63287, "longitude": 120.56081},
            ]})
        return httpx.Response(200, json={"results": []})

    async with respx.mock:
        respx.get("http://toy1.weather.com.cn/search").mock(
            return_value=httpx.Response(200, text=search_payload)
        )
        respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(side_effect=geo_handler)
        client = httpx.AsyncClient()
        try:
            loc = await geocode_cn(client, "桐乡")
        finally:
            await client.aclose()
    assert loc is None


@pytest.mark.asyncio
async def test_geocode_cn_foreign_tokyo():
    """东京应命中国外映射 -> 日本东京，而不是黑龙江/江苏同名小镇。"""
    async with respx.mock:
        def handler(request):
            q = request.url.params.get("name")
            if q == "Tokyo":
                return httpx.Response(200, json={"results": [
                    {"name": "東京", "admin1": "东京都", "country": "日本", "country_code": "JP",
                     "latitude": 35.6895, "longitude": 139.69171},
                ]})
            return httpx.Response(200, json={"results": []})
        respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(side_effect=handler)
        client = httpx.AsyncClient()
        try:
            loc = await geocode_cn(client, "东京")
        finally:
            await client.aclose()
    assert loc is not None
    assert loc.country == "日本"
    assert abs(loc.latitude - 35.6895) < 1e-3


@pytest.mark.asyncio
async def test_geocode_cn_foreign_paris_direct():
    """巴黎未在映射表时也能通过 Open-Meteo 非中国结果命中。"""
    geo._COUNTIES_INDEX = {}
    async with respx.mock:
        def handler(request):
            q = request.url.params.get("name")
            if q == "巴黎":
                return httpx.Response(200, json={"results": [
                    {"name": "巴黎", "admin1": "法兰西岛", "country": "法国", "country_code": "FR",
                     "latitude": 48.85341, "longitude": 2.3488},
                ]})
            return httpx.Response(200, json={"results": []})
        respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(side_effect=handler)
        respx.get("http://toy1.weather.com.cn/search").mock(
            return_value=httpx.Response(200, text="()")
        )
        client = httpx.AsyncClient()
        try:
            loc = await geocode_cn(client, "巴黎")
        finally:
            await client.aclose()
    assert loc is not None
    assert loc.country == "法国"



def test_build_location_normalizes_admin_fields():
    loc = geo._build_location(
        {"name": "鹤山", "province_cn": "广东", "code": "101281102"},
        {"latitude": 22.77, "longitude": 112.96, "timezone": "Asia/Shanghai",
         "admin1": "广东省", "admin2": "江门市"},
    )
    assert loc.admin1 == "广东"
    assert loc.admin2 == "江门市"
    assert loc.admin3 == "鹤山"
