"""API 冒烟测试（验收标准 §0.1：健康检查 / 非法载荷 422 / 鉴权 401）。"""

from __future__ import annotations


def test_health(api):
    resp = api.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "UP"


def test_reject_bad_alert_422(api, auth_headers):
    # 缺少必填字段
    resp = api.post("/webhook/zabbix", json={"event_id": "1"}, headers=auth_headers)
    assert resp.status_code == 422


def test_reject_missing_token_401(api):
    resp = api.post("/webhook/zabbix", json={})
    assert resp.status_code == 401


def test_reject_wrong_token_401(api):
    resp = api.post(
        "/webhook/zabbix",
        json={"event_id": "1", "host": "h", "problem": "p",
              "severity": "HIGH", "occurred_at": "2026-10-24T19:00:00+08:00"},
        headers={"X-OpsDoctor-Token": "wrong-token"},
    )
    assert resp.status_code == 401


def test_admin_endpoints_require_token(api):
    resp = api.get("/approvals")
    assert resp.status_code == 401
    resp = api.post("/incidents/1/investigate")
    assert resp.status_code == 401
