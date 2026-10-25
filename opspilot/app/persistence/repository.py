"""数据访问层：所有 SQL 集中在这里，业务模块不直接拼查询。

关键约定：
- 状态迁移必须走 try_transition（条件更新，防并发状态倒退）；
- 同一 (source, source_event_id, event_kind, payload_hash) 的回调只入库一次；
- 时间统一存 UTC。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.persistence.database import SessionLocal
from app.persistence.models import (
    ALLOWED_TRANSITIONS,
    ActionRun,
    Approval,
    Incident,
    InboundEvent,
    Notification,
    Investigation,
    utcnow,
)

logger = logging.getLogger(__name__)

APPROVAL_TTL_MINUTES = 30


# ---------- 状态机 ----------

def try_transition(session, incident_id: int, expected: str, new: str) -> bool:
    """条件更新：只有当前状态等于 expected 才允许迁移，防止状态倒退。"""
    if new not in ALLOWED_TRANSITIONS.get(expected, set()):
        logger.warning("illegal transition %s -> %s (incident %s)", expected, new, incident_id)
        return False
    values: dict = {"status": new}
    if new == "RESOLVED":
        values["resolved_at"] = utcnow()
    rowcount = (
        session.execute(
            update(Incident)
            .where(Incident.id == incident_id, Incident.status == expected)
            .values(**values)
        ).rowcount
    )
    session.commit()
    return rowcount == 1


# ---------- 告警接入 ----------

def payload_hash(raw: dict) -> str:
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_inbound_event(session, *, source: str, source_event_id: str,
                         event_kind: str, raw_payload: dict) -> tuple[InboundEvent, bool]:
    """写入原始回调。返回 (row, created)；重复载荷 created=False。"""
    h = payload_hash(raw_payload)
    existing = session.execute(
        select(InboundEvent).where(
            InboundEvent.source == source,
            InboundEvent.source_event_id == source_event_id,
            InboundEvent.event_kind == event_kind,
            InboundEvent.payload_hash == h,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    redacted = {k: v for k, v in raw_payload.items() if k.lower() not in {"token", "password", "secret"}}
    row = InboundEvent(
        source=source,
        source_event_id=source_event_id,
        event_kind=event_kind,
        payload_hash=h,
        payload_redacted=json.dumps(redacted, ensure_ascii=False, default=str),
    )
    session.add(row)
    session.commit()
    return row, True


def find_incident(session, source: str, source_event_id: str) -> Incident | None:
    return session.execute(
        select(Incident).where(
            Incident.source == source, Incident.source_event_id == source_event_id
        )
    ).scalar_one_or_none()


def create_incident_from_alert(session, alert) -> Incident:
    """由告警契约创建主事件。调用方保证已做去重判断。"""
    incident = Incident(
        source=alert.source,
        source_event_id=alert.event_id,
        host=alert.host,
        service=alert.service or "",
        problem=alert.problem,
        severity=alert.severity,
        metric=alert.metric or "",
        value=alert.value,
        threshold=alert.threshold,
        tags_json=json.dumps(alert.tags or {}, ensure_ascii=False),
        occurred_at=alert.occurred_at,
        status="RECEIVED",
    )
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident


# ---------- 查询 ----------

def get_incident(session, incident_id: int) -> Incident | None:
    return session.get(Incident, incident_id)


def list_incidents(session, *, status: str | None = None, limit: int = 50, offset: int = 0) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.id.desc()).limit(limit).offset(offset)
    if status:
        stmt = stmt.where(Incident.status == status)
    return list(session.execute(stmt).scalars())


def list_investigations(session, incident_id: int) -> list[Investigation]:
    return list(session.execute(
        select(Investigation).where(Investigation.incident_id == incident_id)
        .order_by(Investigation.id.desc())
    ).scalars())


def list_approvals(session, incident_id: int | None = None, status: str | None = None) -> list[Approval]:
    stmt = select(Approval).order_by(Approval.id.desc())
    if incident_id is not None:
        stmt = stmt.where(Approval.incident_id == incident_id)
    if status:
        stmt = stmt.where(Approval.status == status)
    return list(session.execute(stmt).scalars())


def list_action_runs(session, incident_id: int) -> list[ActionRun]:
    return list(session.execute(
        select(ActionRun).where(ActionRun.incident_id == incident_id).order_by(ActionRun.id)
    ).scalars())


def list_notifications(session, incident_id: int) -> list[Notification]:
    return list(session.execute(
        select(Notification).where(Notification.incident_id == incident_id).order_by(Notification.id)
    ).scalars())


# ---------- 调查 ----------

def create_investigation(session, *, incident_id: int, model: str, investigator: str,
                         diagnosis_status: str, evidence: dict, conclusion: str,
                         uncertainty: str, recommended_action_key: str = "",
                         requires_approval: bool = False) -> Investigation:
    row = Investigation(
        incident_id=incident_id,
        model=model,
        investigator=investigator,
        diagnosis_status=diagnosis_status,
        evidence_json=json.dumps(evidence, ensure_ascii=False, default=str),
        conclusion=conclusion,
        uncertainty=uncertainty,
        recommended_action_key=recommended_action_key,
        requires_approval=1 if requires_approval else 0,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ---------- 审批 ----------

def create_approval(session, *, incident_id: int, action_key: str, target: str,
                    requester: str = "opspilot-agent") -> Approval:
    row = Approval(
        incident_id=incident_id,
        action_key=action_key,
        target=target,
        requester=requester,
        status="PENDING",
        expires_at=utcnow() + timedelta(minutes=APPROVAL_TTL_MINUTES),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_approval(session, approval_id: int) -> Approval | None:
    return session.get(Approval, approval_id)


def decide_approval(session, approval_id: int, decision: str, approver: str) -> Approval | None:
    row = session.get(Approval, approval_id)
    if row is None or row.status != "PENDING":
        return None
    row.status = "APPROVED" if decision == "approve" else "REJECTED"
    row.approver = approver
    row.decided_at = utcnow()
    # 审批决策联动事故状态（条件更新，仅从 NEEDS_APPROVAL 推进）
    new_incident_status = "APPROVED" if decision == "approve" else "ESCALATED"
    session.execute(
        update(Incident)
        .where(Incident.id == row.incident_id, Incident.status == "NEEDS_APPROVAL")
        .values(status=new_incident_status)
    )
    session.commit()
    session.refresh(row)
    return row


# ---------- 动作执行 ----------

def create_action_run(session, *, incident_id: int, approval_id: int | None,
                      action_key: str, target: str, result: str, detail: dict | str) -> ActionRun:
    row = ActionRun(
        incident_id=incident_id,
        approval_id=approval_id,
        action_key=action_key,
        target=target,
        result=result,
        detail=json.dumps(detail, ensure_ascii=False, default=str) if isinstance(detail, dict) else detail,
        finished_at=utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ---------- 通知 ----------

def record_notification(session, *, incident_id: int, channel: str, state: str,
                        subject: str, summary: str, dedupe_key: str,
                        payload: str, status: str = "sent") -> tuple[Notification, bool]:
    existing = session.execute(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = Notification(
        incident_id=incident_id,
        channel=channel,
        state=state,
        subject=subject,
        summary=summary,
        dedupe_key=dedupe_key,
        status=status,
        payload_redacted=payload,
        sent_at=utcnow() if status == "sent" else None,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row, True


# ---------- 周报统计（§12.2：数字全部来自 SQL，不做任何凭空推断） ----------

def weekly_stats(session, week_start: datetime, week_end: datetime) -> dict:
    new_count = session.execute(
        select(func.count(Incident.id)).where(
            Incident.occurred_at >= week_start, Incident.occurred_at < week_end)
    ).scalar() or 0

    resolved_in_week = session.execute(
        select(func.count(Incident.id)).where(
            Incident.resolved_at.is_not(None),
            Incident.resolved_at >= week_start,
            Incident.resolved_at < week_end)
    ).scalar() or 0

    open_rows = session.execute(
        select(Incident).where(Incident.status != "RESOLVED").order_by(Incident.occurred_at)
    ).scalars().all()

    sev_rows = session.execute(
        select(Incident.severity, func.count(Incident.id)).where(
            Incident.occurred_at >= week_start, Incident.occurred_at < week_end
        ).group_by(Incident.severity)
    ).all()

    return {
        "week_start": week_start,
        "week_end": week_end,
        "new_count": int(new_count),
        "resolved_count": int(resolved_in_week),
        "still_open": [
            {"id": r.id, "source_event_id": r.source_event_id, "service": r.service,
             "status": r.status, "severity": r.severity, "occurred_at": r.occurred_at}
            for r in open_rows
        ],
        "by_severity": {sev: int(c) for sev, c in sev_rows},
    }


def latest_investigation(session, incident_id: int) -> Investigation | None:
    return session.execute(
        select(Investigation).where(Investigation.incident_id == incident_id)
        .order_by(Investigation.id.desc()).limit(1)
    ).scalar_one_or_none()
