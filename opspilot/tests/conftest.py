"""pytest 全局夹具：每个测试使用独立临时数据目录与干净数据库。

必须在导入 app 之前设置环境变量。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# ---- 环境先行：先于任何 app 导入 ----
_TMP_DATA = tempfile.mkdtemp(prefix="opspilot-test-")
os.environ["OPSPILOT_DATA_DIR"] = _TMP_DATA
os.environ["OPSPILOT_WORKER_ENABLED"] = "false"
os.environ["OPSPILOT_RUN_ENVIRONMENT"] = "lab"
os.environ["OPSPILOT_NOTIFICATION_MODE"] = "file"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.persistence.database import get_engine, init_db, reset_engine  # noqa: E402

reset_engine()
init_db()

TABLES = ["notifications", "action_runs", "approvals", "investigations",
          "inbound_events", "incidents"]


def _clean_state() -> None:
    from sqlalchemy import text

    engine = get_engine()
    with engine.connect() as conn:
        for table in TABLES:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.commit()
    # 清理模拟状态文件
    state_file = Path(_TMP_DATA) / "mock_state.json"
    if state_file.exists():
        state_file.unlink()
    # 清理通知文件
    notif = Path(_TMP_DATA) / "notifications.jsonl"
    if notif.exists():
        notif.unlink()


@pytest.fixture(autouse=True)
def _clean_db():
    _clean_state()
    yield
    _clean_state()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def api():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers(settings):
    return {"X-OpsPilot-Token": settings.webhook_token_resolved()}


@pytest.fixture
def admin_headers(settings):
    return {"X-OpsPilot-Admin-Token": settings.admin_token_resolved()}
