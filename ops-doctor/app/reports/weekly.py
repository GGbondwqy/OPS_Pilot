"""周报与超期追踪（架构文档 §12.2）。

统计口径：
- 「本周」= 周一 00:00 起、下周一 00:00 止的半开区间 [start, end)；
- 新增/恢复数量来自 SQL，LLM（若启用）只用于润色文字；
- 未闭环问题包括本周新增与历史遗留，不自动清零。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.persistence import repository
from app.persistence.database import SessionLocal


def week_range(week_start_date: str | None = None) -> tuple[datetime, datetime]:
    """计算周区间（按显示时区的自然周，返回 UTC 边界）。"""
    settings = get_settings()
    tz = ZoneInfo(settings.display_tz)
    if week_start_date:
        d = datetime.strptime(week_start_date, "%Y-%m-%d").date()
    else:
        today = datetime.now(tz).date()
        d = today - timedelta(days=today.weekday())  # 本周一
    start_local = datetime(d.year, d.month, d.day, tzinfo=tz)
    end_local = start_local + timedelta(days=7)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def build_weekly_report(week_start: str | None = None) -> dict:
    start, end = week_range(week_start)
    with SessionLocal() as session:
        stats = repository.weekly_stats(session, start, end)
    settings = get_settings()
    tz = ZoneInfo(settings.display_tz)

    def _fmt(dt: datetime) -> str:
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")

    lines = [
        "# OpsDoctor 周报",
        "",
        f"**统计区间**：{_fmt(start)} ~ {_fmt(end)}（{settings.display_tz}，左闭右开）",
        "",
        f"- 新增告警：**{stats['new_count']}** 条",
        f"- 本周恢复：**{stats['resolved_count']}** 条",
        f"- 按严重级：{stats['by_severity'] or '无'}",
        f"- 仍未闭环：**{len(stats['still_open'])}** 条（含历史遗留，不自动清零）",
        "",
    ]
    if stats["still_open"]:
        lines += ["## 未闭环清单", "", "| 事故ID | 源事件 | 服务 | 状态 | 严重级 | 发生时间 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for row in stats["still_open"]:
            occurred = row["occurred_at"]
            if isinstance(occurred, str):
                occurred = datetime.fromisoformat(occurred)
            lines.append(
                f"| {row['id']} | {row['source_event_id']} | {row['service'] or '-'} "
                f"| {row['status']} | {row['severity']} | {_fmt(occurred)} |"
            )
    else:
        lines.append("## 未闭环清单", "")
        lines.append("当前没有未闭环事故。")

    markdown = "\n".join(lines) + "\n"

    settings.ensure_data_dir()
    report_dir = settings.data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    filename = f"weekly-{start.astimezone(tz).strftime('%Y-%m-%d')}.md"
    report_path = report_dir / filename
    report_path.write_text(markdown, encoding="utf-8")

    return {"markdown": markdown, "stats": {
        "new_count": stats["new_count"],
        "resolved_count": stats["resolved_count"],
        "still_open_count": len(stats["still_open"]),
    }, "path": str(report_path)}


REMINDER_RULES = [
    ("DISASTER", 4),   # 高优先级 4 小时未闭环提醒
    ("HIGH", 12),
    ("WARNING", 48),
    ("INFO", 96),
]


def overdue_incidents() -> list[dict]:
    """按 SLA 规则列出未闭环且超时的事故（演示阈值，可按需调整）。"""
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        rows = repository.list_incidents(session, status=None, limit=500)
        result = []
        for r in rows:
            if r.status == "RESOLVED":
                continue
            hours = (now - r.occurred_at).total_seconds() / 3600 if r.occurred_at else 0
            threshold = dict(REMINDER_RULES).get(r.severity, 48)
            if hours >= threshold:
                result.append({
                    "id": r.id, "severity": r.severity, "status": r.status,
                    "service": r.service, "problem": r.problem,
                    "age_hours": round(hours, 1), "sla_hours": threshold,
                })
    return result
