"""统一通知（架构文档 §12.1）。

- file     ：默认。写 data/notifications.jsonl，可断言、可回放；
- webhook  ：通用 JSON POST（自建系统/飞书自定义机器人均可用）；
- dingtalk ：钉钉群机器人 markdown 消息。

所有通知经 dedupe_key 去重：同一事故同一状态只发一次；
发送失败记录 status=failed，不吞异常，供后续重试。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.persistence import repository
from app.persistence.database import SessionLocal

logger = logging.getLogger(__name__)

STATE_LABELS = {
    "NEEDS_APPROVAL": "待审批",
    "ESCALATED": "已升级人工",
    "RESOLVED": "已恢复",
    "OBSERVING": "观察中",
    "REJECTED": "审批被拒绝",
}


def notify(incident_id: int, state: str, subject: str, summary: str,
           evidence_refs: list[str] | None = None) -> dict:
    """发送通知并入库（dedupe_key = incident-{id}-{state}-v1）。"""
    settings = get_settings()
    display_tz = ZoneInfo(settings.display_tz)
    dedupe_key = f"incident-{incident_id}-{state}-v1"
    time_str = datetime.now(display_tz).strftime("%Y-%m-%d %H:%M:%S")
    state_label = STATE_LABELS.get(state, state)
    payload = {
        "incident_id": incident_id,
        "state": state,
        "state_label": state_label,
        "subject": subject,
        "summary": summary,
        "evidence_refs": evidence_refs or [],
        "time": time_str,
    }

    status = "failed"
    try:
        if settings.notification_mode == "dingtalk" and settings.dingtalk_webhook_url:
            _send_dingtalk(subject, f"**[{state_label}]** {summary}\n\n> {time_str}")
            status = "sent"
        elif settings.notification_mode == "webhook" and settings.notification_webhook_url:
            _send_webhook(payload)
            status = "sent"
        else:
            _write_jsonl(payload)
            status = "sent"
    except Exception as exc:  # noqa: BLE001 —— 失败入库供重试，不中断主流程
        logger.warning("notification send failed: %s", exc)
        payload["error"] = str(exc)

    with SessionLocal() as session:
        row, created = repository.record_notification(
            session,
            incident_id=incident_id,
            channel=settings.notification_mode,
            state=state,
            subject=subject,
            summary=summary,
            dedupe_key=dedupe_key,
            payload=json.dumps(payload, ensure_ascii=False),
            status=status,
        )
    return {"created": created, "status": status, "notification_id": row.id}


def _write_jsonl(payload: dict) -> None:
    settings = get_settings()
    settings.ensure_data_dir()
    path = settings.data_dir / "notifications.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _send_webhook(payload: dict) -> None:
    resp = httpx.post(
        get_settings().notification_webhook_url, json=payload, timeout=5.0
    )
    resp.raise_for_status()


def _send_dingtalk(subject: str, text: str) -> None:
    resp = httpx.post(
        get_settings().dingtalk_webhook_url,
        json={"msgtype": "markdown",
              "markdown": {"title": subject, "text": f"### OpsDoctor\n\n{text}"}},
        timeout=5.0,
    )
    resp.raise_for_status()
