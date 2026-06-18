"""
当前日期与相对日期对照，供差旅 Agent 解析「下周一」等口语。
"""

from datetime import date, timedelta

WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


def _fmt(d: date) -> str:
    """格式化为 2026年06月17日（星期三）"""
    return f"{d.year}年{d.month:02d}月{d.day:02d}日（{WEEKDAY_NAMES[d.weekday()]}）"


def _this_week_weekday(today: date, weekday: int) -> date:
    """当前自然周内某一天（周一=0）。"""
    return today - timedelta(days=today.weekday()) + timedelta(days=weekday)


def _next_week_weekday(today: date, weekday: int) -> date:
    """下一周某一天：若今天就是该 weekday，则指 7 天后的同一天。"""
    days_ahead = (weekday - today.weekday() + 7) % 7
    if days_ahead == 0:
        return today + timedelta(days=7)
    return today + timedelta(days=days_ahead)


def get_date_context(today: date | None = None) -> str:
    """生成注入 System Prompt 的日期上下文。"""
    today = today or date.today()

    lines = [
        "## 当前日期上下文",
        f"今天是 {_fmt(today)}",
        "",
        "相对日期对照（解析用户口语时必须使用下表，禁止臆造日期）：",
        f"- 今天：{_fmt(today)}",
        f"- 明天：{_fmt(today + timedelta(days=1))}",
        f"- 后天：{_fmt(today + timedelta(days=2))}",
        "",
        "本周：",
    ]
    for i, name in enumerate(WEEKDAY_NAMES):
        lines.append(f"- 本{name}：{_fmt(_this_week_weekday(today, i))}")

    lines.append("")
    lines.append("下周：")
    for i, name in enumerate(WEEKDAY_NAMES):
        lines.append(f"- 下{name}：{_fmt(_next_week_weekday(today, i))}")

    lines.extend(
        [
            "",
            "规则：用户说「下周一」等相对日期时，查上表换算为公历后再调用工具；"
            "向用户确认行程时需写明换算结果（如「下周一 = 2026年06月22日」）。",
        ]
    )
    return "\n".join(lines)
