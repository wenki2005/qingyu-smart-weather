"""天气新闻播报稿生成。

把当日的天气、预警、晚霞、赏月、生活指数等信息，组织成一段适合
「播报」的天气新闻稿（主持人口吻），既可在窗口内用浏览器语音合成朗读，
也可附在推送消息中阅读。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..models import DailyWeather, WeatherReport
from ..services.weather import WeatherClient


def build_news_bulletin(report: WeatherReport) -> str:
    """生成当日天气新闻播报稿。"""
    city = report.location
    today: Optional[DailyWeather] = report.daily[0] if report.daily else None
    now = datetime.now()
    date_cn = f"{now.month}月{now.day}日"

    lines: list[str] = []
    lines.append(f"观众朋友们，大家好！欢迎收听《晴屿天气新闻》，今天是{date_cn}。")
    lines.append(f"我们来看{city}今天的天气情况。")

    if today:
        desc, _, emoji = WeatherClient.describe_code(today.weather_code)
        temp_txt = (
            f"{today.temp_min:.0f}到{today.temp_max:.0f}摄氏度"
            if today.temp_min is not None and today.temp_max is not None
            else "气温数据暂缺"
        )
        pop_txt = (
            f"降水概率{today.precip_probability_max:.0f}%"
            if today.precip_probability_max is not None
            else "降水概率未知"
        )
        lines.append(f"今天{city}天气为{emoji}{desc}，气温{temp_txt}，{pop_txt}。")
        lines.append(f"日出时间约在{_fmt(today.sunrise)}，日落时间约在{_fmt(today.sunset)}。")

    if report.air_quality and report.air_quality.aqi is not None:
        aq = report.air_quality
        lines.append(
            f"空气质量方面，当前AQI指数{aq.aqi:.0f}，属于「{aq.level}」"
            + (f"，细颗粒物PM2.5为{aq.pm25:.0f}微克每立方米。" if aq.pm25 is not None else "。")
        )

    if report.warnings:
        lines.append("需要特别提醒的是：")
        for w in report.warnings[:3]:
            lines.append(f"{w.title}，请注意防范。")

    if report.sunset and report.sunset.probability > 0:
        s = report.sunset
        lines.append(
            f"傍晚时分，{city}晚霞观赏概率为{s.probability:.0f}%，{s.level}概率，"
            + (f"日落时间约在{_fmt(s.sunset_time)}。" if s.sunset_time else "请留意西边天空。")
        )
        if s.golden_evening or s.blue_evening:
            windows = []
            if s.golden_evening:
                windows.append(f"金色时刻{s.golden_evening}")
            if s.blue_evening:
                windows.append(f"蓝调时刻{s.blue_evening}")
            lines.append("摄影时段方面：" + "，".join(windows) + "。")

    if report.moon and report.moon.illuminated >= 8:
        m = report.moon
        lines.append(
            f"入夜后，月相为「{m.phase}」，月亮照明度{m.illuminated:.0f}%，"
            f"赏月指数{m.score:.0f}%，{m.level}，最佳赏月时段在{m.best_time or '晚间'}前后。"
        )

    if report.astro_events:
        picks = "；".join(
            f"{a.icon}{a.title}：{a.summary}" for a in report.astro_events[:3]
        )
        lines.append(f"天象方面：{picks}。")
    if report.life_indices:
        top = report.life_indices[:3]
        picks = "；".join(f"{x.icon}{x.name}：{x.level}" for x in top)
        lines.append(f"生活指数方面：{picks}。")

    lines.append("以上就是今天的天气新闻，感谢收听，我们明天再见！")
    return "\n".join(lines)


def _fmt(iso: Optional[str]) -> str:
    if not iso:
        return "待定"
    return iso[-5:]
