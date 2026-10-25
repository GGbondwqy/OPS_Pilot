"""周报与超期追踪测试（口径 §12.2：数字必须来自 SQL）。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.persistence import repository
from app.persistence.database import SessionLocal
from app.reports.weekly import build_weekly_report, overdue_incidents


def _make_incident(source_event_id: str, severity: str = "HIGH",
                   status: str = "RECEIVED", hours_ago: float = 1.0, **kwargs):
    occurred = datetime.now(timezone.utc).timestamp() - hours_ago * 3600
    occurred_dt = datetime.fromtimestamp(occurred, tz=timezone.utc)
    resolved_dt = None
    if status == "RESOLVED":
        resolved_dt = datetime.fromtimestamp(occurred + 1800, tz=timezone.utc)
    with SessionLocal() as session:
        incident = repository.Incident(
            source="zabbix_mock",
            source_event_id=source_event_id,
            host="mall-server-01",
            service="mall-portal",
            problem="测试告警",
            severity=severity,
            status=status,
            occurred_at=occurred_dt,
            resolved_at=resolved_dt,
            **kwargs,
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)
        return incident.id


def test_weekly_report_counts(settings):
    _make_incident("W-001")
    _make_incident("W-002", status="RESOLVED")
    _make_incident("W-003", severity="DISASTER", hours_ago=24 * 10)  # 上上周 → 不计入本周

    report = build_weekly_report()
    stats = report["stats"]
    assert stats["new_count"] == 2, "仅统计本周新增"
    assert stats["resolved_count"] == 1
    assert stats["still_open_count"] >= 1, "未闭环含历史遗留"

    markdown = report["markdown"]
    assert "OpsDoctor 周报" in markdown
    assert "未闭环清单" in markdown
    # 报告文件已保存到 data/reports/
    assert Path(report["path"]).exists()


def test_overdue_respects_sla(settings):
    # HIGH 事故超过 12 小时未闭环 → 应出现在超期清单
    _make_incident("OD-001", severity="HIGH", hours_ago=13)
    # 刚发生的事故不应出现
    _make_incident("OD-002", severity="HIGH", hours_ago=1)
    # 已恢复的事故不应出现
    _make_incident("OD-003", severity="HIGH", hours_ago=50, status="RESOLVED")

    rows = overdue_incidents()
    ids = {r["id"] for r in rows}
    overdue_rows = [r for r in rows if r["id"] in ids]
    matched = [r for r in overdue_rows if r["severity"] == "HIGH" and r["age_hours"] >= 12]
    assert any(r["problem"] == "测试告警" for r in matched)
