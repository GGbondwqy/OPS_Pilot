"""OpsDoctor API 主应用。

安全边界（架构文档 §5）：
- POST /webhook/zabbix 需要 X-OpsDoctor-Token（常量时间比较）；
- 审批决策 / 手动调查等内部接口需要 X-OpsDoctor-Admin-Token；
- /docs 仅调试用途，生产部署建议关闭或限制网段。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.persistence import repository
from app.persistence.database import SessionLocal, init_db
from app.policy.rules import whitelist_action_keys
from app.schemas.alert import AlertIn
from app.schemas.approval import ApprovalDecision, ApprovalRequest
from app.persistence.models import Incident
from app.worker import process_once, start_worker, stop_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_data_dir()
    init_db()
    settings.webhook_token_resolved()  # 首次启动生成随机令牌
    settings.admin_token_resolved()
    if settings.worker_enabled:
        start_worker()
    yield
    stop_worker()


app = FastAPI(
    title="OpsDoctor",
    description="开箱即用的智能运维 Agent：告警接入 → 证据调查 → 审批式修复 → 复验闭环",
    version=__version__,
    lifespan=lifespan,
)


# ---------- 鉴权 ----------

def _verify_webhook_token(token: str | None) -> None:
    import secrets

    expected = get_settings().webhook_token_resolved()
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid X-OpsDoctor-Token")


def _verify_admin_token(token: str | None) -> None:
    import secrets

    expected = get_settings().admin_token_resolved()
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid X-OpsDoctor-Admin-Token")


def webhook_auth(x_opsdoctor_token: str | None = Header(default=None)) -> None:
    """依赖形式：鉴权先于请求体校验执行（错误令牌优先返回 401）。"""
    _verify_webhook_token(x_opsdoctor_token)


def admin_auth(x_opsdoctor_admin_token: str | None = Header(default=None)) -> None:
    _verify_admin_token(x_opsdoctor_admin_token)


# ---------- 基础 ----------

@app.get("/health")
def health():
    settings = get_settings()
    return {
        "status": "UP",
        "version": __version__,
        "monitoring_source": settings.monitoring_source,
        "llm": "configured" if settings.llm_configured else "not-configured (rules mode)",
        "environment": settings.run_environment,
    }


# ---------- 告警接入 ----------

@app.post("/webhook/zabbix", status_code=202)
def receive_alert(alert: AlertIn, _auth: None = Depends(webhook_auth)):
    """接收告警：快速校验并持久化后返回 202，调查在后台异步进行。

    - PROBLEM：创建/复用主事件（幂等，同一事件重复推送不新增事故）；
    - RECOVERY：不新建事故，记录恢复事件并与原事故关联。
    """
    raw = alert.model_dump(mode="json")

    with SessionLocal() as session:
        inbound, created_inbound = repository.record_inbound_event(
            session, source=alert.source, source_event_id=alert.event_id,
            event_kind=alert.event_kind, raw_payload=raw,
        )
        incident = repository.find_incident(session, alert.source, alert.event_id)

        if alert.event_kind == "RECOVERY":
            if incident is None:
                return JSONResponse(status_code=202, content={
                    "accepted": True, "deduplicated": not created_inbound,
                    "note": "recovery event without matching incident recorded",
                })
            if incident.recovery_seen_at is None:
                incident.recovery_seen_at = datetime.now(timezone.utc)
                session.commit()
            return JSONResponse(status_code=202, content={
                "accepted": True, "incident_id": incident.id,
                "deduplicated": not created_inbound, "kind": "RECOVERY",
                "state": incident.status,
            })

        # PROBLEM
        if incident is not None:
            return JSONResponse(status_code=202, content={
                "accepted": True, "incident_id": incident.id,
                "deduplicated": True, "state": incident.status,
            })

        incident = repository.create_incident_from_alert(session, alert)
        inbound.incident_id = incident.id
        session.commit()
        return JSONResponse(status_code=202, content={
            "accepted": True, "incident_id": incident.id,
            "deduplicated": False, "state": "RECEIVED",
        })


# ---------- 事故查询 ----------

@app.get("/incidents")
def list_incidents(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    with SessionLocal() as session:
        rows = repository.list_incidents(session, status=status, limit=limit, offset=offset)
        return {"count": len(rows), "incidents": [_incident_brief(r) for r in rows]}


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: int):
    with SessionLocal() as session:
        incident = repository.get_incident(session, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        return {
            "incident": _incident_brief(incident),
            "investigations": [
                {"id": i.id, "model": i.model, "investigator": i.investigator,
                 "diagnosis_status": i.diagnosis_status, "conclusion": i.conclusion,
                 "uncertainty": i.uncertainty,
                 "recommended_action_key": i.recommended_action_key,
                 "evidence": i.evidence_json, "created_at": str(i.created_at)}
                for i in repository.list_investigations(session, incident_id)
            ],
            "approvals": [
                {"id": a.id, "action_key": a.action_key, "target": a.target,
                 "status": a.status, "requester": a.requester, "approver": a.approver,
                 "expires_at": str(a.expires_at)}
                for a in repository.list_approvals(session, incident_id=incident_id)
            ],
            "action_runs": [
                {"id": r.id, "action_key": r.action_key, "target": r.target,
                 "result": r.result, "detail": r.detail, "started_at": str(r.started_at)}
                for r in repository.list_action_runs(session, incident_id)
            ],
            "notifications": [
                {"id": n.id, "channel": n.channel, "state": n.state,
                 "subject": n.subject, "status": n.status, "dedupe_key": n.dedupe_key}
                for n in repository.list_notifications(session, incident_id)
            ],
        }


def _incident_brief(r: Incident) -> dict:
    return {
        "id": r.id, "source": r.source, "source_event_id": r.source_event_id,
        "host": r.host, "service": r.service, "problem": r.problem,
        "severity": r.severity, "status": r.status, "metric": r.metric,
        "value": r.value, "threshold": r.threshold,
        "occurred_at": str(r.occurred_at), "resolved_at": str(r.resolved_at) if r.resolved_at else None,
    }


# ---------- 调查触发（内部接口） ----------

@app.post("/incidents/{incident_id}/investigate", status_code=202)
def trigger_investigation(incident_id: int, _auth: None = Depends(admin_auth)):
    """手动触发调查（实验用）。正常运行时后台 worker 自动调查。"""
    with SessionLocal() as session:
        incident = repository.get_incident(session, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        if incident.status != "RECEIVED":
            raise HTTPException(status_code=409,
                                detail=f"incident status={incident.status}，仅 RECEIVED 可触发")
    result = process_once()  # 同步驱动一轮，保证调用后即可观察结果
    return {"accepted": True, "processed": result}


# ---------- 审批 ----------

@app.post("/incidents/{incident_id}/approvals", status_code=201)
def create_approval(
    incident_id: int, req: ApprovalRequest, _auth: None = Depends(admin_auth)
):
    if req.action_key not in whitelist_action_keys():
        raise HTTPException(status_code=422, detail=f"action_key {req.action_key} 不在白名单内")
    with SessionLocal() as session:
        incident = repository.get_incident(session, incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="incident not found")
        approval = repository.create_approval(
            session, incident_id=incident_id, action_key=req.action_key,
            target=req.target, requester=req.requester,
        )
        return {"id": approval.id, "status": approval.status,
                "action_key": approval.action_key, "target": approval.target}


@app.get("/approvals")
def list_approvals(status: str | None = Query(default=None), _auth: None = Depends(admin_auth)):
    with SessionLocal() as session:
        rows = repository.list_approvals(session, status=status)
        return {"count": len(rows), "approvals": [
            {"id": a.id, "incident_id": a.incident_id, "action_key": a.action_key,
             "target": a.target, "status": a.status, "requester": a.requester,
             "approver": a.approver}
            for a in rows
        ]}


@app.post("/approvals/{approval_id}/decision")
def decide_approval(
    approval_id: int, decision: ApprovalDecision, _auth: None = Depends(admin_auth)
):
    """人工审批决策。此接口禁止任何自动化调用方（含模型）使用。"""
    from app.agent.remediation_pipeline import execute_approved_action
    from app.notifications import notify

    with SessionLocal() as session:
        approval = repository.decide_approval(
            session, approval_id, decision.decision, decision.approver
        )
        if approval is None:
            raise HTTPException(status_code=409, detail="审批单不存在或已决策")
        incident_id = approval.incident_id

    if decision.decision == "approve":
        result = execute_approved_action(approval_id)
        return {"approval_id": approval_id, "status": "APPROVED", "execution": result}

    notify(
        incident_id, "REJECTED",
        f"[OpsDoctor] 事故 #{incident_id} 审批被拒绝",
        f"审批人 {decision.approver} 拒绝了动作，事故转人工处理。",
        evidence_refs=[f"approval:{approval_id}"],
    )
    return {"approval_id": approval_id, "status": "REJECTED"}


# ---------- 报告 ----------

@app.get("/reports/weekly")
def weekly_report(week_start: str | None = Query(default=None, description="YYYY-MM-DD（周一）")):
    from app.reports.weekly import build_weekly_report

    try:
        report = build_weekly_report(week_start)
    except ValueError:
        raise HTTPException(status_code=422, detail="week_start 格式应为 YYYY-MM-DD")
    return {"week_start": week_start, "stats": report["stats"], "markdown_file": report["path"]}


@app.get("/reports/overdue")
def overdue_report():
    from app.reports.weekly import overdue_incidents

    rows = overdue_incidents()
    return {"count": len(rows), "incidents": rows}
