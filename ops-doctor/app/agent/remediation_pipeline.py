"""执行与复验流水线（架构文档 §8.3/§9.3）。

顺序：APPROVED → can_execute 二次校验 → EXECUTING → 执行动作
     → VERIFYING → 健康检查 + 业务指标复验 → RESOLVED / ESCALATED。

核心原则：
- 命令执行成功 ≠ 恢复；必须独立复验；
- 假恢复（进程起了但业务未恢复）必须被拦截并升级；
- 恢复判定依据：服务健康 + 队列积压回落到阈值以下 + （监控恢复事件，若有）。
"""

from __future__ import annotations

import logging

from app.adapters import get_monitoring_port
from app.config import get_settings
from app.notifications import notify
from app.persistence import repository
from app.persistence.database import SessionLocal
from app.policy.rules import ApprovalView, can_execute
from app.remediation.actions import execute_action

logger = logging.getLogger(__name__)


def execute_approved_action(approval_id: int) -> dict:
    """对已批准的审批单执行动作并复验。worker 与手动触发共用。"""
    refused: dict | None = None
    with SessionLocal() as session:
        approval = repository.get_approval(session, approval_id)
        if approval is None:
            return {"ok": False, "error": f"approval {approval_id} not found"}
        incident_id = approval.incident_id
        incident = repository.get_incident(session, incident_id)
        action_key, target = approval.action_key, approval.target
        incident_metric = incident.metric if incident else ""
        incident_threshold = incident.threshold if incident else None

        if approval.status != "APPROVED":
            return {"ok": False, "error": f"approval {approval_id} status={approval.status}，未批准"}

        # 执行前二次校验（策略纯函数）
        view = ApprovalView(
            status=approval.status, action_key=approval.action_key,
            target=approval.target, requester=approval.requester,
            approver=approval.approver, expires_at=approval.expires_at,
        )
        allowed, reason = can_execute(action_key, target, view, get_settings().run_environment)
        if not allowed:
            repository.create_action_run(
                session, incident_id=incident_id, approval_id=approval_id,
                action_key=action_key, target=target, result="REFUSED",
                detail={"reason": reason},
            )
            approval.status = "REFUSED"  # 消费掉审批单，防止 worker 反复重试
            repository.try_transition(session, incident_id, "APPROVED", "ESCALATED")
            logger.warning("action refused: %s", reason)
            refused = {"ok": False, "refused": True, "reason": reason, "incident_id": incident_id}
        else:
            # 立即消费审批单：一次批准只允许执行一次
            approval.status = "EXECUTED"
            session.commit()

    if refused is not None:
        notify(
            incident_id, "ESCALATED",
            f"[OpsDoctor] 动作被安全策略拒绝 #{incident_id}",
            f"动作 {action_key}（目标 {target}）被拒绝：{refused['reason']}。已转人工处理。",
            evidence_refs=[f"approval:{approval_id}"],
        )
        return refused

    # EXECUTING
    with SessionLocal() as session:
        moved = repository.try_transition(session, incident_id, "APPROVED", "EXECUTING")
    if not moved:
        return {"ok": False, "error": f"incident {incident_id} 不在 APPROVED 状态"}

    result = execute_action(action_key, target)
    with SessionLocal() as session:
        repository.create_action_run(
            session, incident_id=incident_id, approval_id=approval_id,
            action_key=action_key, target=target,
            result="OK" if result.get("executed") else "FAILED",
            detail=result,
        )
        repository.try_transition(session, incident_id, "EXECUTING", "VERIFYING")

    # VERIFYING：独立复验，不信任执行返回值
    verification = verify_recovery(incident_id, incident_metric, incident_threshold)
    with SessionLocal() as session:
        if verification["healthy"]:
            repository.try_transition(session, incident_id, "VERIFYING", "RESOLVED")
            final = "RESOLVED"
        else:
            repository.try_transition(session, incident_id, "VERIFYING", "ESCALATED")
            final = "ESCALATED"

    notify(
        incident_id, final,
        f"[OpsDoctor] 事故 #{incident_id} {'已恢复' if final == 'RESOLVED' else '执行后未恢复，已升级人工'}",
        verification["summary"],
        evidence_refs=[f"approval:{approval_id}", f"action_run:{action_key}"],
    )
    return {"ok": True, "incident_id": incident_id, "state": final, "verification": verification}


def verify_recovery(incident_id: int, metric: str = "", threshold: float | None = None) -> dict:
    """恢复复验：应用健康 + 业务指标（队列积压）+ 监控对账信息。"""
    port = get_monitoring_port()
    checks: list[dict] = []

    with SessionLocal() as session:
        incident = repository.get_incident(session, incident_id)
        service = (incident.service if incident else "") or ""
        host = incident.host if incident else ""
        threshold = threshold if threshold is not None else (
            incident.threshold if incident else None)
        metric = metric or (incident.metric if incident else "")
        recovery_seen = bool(incident and incident.recovery_seen_at)

    health = port.get_service_health(service or host)
    checks.append({
        "check": "service_health",
        "value": health.get("status"),
        "pass": health.get("healthy") is True,
    })

    if "rabbitmq" in (metric or "").lower() or "queue" in str(incident.tags_json if incident else ""):
        queue_name = _queue_name(incident)
        stats = port.get_queue_stats(queue_name)
        consumers = stats.get("consumers")
        ready = stats.get("messages_ready")
        backlog_threshold = threshold if threshold is not None else get_settings().queue_backlog_threshold
        consumers_ok = isinstance(consumers, (int, float)) and consumers > 0
        backlog_ok = isinstance(ready, (int, float)) and ready < backlog_threshold
        checks.append({
            "check": "queue_consumers", "value": consumers, "pass": consumers_ok,
        })
        checks.append({
            "check": "queue_backlog", "value": ready,
            "threshold": backlog_threshold, "pass": backlog_ok,
        })

    healthy = all(c["pass"] for c in checks) if checks else False
    failed = [c["check"] for c in checks if not c["pass"]]
    if healthy:
        summary = f"复验通过（{', '.join(c['check'] for c in checks)}）；"
        if recovery_seen:
            summary += "且已收到监控恢复事件。"
    else:
        summary = f"执行已返回，但复验未通过（{', '.join(failed)}），按假恢复处理并升级人工。"

    return {"healthy": healthy, "checks": checks, "summary": summary,
            "recovery_event_seen": recovery_seen}


def _queue_name(incident) -> str:
    import json

    try:
        tags = json.loads(incident.tags_json or "{}") if incident else {}
    except Exception:  # noqa: BLE001
        tags = {}
    return tags.get("queue") or "default"
