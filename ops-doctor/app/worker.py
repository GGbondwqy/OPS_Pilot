"""后台工作器：单实例顺序 worker（MVP 演示用，非可靠分布式队列）。

职责：
- 轮询 RECEIVED 事故 → 调查流水线；
- 轮询 APPROVED 审批单 → 执行复验流水线。

重启恢复能力有限（见架构文档 §8.3）；生产化时应换成数据库事务队列/Outbox。
"""

from __future__ import annotations

import logging
import threading
import time

from app.config import get_settings
from app.persistence import repository
from app.persistence.database import SessionLocal
from app.agent.orchestrator import investigate_incident
from app.agent.remediation_pipeline import execute_approved_action

logger = logging.getLogger(__name__)

_stop = threading.Event()
_thread: threading.Thread | None = None


def process_once() -> int:
    """处理一轮到期任务，返回处理条数（供测试与 CLI 手动驱动）。"""
    processed = 0
    with SessionLocal() as session:
        received = repository.list_incidents(session, status="RECEIVED", limit=5)
    for incident in received:
        result = investigate_incident(incident.id)
        logger.info("investigated incident %s: %s", incident.id, result.get("state"))
        processed += 1

    with SessionLocal() as session:
        pending = repository.list_approvals(session, status="APPROVED")
    for approval in pending[:5]:
        result = execute_approved_action(approval.id)
        logger.info("executed approval %s: %s", approval.id, result.get("state"))
        processed += 1
    return processed


def _loop() -> None:
    interval = get_settings().worker_interval_s
    logger.info("worker started (interval=%.1fs)", interval)
    while not _stop.is_set():
        try:
            process_once()
        except Exception:  # noqa: BLE001 —— worker 崩了要活着继续
            logger.exception("worker iteration failed")
        _stop.wait(interval)
    logger.info("worker stopped")


def start_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ops-doctor-worker", daemon=True)
    _thread.start()


def stop_worker() -> None:
    _stop.set()
