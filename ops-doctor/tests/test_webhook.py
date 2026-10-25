"""告警接入幂等与恢复事件关联（验收标准：同事件 3 次仅 1 条事故）。"""

from __future__ import annotations

from app.persistence import repository
from app.persistence.database import SessionLocal

BASE_ALERT = {
    "source": "zabbix_mock",
    "event_id": "10001",
    "event_kind": "PROBLEM",
    "host": "mall-server-01",
    "service": "mall-portal",
    "problem": "RabbitMQ 队列积压超过阈值",
    "severity": "HIGH",
    "occurred_at": "2026-10-24T19:00:00+08:00",
    "metric": "rabbitmq_queue_messages_ready",
    "value": 130,
    "threshold": 100,
    "tags": {"env": "lab", "queue": "mall.order.status"},
}


def _post(api, headers, **overrides):
    payload = {**BASE_ALERT, **overrides}
    return api.post("/webhook/zabbix", json=payload, headers=headers)


def test_duplicate_alert_creates_single_incident(api, auth_headers):
    r1 = _post(api, auth_headers)
    r2 = _post(api, auth_headers)
    r3 = _post(api, auth_headers)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r3.status_code == 202

    b1, b2, b3 = r1.json(), r2.json(), r3.json()
    assert b1["deduplicated"] is False
    assert b2["deduplicated"] is True
    assert b3["deduplicated"] is True
    assert b1["incident_id"] == b2["incident_id"] == b3["incident_id"]

    with SessionLocal() as session:
        rows = repository.list_incidents(session)
        assert len(rows) == 1


def test_recovery_event_does_not_create_incident(api, auth_headers):
    problem = _post(api, auth_headers)
    incident_id = problem.json()["incident_id"]

    recovery = _post(api, auth_headers, event_kind="RECOVERY")
    assert recovery.status_code == 202
    body = recovery.json()
    assert body["incident_id"] == incident_id
    assert body["kind"] == "RECOVERY"

    with SessionLocal() as session:
        rows = repository.list_incidents(session)
        assert len(rows) == 1
        incident = repository.get_incident(session, incident_id)
        assert incident.recovery_seen_at is not None


def test_orphan_recovery_recorded_without_incident(api, auth_headers):
    recovery = _post(api, auth_headers, event_id="99999", event_kind="RECOVERY")
    assert recovery.status_code == 202
    assert "incident_id" not in recovery.json()

    with SessionLocal() as session:
        rows = repository.list_incidents(session)
        assert len(rows) == 0
