"""测试 IP 定位服务的解析与降级逻辑（mock 网络）。"""
import pytest
import respx
from httpx import Response

from app.services.geolocation import GeoLocator


@pytest.mark.asyncio
@respx.mock
async def test_locate_via_ipinfo():
    respx.get("https://ipinfo.io/json").mock(
        return_value=Response(200, json={
            "city": "Shenzhen",
            "region": "Guangdong",
            "country": "CN",
            "loc": "22.5455,114.0683",
            "timezone": "Asia/Shanghai",
        })
    )
    loc = await GeoLocator(timeout=3).locate()
    assert loc is not None
    assert loc.name == "Shenzhen"
    assert abs(loc.latitude - 22.5455) < 1e-6
    assert abs(loc.longitude - 114.0683) < 1e-6
    assert loc.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
@respx.mock
async def test_locate_fallback_to_geolocation_db():
    # ipinfo 失败 -> 降级 geolocation-db
    respx.get("https://ipinfo.io/json").mock(return_value=Response(500))
    respx.get("https://geolocation-db.com/json/").mock(
        return_value=Response(200, json={
            "city": "Guangzhou",
            "latitude": 23.1167,
            "longitude": 113.25,
            "country_code": "CN",
        })
    )
    loc = await GeoLocator(timeout=3).locate()
    assert loc is not None
    assert loc.name == "Guangzhou"
    assert loc.latitude == 23.1167


@pytest.mark.asyncio
@respx.mock
async def test_locate_all_sources_fail_returns_none():
    respx.get("https://ipinfo.io/json").mock(return_value=Response(500))
    respx.get("https://geolocation-db.com/json/").mock(return_value=Response(500))
    loc = await GeoLocator(timeout=3).locate()
    assert loc is None
