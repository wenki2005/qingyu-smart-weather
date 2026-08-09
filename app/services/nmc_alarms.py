"""中央气象台在线预警（免费、无需 API Key）。

数据源：中国气象局国家气象中心（NMC）预警发布接口，联网获取当前有效的
台风 / 暴雨 / 冰雹 / 大风 / 雷电 / 高温 等官方预警信号。

- 按省份过滤接口数据，再按地级市（admin2）二次过滤，确保只展示用户所在区域预警
- 等级从预警标题解析（红色 > 橙色 > 黄色 > 蓝色）
"""
from __future__ import annotations

import logging
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
        limit: int = 6,
    ) -> list[WeatherWarning]:
        """拉取指定省/市的当前官方预警。

        province：省级行政区（如 浙江）；city：地级市（如 杭州市），用于二次过滤。
        """
        # 无法确定省份时无法正确限定预警范围：宁可返回空，也不把全国/外省预警推给用户
        if not province:
            return []
        try:
            client = await self._http()
            items: list[dict] = []
            page = 1
            while page <= 6:  # 最多取 6 页（300 条），覆盖省内全部预警
                resp = await client.get(
                    self._s.nmc_alarm_url,
                    params={
                        "pageNo": page,
                        "pageSize": 50,
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
                if page * 50 >= total or not page_data.get("list"):
                    break
                page += 1
            warnings = [self._to_warning(it) for it in items]
            # 只展示用户所在城市的预警（标题含地级市名，含下属区县），
            # 无本市预警时返回空，避免把外市（如茂名）的预警误显示给本市用户。
            if city:
                warnings = [w for w in warnings if city in w.title]
            warnings.sort(key=lambda w: LEVEL_ORDER.get(w.level, 0), reverse=True)
            return warnings[:limit]
        except Exception as e:
            logger.warning("获取中央气象台预警失败: %s", e)
            return []

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
