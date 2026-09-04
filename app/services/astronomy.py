"""特殊天文现象播报服务。

提供流星雨、极光（地磁活动）、日月食、超级月亮等特殊天象的观测指数，
数据源全部免费、无需 API Key（与项目其余数据源保持一致）：

- **流星雨**：内置常见流星雨表（活跃期 / 极大日期 / ZHR / 可见半球，数据参考
  Stellarium 开源项目 showers.json 与 IMO 国际流星组织公开资料），
  按**当前地点半球**过滤（北半球 / 南半球），仅展示**临近极大**的流星雨，
  并结合月相照明度与夜间云量修正（月光与云层会掩盖流星）；
- **极光**：优先使用 NOAA SWPC 的 Kp 3 天预报（JSON，免费），
  其次用京都世界资料中心（WDC）实测 Kp 兜底，断网时退化为估算基线；
  仅当**所在纬度可见极光**或出现**强地磁暴（Kp≥6）**时才展示，避免对所有
  纬度无差别推送（思路参考 DarkHours 开源项目：Kp + 纬度评分）；
- **日月食 / 超级月亮**：内置近期重要天象预告表（公开天文日历），
  仅展示**未来 30 天**内的事件，避免一次性堆砌全部预告。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

import httpx

from ..models import AstroEvent, DailyWeather
from .moon import moon_phase

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 流星雨
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MeteorShower:
    """一个流星雨（日期用 (月, 日) 表示，支持跨年活跃期）。

    hemisphere: north=北半球可见 / south=南半球可见 / both=南北半球均可。
    """
    name: str
    eng: str
    start: tuple[int, int]
    peak: tuple[int, int]
    end: tuple[int, int]
    zhr: int          # 天顶每时出现率（极大时的理论值）
    hemisphere: str = "north"
    icon: str = "☄️"


METEOR_SHOWERS: list[MeteorShower] = [
    # ---------- 北半球 ----------
    MeteorShower("象限仪座流星雨", "Quadrantids", (12, 28), (1, 4), (1, 12), 110, "north"),
    MeteorShower("天琴座流星雨", "Lyrids", (4, 14), (4, 22), (4, 30), 18, "north"),
    MeteorShower("英仙座流星雨", "Perseids", (7, 17), (8, 13), (8, 24), 100, "north"),
    MeteorShower("狮子座流星雨", "Leonids", (11, 6), (11, 17), (11, 30), 15, "north"),
    MeteorShower("双子座流星雨", "Geminids", (12, 4), (12, 14), (12, 17), 150, "north"),
    # ---------- 南北半球均可 ----------
    MeteorShower("宝瓶座η流星雨", "Eta Aquariids", (4, 19), (5, 6), (5, 28), 50, "both"),
    MeteorShower("猎户座流星雨", "Orionids", (10, 2), (10, 21), (11, 7), 20, "both"),
    # ---------- 南半球 ----------
    MeteorShower("宝瓶座δ南流星雨", "Southern Delta Aquariids", (7, 12), (7, 28), (8, 23), 25, "south"),
    MeteorShower("船尾座π流星雨", "Pi Puppids", (4, 15), (4, 23), (4, 28), 40, "south"),
    MeteorShower("南鱼座流星雨", "Piscis Austrinids", (6, 15), (7, 28), (8, 10), 5, "south"),
    MeteorShower("矩尺座γ流星雨", "Gamma Normids", (2, 25), (3, 14), (3, 22), 6, "south"),
]


def _md_to_date(md: tuple[int, int], year: int) -> date:
    month, day = md
    return date(year, month, day)


def _days_from_peak(target: date, peak: tuple[int, int]) -> int:
    """目标日期距极大日的天数（peak - target，正数表示极大尚未到来）。"""
    best = None
    for year in (target.year - 1, target.year, target.year + 1):
        delta = (_md_to_date(peak, year) - target).days
        if best is None or abs(delta) < abs(best):
            best = delta
    return best or 0


def _in_window(target: date, start: tuple[int, int], end: tuple[int, int]) -> bool:
    """目标日期是否处于活跃期（支持跨年窗口，如 12-28 ~ 01-12）。"""
    s = _md_to_date(start, target.year)
    e = _md_to_date(end, target.year)
    if s <= e:
        return s <= target <= e
    # 跨年窗口：覆盖 [今年 start, 今年底] ∪ [今年初, 今年 end]
    return target >= s or target <= e


def _hemisphere_ok(sh: MeteorShower, latitude: float) -> bool:
    """按当前地点纬度过滤流星雨：北纬看北半球/双半球，南纬看南半球/双半球。"""
    if latitude >= 0:
        return sh.hemisphere in ("north", "both")
    return sh.hemisphere in ("south", "both")


def _activity_factor(days_from_peak: int) -> float:
    """距极大日期的活跃度 0-1：极大日 1.0，随远离逐步衰减。"""
    d = abs(days_from_peak)
    if d == 0:
        return 1.0
    if d == 1:
        return 0.85
    if d == 2:
        return 0.60
    if d == 3:
        return 0.40
    if d == 4:
        return 0.25
    if d == 5:
        return 0.12
    return 0.05


def _moonlight_factor(illuminated: float) -> float:
    """月光干扰因子 0-1：月亮越亮越难看到流星。"""
    if illuminated >= 90:
        return 0.15
    if illuminated >= 70:
        return 0.30
    if illuminated >= 50:
        return 0.50
    if illuminated >= 25:
        return 0.75
    return 1.0


def _cloud_factor(avg_cloud: Optional[float]) -> float:
    """云量因子 0-1：云越多越难观测；数据缺失时按中等偏乐观处理。"""
    if avg_cloud is None:
        return 0.90
    if avg_cloud >= 85:
        return 0.10
    if avg_cloud >= 70:
        return 0.30
    if avg_cloud >= 50:
        return 0.55
    if avg_cloud >= 30:
        return 0.80
    return 1.0


def _night_cloud(daily: Optional[DailyWeather]) -> Optional[float]:
    """取当晚 20:00-24:00 与次日凌晨 0-3 点的平均云量（若无小时数据返回 None）。"""
    if daily is None or not daily.hours:
        return None
    night = [h.cloud_cover for h in daily.hours if h.time.hour >= 20 or h.time.hour < 3]
    clean = [v for v in night if v is not None]
    return sum(clean) / len(clean) if clean else None


def _level(score: float) -> str:
    if score >= 75:
        return "高"
    if score >= 50:
        return "中"
    return "低"


def meteor_shower_events(
    daily: Optional[DailyWeather],
    target: Optional[date] = None,
    latitude: float = 0.0,
) -> list[AstroEvent]:
    """计算今晚各流星雨的观测指数（活跃度 × 月光 × 云量）。

    只展示：1) 与当前纬度半球匹配；2) 处于活跃期且临近极大（距极大 ≤4 天）。
    避免把尚在活跃期但早已远离极大的流星雨也一并展示。
    """
    target = target or date.today()
    _, illuminated = moon_phase(target)
    cloud = _night_cloud(daily)
    events: list[AstroEvent] = []
    for sh in METEOR_SHOWERS:
        if not _hemisphere_ok(sh, latitude):
            continue
        if not _in_window(target, sh.start, sh.end):
            continue
        days = _days_from_peak(target, sh.peak)
        activity = _activity_factor(days)
        if activity < 0.20:
            continue  # 远离极大（约 >4 天），不单独展示
        score = round(100.0 * activity * _moonlight_factor(illuminated) * _cloud_factor(cloud))
        if score <= 0:
            continue
        peak_dt = _md_to_date(sh.peak, target.year)
        when = _peak_when(days, peak_dt)
        summary = f"活跃期 {_fmt_md(sh.start)}-{_fmt_md(sh.end)}，极大 {_fmt_md(sh.peak)}，ZHR≈{sh.zhr}"
        detail_parts = [f"距极大 {abs(days)} 天" if days != 0 else "正值极大"]
        if illuminated >= 50:
            detail_parts.append(f"月光干扰大（照明度 {illuminated:.0f}%）")
        else:
            detail_parts.append(f"月光干扰小（照明度 {illuminated:.0f}%）")
        if cloud is not None:
            detail_parts.append(f"夜间云量 {cloud:.0f}%")
        events.append(AstroEvent(
            kind="meteor_shower",
            title=sh.name,
            icon=sh.icon,
            when=when,
            score=score,
            level=_level(score),
            summary=summary,
            detail="；".join(detail_parts) + "。",
            source="内置天象表（IMO / Stellarium）",
            best_time="后半夜（辐射点升起后）",
        ))
    events.sort(key=lambda e: e.score, reverse=True)
    return events


def _peak_when(days: int, peak_dt: date) -> str:
    if days == 0:
        return "今晚极大"
    if days == 1:
        return "明晚极大"
    if days == -1:
        return "昨夜极大"
    return f"极大 {peak_dt.month}月{peak_dt.day}日前后"


def _fmt_md(md: tuple[int, int]) -> str:
    return f"{md[0]}月{md[1]}日"


# --------------------------------------------------------------------------- #
# 极光（地磁活动）
# --------------------------------------------------------------------------- #

NOAA_KP_URLS = (
    "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json",
    "https://services.swpc.noaa.gov/json/planetary-k-index-forecast.json",
)
KYOTO_KP_URL = "http://wdc.kugi.kyoto-u.ac.jp/cgi-bin/kp-cgi"
_KP_FORECAST_CACHE: Optional[tuple[float, tuple[float, str]]] = None
_KP_NOW_CACHE: Optional[tuple[float, float]] = None
_KP_FORECAST_TTL = 900.0
_KP_NOW_TTL = 1800.0

# 极光展示阈值：Kp 达到该值即为强地磁暴，即使本地纬度不可见也值得提醒
AURORA_STORM_KP = 6.0


def _kp_text(kp: float) -> str:
    """Kp 等级中文描述（NOAA G 等级惯例）。"""
    if kp >= 8:
        return "极强地磁暴"
    if kp >= 6:
        return "强地磁暴"
    if kp >= 5:
        return "活跃（小地磁暴）"
    if kp >= 4:
        return "较活跃"
    if kp >= 3:
        return "活跃"
    if kp >= 2:
        return "基本平静"
    return "平静"


def aurora_visibility(kp: float, latitude: float) -> tuple[str, str, float]:
    """按 Kp 与纬度估算极光可见性，返回 (状态, 描述, 基准评分 0-100)。

    极光带中心纬度经验值：Kp=0 时约 66.5°N，每升 1 级向南移动约 2°，
    极光带宽度约 ±3~4°。参考 DarkHours 等开源项目的纬度评分思路。
    """
    center = 66.5 - max(0.0, kp) * 2.0
    south_edge = center - 3.0
    north_edge = center + 4.0
    lat = abs(float(latitude))
    hemi = "北纬" if float(latitude) >= 0 else "南纬"
    if lat >= north_edge:
        return "头顶可见", f"{hemi} {north_edge:.0f}° 以外可仰望头顶极光", 95.0
    if lat >= south_edge:
        base = 68.0 + (lat - south_edge) / max(0.1, north_edge - south_edge) * 27.0
        return "天空可见", f"{hemi} {south_edge:.0f}°~{north_edge:.0f}° 的天空可见极光", base
    if lat >= south_edge - 5.0:
        base = 38.0 + (lat - (south_edge - 5.0)) / 5.0 * 30.0
        return "地平线可见", f"{hemi} {south_edge - 5.0:.0f}°~{south_edge:.0f}° 仅在地平线附近可见", base
    return "基本无缘", f"极光带目前在高纬度 {south_edge:.0f}° 附近，你所在纬度难以直接观测", 6.0


def aurora_event(
    kp: Optional[float],
    latitude: float,
    daily: Optional[DailyWeather],
    source_label: str,
    when: str,
) -> AstroEvent:
    """由 Kp + 纬度 + 云量生成极光 AstroEvent。kp 为 None 时给出数据暂缺提示。"""
    if kp is None:
        return AstroEvent(
            kind="aurora",
            title="极光（地磁活动）",
            icon="🌌",
            when=when,
            score=0,
            level="低",
            summary="地磁数据暂缺",
            detail="未能获取 Kp 指数，可稍后刷新重试。",
            source=source_label,
            best_time="午夜前后（地磁活跃时）",
        )
    status, desc, base = aurora_visibility(kp, latitude)
    cloud = _night_cloud(daily)
    cloud_f = _cloud_factor(cloud)
    score = round(base * cloud_f)
    detail = f"{desc}；当前 Kp≈{kp:.1f}（{_kp_text(kp)}）。"
    if status == "基本无缘" and kp >= AURORA_STORM_KP:
        hemi = "北方" if latitude >= 0 else "南方"
        detail = (
            f"发生强地磁暴（Kp≈{kp:.1f}），{hemi}高纬度地区可能看到极光；"
            f"你所在纬度（{abs(latitude):.0f}°）难以直接观测，可关注摄影直播。"
        )
    elif cloud is not None:
        detail += f"夜间云量 {cloud:.0f}%，"
        detail += "晴朗窗口更利于观测。" if cloud < 50 else "云层可能遮挡极光。"
    else:
        detail += "建议关注实时云图。"
    return AstroEvent(
        kind="aurora",
        title="极光（地磁活动）",
        icon="🌌",
        when=when,
        score=min(100, score),
        level=_level(score),
        summary=f"Kp≈{kp:.1f} · {_kp_text(kp)}",
        detail=detail,
        source=source_label,
        best_time="午夜前后（地磁活跃时）",
    )


def _parse_kp_token(tok: str) -> Optional[float]:
    """把京都 WDC 的 Kp 记号（如 3+ / 3- / 4）转为数值。"""
    t = (tok or "").strip()
    if not t:
        return None
    base = t.rstrip("+-")
    try:
        v = float(base)
    except ValueError:
        return None
    if t.endswith("+"):
        v += 0.33
    elif t.endswith("-"):
        v -= 0.33
    return max(0.0, min(9.0, v))


def _parse_noaa_kp(text: str) -> Optional[tuple[float, str]]:
    """解析 NOAA Kp 预报 JSON：[[time, kp, observed, scale], ...]，返回 (最大Kp, 时间)。"""
    try:
        rows = json.loads(text)
    except Exception:
        return None
    best: Optional[tuple[float, str]] = None
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            kp = float(row[1])
        except (TypeError, ValueError):
            continue
        if math.isnan(kp):
            continue
        if best is None or kp > best[0]:
            best = (kp, str(row[0]))
    return best


def _parse_kyoto_kp(text: str) -> Optional[float]:
    """解析京都 WDC 实测 Kp 文本，返回最近 3 日 Kp 峰值。

    京都 WDC 的 Kp 数据为定宽列格式，值之间可能没有空格
    （如 `1+1-2-` 代表 1+、1-、2-），故用正则 `\\d[+-]?` 切分，
    取每行前 8 个令牌作为当日 8 个 3 小时 Kp 值。
    """
    days: list[tuple[date, float]] = []
    for ln in text.splitlines():
        m = re.match(r"^(\d{8})\s+(\S.*)$", ln.strip())
        if not m:
            continue
        try:
            day = date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:8]))
        except ValueError:
            continue
        tokens = re.findall(r"\d[+-]?", m.group(2))
        vals = [_parse_kp_token(t) for t in tokens[:8]]
        vals = [v for v in vals if v is not None]
        if vals:
            days.append((day, max(vals)))
    if not days:
        return None
    recent = days[-3:]
    return round(max(v for _, v in recent), 2)


def estimate_baseline_kp() -> float:
    """断网时的粗略估算：当前处于太阳活动周（第 25 周）峰值后下降期，取中性基线 3。"""
    return 3.0


# --------------------------------------------------------------------------- #
# 近期重要天象预告（日月食 / 超级月亮）
# --------------------------------------------------------------------------- #

UPCOMING_EVENTS: list[tuple[date, str, str, str]] = [
    # (日期, 标题, 图标, 说明)
    (date(2026, 8, 28), "月偏食", "🌑", "本月第二次月食，我国处于白天，难以直接观测，可关注直播与摄影作品。"),
    (date(2026, 11, 24), "超级月亮", "🌕", "11 月满月（海狸月）恰逢近地点，视直径更大、亮度更高。"),
    (date(2026, 12, 24), "超级月亮", "🌕", "12 月满月（冷月）为 2026 年最后一个超级月亮。"),
    (date(2027, 2, 20), "半影月食", "🌘", "月面变暗不明显，肉眼观察效果有限，适合长曝光摄影记录。"),
    (date(2027, 8, 2), "日偏食", "🌞", "部分地区可见日偏食；请务必使用专业减光设备，切勿直视太阳。"),
]


def special_events(target: Optional[date] = None, horizon_days: int = 30) -> list[AstroEvent]:
    """返回近期（默认 30 天内）的日月食 / 超级月亮预告，避免一次性堆砌全部事件。"""
    target = target or date.today()
    events: list[AstroEvent] = []
    for ev_date, title, icon, desc in UPCOMING_EVENTS:
        days = (ev_date - target).days
        if days < 0 or days > horizon_days:
            continue
        if days == 0:
            when, score = "今天", 98
        elif days == 1:
            when, score = "明天", 95
        elif days <= 3:
            when, score = f"{ev_date.month}月{ev_date.day}日", 90
        elif days <= 7:
            when, score = f"{ev_date.month}月{ev_date.day}日", 82
        else:
            when = f"{ev_date.month}月{ev_date.day}日（还有 {days} 天）"
            score = round(max(45, 80 - days * 0.15))
        events.append(AstroEvent(
            kind="eclipse" if "食" in title else "supermoon",
            title=title,
            icon=icon,
            when=when,
            score=score,
            level=_level(score),
            summary=f"{ev_date.year}年{ev_date.month}月{ev_date.day}日 {title}",
            detail=desc,
            source="公开天文日历",
        ))
    return events


# --------------------------------------------------------------------------- #
# 服务入口
# --------------------------------------------------------------------------- #

# 天象卡片最多展示条数（避免一次性堆砌）
MAX_ASTRO_EVENTS = 5


class AstronomyService:
    """天象播报服务：拉取 Kp（NOAA 预报 → 京都实测 → 估算基线）并组装天象列表。"""

    def __init__(self, timeout: float = 8.0):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": "Mozilla/5.0 (qingyu-smart-weather)"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except RuntimeError:
                pass
            self._client = None

    async def fetch_kp_forecast(self) -> Optional[tuple[float, str]]:
        """NOAA SWPC 3 天 Kp 预报：返回 (最大 Kp, 时间)。"""
        global _KP_FORECAST_CACHE
        now = time.monotonic()
        if _KP_FORECAST_CACHE and now - _KP_FORECAST_CACHE[0] < _KP_FORECAST_TTL:
            return _KP_FORECAST_CACHE[1]
        client = await self._http()
        for url in NOAA_KP_URLS:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                parsed = _parse_noaa_kp(resp.text)
                if parsed is not None:
                    _KP_FORECAST_CACHE = (time.monotonic(), parsed)
                    return parsed
            except Exception as e:
                logger.warning("NOAA Kp 预报拉取失败: %s", e)
        return None

    async def fetch_kp_now(self) -> Optional[float]:
        """京都 WDC 实测 Kp：返回最近 3 日峰值。"""
        global _KP_NOW_CACHE
        now = time.monotonic()
        if _KP_NOW_CACHE and now - _KP_NOW_CACHE[0] < _KP_NOW_TTL:
            return _KP_NOW_CACHE[1]
        client = await self._http()
        try:
            today = date.today()
            body = (
                f"SCent={today.year // 100}&STens={(today.year % 100) // 10}&SYear={today.year % 10}"
                f"&From=1&ECent={today.year // 100}&ETens={(today.year % 100) // 10}"
                f"&EYear={today.year % 10}&To=12"
            )
            resp = await client.post(
                KYOTO_KP_URL,
                content=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                return None
            parsed = _parse_kyoto_kp(resp.text)
            if parsed is not None:
                _KP_NOW_CACHE = (time.monotonic(), parsed)
            return parsed
        except Exception as e:
            logger.warning("京都 WDC Kp 拉取失败: %s", e)
            return None

    async def compute(
        self,
        latitude: float,
        daily: list[DailyWeather],
        target: Optional[date] = None,
    ) -> list[AstroEvent]:
        """组装天象播报事件（按地点与事件相关性过滤，最多 MAX_ASTRO_EVENTS 条）。"""
        target = target or date.today()
        events: list[AstroEvent] = []
        today = daily[0] if daily else None

        # 流星雨（本地计算，无需网络；按半球 + 临近极大过滤）
        events.extend(meteor_shower_events(today, target, latitude=latitude))

        # 极光：NOAA 预报优先，京都实测兜底，均失败则用估算基线。
        # 仅当「所在纬度可见」或「发生强地磁暴」时才展示，避免无差别推送。
        kp, when, source = None, "当前", "估算基线"
        forecast = await self.fetch_kp_forecast()
        if forecast is not None:
            kp, when, source = forecast[0], "未来 3 天", "NOAA SWPC 预报"
        else:
            kp_now = await self.fetch_kp_now()
            if kp_now is not None:
                kp, when, source = kp_now, "最近发布", "京都 WDC 实测（可能滞后数周）"
            else:
                kp = estimate_baseline_kp()
                when, source = "当前", "估算基线（未联网）"
        aurora = aurora_event(kp, latitude, today, source, when)
        if aurora.score >= 30 or (kp is not None and kp >= AURORA_STORM_KP):
            events.append(aurora)

        # 日月食 / 超级月亮预告（仅未来 30 天）
        events.extend(special_events(target))

        # 排序：评分高的在前，评分相同按 kind 固定顺序；并限制展示条数
        order = {"meteor_shower": 0, "aurora": 1, "eclipse": 2, "supermoon": 3}
        events.sort(key=lambda e: (-e.score, order.get(e.kind, 9)))
        return events[:MAX_ASTRO_EVENTS]


async def build_astronomy(
    latitude: float,
    daily: list[DailyWeather],
    target: Optional[date] = None,
) -> list[AstroEvent]:
    """便捷入口：创建服务、计算并关闭。失败时返回空列表。"""
    svc = AstronomyService()
    try:
        return await svc.compute(latitude, daily, target)
    except Exception as e:
        logger.warning("天象播报计算失败: %s", e)
        return []
    finally:
        await svc.aclose()
