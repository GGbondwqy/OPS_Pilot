"""端到端流水线测试（验收标准：审批前拒绝 / 审批后恢复 / 假恢复拦截升级）。"""

from __future__ import annotations

from app.adapters.mock import MockMonitoringPort
from app.agent.orchestrator import investigate_incident
from app.agent.remediation_pipeline import execute_approved_action
from app.persistence import repository
from app.persistence.database import SessionLocal

ALERT = {
    "source": "zabbix_mock",
    "event_id": "50001",
    "event_kind": "PROBLEM",
    "host": "mall-server-01",
    "service": "mall-portal",
    "problem": "RabbitMQ 队列积压超过阈值且消费者数为 0",
    "severity": "HIGH",
    "occurred_at": "2026-10-24T19:00:00+08:00",
    "metric": "rabbitmq_queue_messages_ready",
    "value": 130,
    "threshold": 100,
    "tags": {"env": "lab", "system": "mall", "queue": "mall.order.status"},
}


def _inject(api, auth_headers, **overrides):
    MockMonitoringPort().write_state("rabbitmq")
    resp = api.post("/webhook/zabbix", json={**ALERT, **overrides}, headers=auth_headers)
    assert resp.status_code == 202
    return resp.json()["incident_id"]


def _investigate(incident_id):
    result = investigate_incident(incident_id)
    assert result["ok"], result
    return result


def _pending_approval(incident_id):
    with SessionLocal() as session:
        rows = [a for a in repository.list_approvals(session, incident_id=incident_id)
                if a.status == "PENDING"]
        return rows[0] if rows else None


def _status(incident_id):
    with SessionLocal() as session:
        return repository.get_incident(session, incident_id).status


def test_full_remediation_loop_resolves(api, auth_headers):
    """成功路径：注入 → 调查 → 待审批 → 批准 → 执行 → 复验 → RESOLVED。"""
    incident_id = _inject(api, auth_headers)

    result = _investigate(incident_id)
    assert result["state"] == "NEEDS_APPROVAL"
    assert result["investigator"] == "rules"  # 未配置 LLM → 规则模式
    assert _status(incident_id) == "NEEDS_APPROVAL"

    approval = _pending_approval(incident_id)
    assert approval is not None
    assert approval.action_key == "restart_lab_consumer"
    assert approval.target == "mall-consumer-lab"

    with SessionLocal() as session:
        repository.decide_approval(session, approval.id, "approve", "demo-operator")

    execution = execute_approved_action(approval.id)
    assert execution["ok"], execution
    assert execution["state"] == "RESOLVED"
    assert execution["verification"]["healthy"] is True

    with SessionLocal() as session:
        incident = repository.get_incident(session, incident_id)
        assert incident.status == "RESOLVED"
        assert incident.resolved_at is not None
        # 审批单已消费，不可重复执行
        assert repository.get_approval(session, approval.id).status == "EXECUTED"


def test_fake_recovery_is_escalated(api, auth_headers):
    """F07 假恢复：执行成功但业务未恢复 → 必须升级人工，不得标记 RESOLVED。"""
    incident_id = _inject(api, auth_headers)
    MockMonitoringPort().write_state("stuck-rabbitmq")  # poisoned：执行后业务仍故障

    _investigate(incident_id)
    approval = _pending_approval(incident_id)
    assert approval is not None

    with SessionLocal() as session:
        repository.decide_approval(session, approval.id, "approve", "demo-operator")

    execution = execute_approved_action(approval.id)
    assert execution["ok"]
    assert execution["state"] == "ESCALATED"
    assert execution["verification"]["healthy"] is False
    assert _status(incident_id) == "ESCALATED"


def test_rejected_approval_escalates(api, auth_headers):
    """审批拒绝：事故转人工，不执行任何动作。"""
    incident_id = _inject(api, auth_headers)
    _investigate(incident_id)
    approval = _pending_approval(incident_id)
    assert approval is not None

    with SessionLocal() as session:
        repository.decide_approval(session, approval.id, "reject", "demo-operator")

    assert _status(incident_id) == "ESCALATED"
    with SessionLocal() as session:
        runs = repository.list_action_runs(session, incident_id)
        assert runs == [], "拒绝后不应有任何动作执行记录"


def test_duplicate_investigation_is_guarded(api, auth_headers):
    """重复触发调查：非 RECEIVED 状态必须拒绝，防止重复执行。"""
    incident_id = _inject(api, auth_headers)
    _investigate(incident_id)
    result = investigate_incident(incident_id)
    assert not result["ok"]
    assert "RECEIVED" in result["error"]


def test_notifications_recorded_with_dedupe(api, auth_headers):
    """闭环各阶段通知落盘且 dedupe_key 唯一。"""
    incident_id = _inject(api, auth_headers)
    _investigate(incident_id)
    approval = _pending_approval(incident_id)
    with SessionLocal() as session:
        repository.decide_approval(session, approval.id, "approve", "demo-operator")
    execute_approved_action(approval.id)

    with SessionLocal() as session:
        notes = repository.list_notifications(session, incident_id)
        keys = [n.dedupe_key for n in notes]
        assert len(keys) == len(set(keys)), "通知 dedupe_key 不允许重复"
        states = {n.state for n in notes}
        assert "NEEDS_APPROVAL" in states
        assert "RESOLVED" in states
