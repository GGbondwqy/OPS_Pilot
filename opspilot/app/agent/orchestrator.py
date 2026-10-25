"""调查编排：收集证据 → Runbook 检索 → 调查（LLM 或规则）→ 保存报告 → 规则判定。"""

from __future__ import annotations

import logging

from app.adapters import MonitoringPort, get_monitoring_port
from app.agent.llm_investigator import investigate_with_llm
from app.agent.rule_based import investigate_with_rules
from app.config import get_settings
from app.persistence import repository
from app.persistence.database import SessionLocal
from app.persistence.models import Incident
from app.policy.rules import ALLOWED_TARGETS, whitelist_action_keys
from app.rag import retrieve_runbook
from app.remediation.actions import AUTO_APPROVAL_TARGETS
from app.schemas.investigation import Evidence, InvestigationReport

logger = logging.getLogger(__name__)


def collect_evidence(incident: Incident, port: MonitoringPort) -> Evidence:
    """收集只读证据；任何一步失败都记录并继续，不让调查中断。"""
    evidence = Evidence()
    problem = port.get_problem(incident.source_event_id)
    if problem.get("found"):
        evidence.monitoring["problem"] = problem
    health = port.get_service_health(incident.service or incident.host)
    evidence.monitoring["service_health"] = health

    runbook_hits = retrieve_runbook(
        f"{incident.problem} {incident.metric}",
        service=incident.service or None,
        k=3,
    )
    evidence.runbook = runbook_hits
    evidence.knowledge_status = "OK" if runbook_hits else "INSUFFICIENT"
    return evidence


def run_investigation(incident: Incident, port: MonitoringPort) -> tuple[Evidence, InvestigationReport, str, str]:
    """执行调查。返回 (evidence, report, investigator, model_name)。"""
    evidence = collect_evidence(incident, port)
    settings = get_settings()
    allowed = whitelist_action_keys()

    if settings.llm_configured:
        try:
            report = investigate_with_llm(incident, port, evidence, allowed)
            evidence.llm_mode = "llm"
            return evidence, report, "llm", settings.llm_model
        except Exception as exc:  # noqa: BLE001 —— 模型失败不阻断，升级人工
            logger.warning("LLM investigation failed: %s", exc)
            evidence.llm_mode = "unavailable"
            evidence.errors.append(f"llm: {exc}")
            report = InvestigationReport(
                incident_id=incident.id,
                diagnosis_status="INVALID_REPORT",
                observations=[{
                    "fact": f"LLM 调查失败: {exc}",
                    "source": "orchestrator",
                    "observed_at": "",
                }],
                hypotheses=[],
                runbook_refs=[r["doc_id"] for r in evidence.runbook],
                recommended_action_key="",
                requires_approval=False,
                uncertainty="模型调用失败，证据不完整，需人工判断",
                human_summary="模型调查失败，已按安全策略升级人工处理，未执行任何动作。",
            )
            return evidence, report, "llm_failed", settings.llm_model

    evidence.llm_mode = "rules"
    report = investigate_with_rules(incident, port, evidence)
    return evidence, report, "rules", "rule-based (LLM not configured)"


def apply_report(incident: Incident, report: InvestigationReport) -> str:
    """按调查报告推进状态机。返回最终状态。

    判定规则（文档 §8/§9）：
    - 报告无效 / 模型失败 → ESCALATED
    - 推荐动作在白名单内且需要审批 → NEEDS_APPROVAL + 自动创建审批单
    - 无推荐动作 / 高风险动作 → ESCALATED（转人工）
    """
    from app.notifications import notify

    with SessionLocal() as session:
        fresh = repository.get_incident(session, incident.id)
        if fresh is None:
            return "MISSING"

        if not report.is_valid():
            repository.try_transition(session, fresh.id, "DIAGNOSED", "ESCALATED")
            _notify_state(fresh.id, "ESCALATED", report.human_summary)
            return "ESCALATED"

        action_key = report.recommended_action_key or ""
        if action_key and action_key in whitelist_action_keys():
            repository.try_transition(session, fresh.id, "DIAGNOSED", "NEEDS_APPROVAL")
            # 自动创建待审批单，target 用预登记目标
            target = AUTO_APPROVAL_TARGETS.get(action_key, fresh.host)
            if action_key in ALLOWED_TARGETS and target not in ALLOWED_TARGETS[action_key]:
                target = next(iter(ALLOWED_TARGETS[action_key]), fresh.host)
            approval = repository.create_approval(
                session, incident_id=fresh.id, action_key=action_key,
                target=target, requester="opspilot-agent",
            )
            _notify_state(
                fresh.id, "NEEDS_APPROVAL",
                f"{report.human_summary}\n待审批动作：{action_key}（目标 {target}，审批单 #{approval.id}）",
            )
            return "NEEDS_APPROVAL"

        repository.try_transition(session, fresh.id, "DIAGNOSED", "ESCALATED")
        _notify_state(
            fresh.id, "ESCALATED",
            report.human_summary or "未匹配到可自动执行的预定义动作，需人工处理。",
        )
        return "ESCALATED"


def _notify_state(incident_id: int, state: str, summary: str) -> None:
    from app.notifications import notify

    try:
        notify(
            incident_id, state,
            f"[OpsPilot] 事故 #{incident_id} {state}",
            summary or "（详见事故详情）",
            evidence_refs=[f"incident:{incident_id}"],
        )
    except Exception:  # noqa: BLE001 —— 通知失败不阻断状态机
        logger.exception("notify failed for incident %s state %s", incident_id, state)


def investigate_incident(incident_id: int) -> dict:
    """对单个事故执行完整调查流水线（worker 与手动触发共用入口）。"""
    with SessionLocal() as session:
        incident = repository.get_incident(session, incident_id)
        if incident is None:
            return {"ok": False, "error": f"incident {incident_id} not found"}
        if not repository.try_transition(session, incident_id, "RECEIVED", "TRIAGING"):
            return {"ok": False, "error": f"incident {incident_id} not in RECEIVED state"}
        snapshot = Incident(
            id=incident.id, source=incident.source,
            source_event_id=incident.source_event_id, host=incident.host,
            service=incident.service, problem=incident.problem,
            severity=incident.severity, metric=incident.metric,
            value=incident.value, threshold=incident.threshold,
            tags_json=incident.tags_json,
        )

    port: MonitoringPort = get_monitoring_port()
    evidence, report, investigator, model_name = run_investigation(snapshot, port)

    with SessionLocal() as session:
        repository.create_investigation(
            session,
            incident_id=incident_id,
            model=model_name,
            investigator=investigator,
            diagnosis_status=report.diagnosis_status,
            evidence={
                "monitoring": evidence.monitoring,
                "runbook": evidence.runbook,
                "knowledge_status": evidence.knowledge_status,
                "llm_mode": evidence.llm_mode,
                "errors": evidence.errors,
                "report": report.model_dump(),
            },
            conclusion=report.human_summary,
            uncertainty=report.uncertainty,
            recommended_action_key=report.recommended_action_key,
            requires_approval=report.requires_approval,
        )
        # 证据已落盘，进入已诊断状态（后续由 apply_report 推进）
        repository.try_transition(session, incident_id, "TRIAGING", "DIAGNOSED")

    final_state = apply_report(snapshot, report)
    return {
        "ok": True,
        "incident_id": incident_id,
        "investigator": investigator,
        "diagnosis_status": report.diagnosis_status,
        "state": final_state,
        "summary": report.human_summary,
    }
