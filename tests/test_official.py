"""测试中国天气网官方天气服务（实况 + 今日预报）与解析。"""
import httpx
import pytest
import respx

from app.models import Location
from app.services.official import OfficialWeather, _extract_json


def test_extract_json():
    assert _extract_json("var dataSK={}") == {}
    assert _extract_json('var cityDZ101210304 ={"a":1};') == {"a": 1}
    # 响应尾部还有其它 JS/对象时，只取第一个对象
    assert _extract_json('var cityDZ101210304 ={"a":1};var other={"b":2};') == {"a": 1}
    assert _extract_json("not json") is None


@pytest.mark.asyncio
@respx.mock
async def test_official_fetch_composes():
    loc = Location(name="桐乡", latitude=30.63, longitude=120.56, admin1="浙江",
                   admin2="嘉兴市", wcn_code="101210304")
    sk_text = 'var dataSK={"cityname":"桐乡","temp":"26.3","WD":"东北风","WS":"4级","SD":"78%","time":"18:00"};'
    dz_text = 'var cityDZ101210304 ={"weatherinfo":{"cityname":"桐乡","temp":"27℃","tempn":"24℃","weather":"暴雨","fctime":"202608091800"}};'
    respx.get("http://d1.weather.com.cn/sk_2d/101210304.html").mock(return_value=httpx.Response(200, text=sk_text))
    respx.get("http://d1.weather.com.cn/dingzhi/101210304.html").mock(return_value=httpx.Response(200, text=dz_text))
    svc = OfficialWeather()
    try:
        out = await svc.fetch(loc)
    finally:
        await svc.aclose()
    assert out is not None
    assert out["current"]["temp"] == "26.3"
    assert out["current"]["wind_dir"] == "东北风"
    assert out["current"]["humidity"] == "78%"
    assert out["today"]["weather"] == "暴雨"
    assert out["today"]["temp"] == "27℃"


@pytest.mark.asyncio
async def test_official_fetch_requires_code():
    loc = Location(name="东京", latitude=35.69, longitude=139.69, country="日本")
    svc = OfficialWeather()
    try:
        out = await svc.fetch(loc)   # 无 wcn_code（国外城市）-> None
    finally:
        await svc.aclose()
    assert out is None
