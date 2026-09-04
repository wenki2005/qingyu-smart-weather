"""测试在线灾害预警数据源：中央气象台预警、台风路径解析与影响判定。"""
import httpx
import pytest
import respx

from app.services.nmc_alarms import NmcAlarmProvider
from app.services.typhoon import NmcTyphoonClient, _parse_jsonp, distance_km


def test_parse_jsonp_list():
    txt = 'typhoon_jsons_list_default(({"typhoonList":[[1,"AAA","甲","2601","2601",null,"a","start"],[2,"BBB",null,"2602","2602",null,"b","stop"]]}))'
    data = _parse_jsonp(txt)
    assert len(data["typhoonList"]) == 2


def test_distance_km():
    assert distance_km(30.0, 120.0, 30.0, 120.0) < 0.1
    d = distance_km(0.0, 0.0, 0.0, 1.0)
    assert 100 < d < 120


@pytest.mark.asyncio
async def test_typhoon_impact_affected(settings):
    client = NmcTyphoonClient(settings)

    async def fake_active():
        return [{"sid": 1, "name_cn": "灿鸿", "name_en": "CHAN-HOM", "number": "2615"}]

    async def fake_track(sid):
        return [
            {"time": "202608090600", "category": "TY", "lon": 121.0, "lat": 30.0, "wind": 33.0, "hour": 0},
            {"time": "202608101400", "category": "TY", "lon": 122.0, "lat": 31.0, "wind": 33.0, "hour": 24},
        ]

    client.active_storms = fake_active
    client._track_points = fake_track
    impacts = await client.impact(30.29, 120.16)  # 杭州
    assert len(impacts) == 1
    assert impacts[0].affected is True
    assert impacts[0].min_distance_km < 100
    assert impacts[0].category_cn == "台风"
    await client.aclose()


@pytest.mark.asyncio
async def test_typhoon_impact_not_affected(settings):
    client = NmcTyphoonClient(settings)

    async def fake_active():
        return [{"sid": 2, "name_cn": "皮拉", "name_en": "PEILOU", "number": "2616"}]

    async def fake_track(sid):
        return [{"time": "202608090600", "category": "TS", "lon": 142.6, "lat": 21.2, "wind": 18.0, "hour": 0}]

    client.active_storms = fake_active
    client._track_points = fake_track
    impacts = await client.impact(30.29, 120.16)
    assert len(impacts) == 1
    assert impacts[0].affected is False
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nmc_alarm_fetch_filters_by_city(settings):
    payload = {
        "msg": "success", "code": 0,
        "data": {"page": {"pageNo": 1, "pageSize": 50, "count": 3, "next": 0, "list": [
            {"alertid": "1", "issuetime": "2026/08/09 16:57",
             "title": "浙江省杭州市萧山区气象台发布台风橙色预警信号", "pic": "p0001002.png",
             "url": "/publish/alarm/43112341600000_20260809165718.html"},
            {"alertid": "2", "issuetime": "2026/08/09 16:54",
             "title": "浙江省杭州市桐庐县气象台发布暴雨红色预警信号", "pic": "p0002001.png",
             "url": "/publish/alarm/22222222222222_20260809165400.html"},
            {"alertid": "3", "issuetime": "2026/08/09 16:50",
             "title": "浙江省宁波市海曙区气象台发布大风蓝色预警信号", "pic": "p0005004.png"},
        ]}},
    }
    respx.get(settings.nmc_alarm_url).mock(return_value=httpx.Response(200, json=payload))
    provider = NmcAlarmProvider(settings)
    warns = await provider.fetch("浙江", "杭州市", max_age_hours=0)
    assert len(warns) == 2
    assert warns[0].level == "红色"          # 暴雨红 > 台风橙
    assert warns[0].source == "nmc"
    assert warns[0].issued_at == "2026-08-09 16:54"
    assert warns[0].url.startswith("http://www.nmc.cn/publish/alarm/")
    assert all("宁波" not in w.title for w in warns)
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nmc_alarm_no_city_hits_returns_empty(settings):
    payload = {
        "msg": "success", "code": 0,
        "data": {"page": {"pageNo": 1, "pageSize": 50, "count": 2, "next": 0, "list": [
            {"alertid": "1", "issuetime": "2026/08/09 16:50",
             "title": "广东省茂名市电白区气象台发布暴雨橙色预警信号", "pic": "p0002002.png"},
        ]}},
    }
    respx.get(settings.nmc_alarm_url).mock(return_value=httpx.Response(200, json=payload))
    provider = NmcAlarmProvider(settings)
    warns = await provider.fetch("广东", "深圳市", max_age_hours=0)
    assert warns == []          # 外市预警不展示
    await provider.aclose()


@pytest.mark.asyncio
async def test_nmc_alarm_requires_province(settings):
    """无省份信息时不查询 NMC（避免把全国/外省预警误推给用户）。"""
    provider = NmcAlarmProvider(settings)
    warns = await provider.fetch("", "")
    assert warns == []
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nmc_alarm_matches_county_when_city_name_missing(settings):
    """区县预警标题只写区县名时，也不能漏掉当前地点。"""
    payload = {
        "data": {"page": {"pageNo": 1, "pageSize": 100, "count": 1, "list": [
            {"alertid": "1", "issuetime": "2026/09/04 10:00",
             "title": "广东省鹤山市气象台发布暴雨黄色预警信号"},
        ]}},
    }
    respx.get(settings.nmc_alarm_url).mock(return_value=httpx.Response(200, json=payload))
    provider = NmcAlarmProvider(settings)
    warns = await provider.fetch("广东", "江门市", "鹤山市", max_age_hours=0)
    assert len(warns) == 1
    await provider.aclose()


def test_nmc_region_suffix_normalization():
    assert NmcAlarmProvider._clean_region("广西壮族自治区") == "广西"
    assert NmcAlarmProvider._clean_region("香港特别行政区") == "香港"
    assert NmcAlarmProvider._clean_region("江门市") == "江门"


def test_nmc_freshness_filter():
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    old = "2020-01-01 00:00"
    assert NmcAlarmProvider._is_fresh({}, now, 12)
    assert not NmcAlarmProvider._is_fresh({}, old, 12)
    assert NmcAlarmProvider._is_fresh({}, old, 0)



def test_typhoon_radius_depends_on_strength():
    from app.services.typhoon import impact_radius_km
    assert impact_radius_km("TD", 15.0) < impact_radius_km("TY", 35.0)
    assert impact_radius_km("TD", 15.0) <= 300
    assert impact_radius_km("SuperTY", 50.0) <= 700
