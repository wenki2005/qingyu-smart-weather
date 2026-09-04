"""中央气象台在线预警（免费、无需 API Key）。

数据源：中国气象局国家气象中心（NMC）预警发布接口，联网获取当前有效的
台风 / 暴雨 / 冰雹 / 大风 / 雷电 / 高温 等官方预警信号。

- 按省份过滤接口数据，再按地级市（admin2）二次过滤，确保只展示用户所在区域预警
- 等级从预警标题解析（红色 > 橙色 > 黄色 > 蓝色）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from ..models import WeatherWarning

logger = logging.getLogger(__name__)

LEVEL_ORDER = {"红色": 4, "橙色": 3, "黄色": 2, "蓝色": 1}


class NmcAlarmProvider:
    """NMC 预警提供者。"""

    def __init__(self, settings, timeout: float = 10.0):
        self._s = settings
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except RuntimeError:
                pass
            self._client = None

    async def fetch(
        self,
        province: Optional[str] = None,
        city: Optional[str] = None,
        district: Optional[str] = None,
        limit: int = 6,
        max_age_hours: float = 12.0,
    ) -> list[WeatherWarning]:
        """拉取指定省/市的当前官方预警。

        province：省级行政区；city：地级市；district：区县/县级市。
        max_age_hours：超过该时长且接口没有明确有效期的预警不展示。
        """
        # 无法确定省份时无法正确限定预警范围：宁可返回空，也不把全国/外省预警推给用户
        if not province:
            return []
        try:
            client = await self._http()
            items: list[dict] = []
            page = 1
            page_size = 100
            while page <= 3:  # 只取最新 300 条，避免异常数据拖慢切换
                resp = await client.get(
                    self._s.nmc_alarm_url,
                    params={
                        "pageNo": page,
                        "pageSize": page_size,
                        "signaltype": "",
                        "signallevel": "",
                        "province": province,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                page_data = (data.get("data") or {}).get("page") or {}
                items.extend(page_data.get("list") or [])
                total = page_data.get("count") or 0
                if page * page_size >= total or not page_data.get("list"):
                    break
                page += 1
            warnings = []
            for item in items:
                warning = self._to_warning(item)
                if not self._is_fresh(item, warning.issued_at, max_age_hours):
                    continue
                if self._matches_region(item, warning.title, province, city, district):
                    warnings.append(warning)
            warnings.sort(
                key=lambda w: (LEVEL_ORDER.get(w.level, 0), w.issued_at), reverse=True
            )
            return warnings[:limit]
        except Exception as e:
            logger.warning("获取中央气象台预警失败: %s", e)
            return []

    @staticmethod
    def _clean_region(value: Optional[str]) -> str:
        value = str(value or "").strip()
        # 省级长后缀必须优先处理，否则“广西壮族自治区”会变成“广西壮族”。
        for suffix in (
            "壮族自治区", "回族自治区", "维吾尔自治区", "特别行政区",
            "自治区", "省", "市", "县", "区",
        ):
            if value.endswith(suffix):
                value = value[:-len(suffix)]
                break
        return value

    @classmethod
    def _matches_region(
        cls,
        item: dict,
        title: str,
        province: Optional[str],
        city: Optional[str],
        district: Optional[str],
    ) -> bool:
        """优先使用接口结构化行政区字段，标题仅作最后兜底。"""
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("province", "city", "county", "district", "area", "title")
        ) + " " + title
        province_name = cls._clean_region(province)
        city_name = cls._clean_region(city)
        district_name = cls._clean_region(district)
        if province_name and province_name not in haystack:
            return False
        # 区县级标题可能只有“鹤山市气象台”，也可能写成“江门市鹤山市”；
        # 因此地级市与区县是 OR 关系，不能要求两者同时出现。
        area_hits = [name for name in (city_name, district_name) if name]
        if area_hits and not any(name in haystack for name in area_hits):
            return False
        return bool(province_name or area_hits)

    @staticmethod
    def _parse_issued(value: str) -> Optional[datetime]:
        text = str(value or "").strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None

    @classmethod
    def _is_fresh(cls, item: dict, issued: str, max_age_hours: float) -> bool:
        """过滤明显过期预警；无法解析时间时保留，避免误删官方数据。"""
        now = datetime.now()
        issued_dt = cls._parse_issued(issued)
        if issued_dt is None or max_age_hours <= 0:
            return True
        # 接口若提供结束时间，以结束时间优先。
        for key in ("endtime", "validtime", "expiretime", "valid_until"):
            end_dt = cls._parse_issued(str(item.get(key) or ""))
            if end_dt is not None:
                return end_dt >= now
        return now - issued_dt <= timedelta(hours=max_age_hours)

    @staticmethod
    def _to_warning(item: dict) -> WeatherWarning:
        title = item.get("title", "气象预警")
        level = next((lv for lv in ("红色", "橙色", "黄色", "蓝色") if lv in title), "提示")
        issued = item.get("issuetime", "")
        issued_iso = issued.replace("/", "-")
        desc = f"中央气象台预警信号，发布时间 {issued}。点击查看详情。"
        rel = item.get("url", "")
        url = ("http://www.nmc.cn" + rel) if rel.startswith("/") else (rel or "")
        return WeatherWarning(
            title=title,
            level=level,
            description=desc,
            source="nmc",
            issued_at=issued_iso,
            url=url,
        )
