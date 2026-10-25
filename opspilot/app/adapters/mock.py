"""内置模拟监控数据源（开箱即用默认）。

- fixtures/alerts.json 提供静态告警样本；
- data/mock_state.json 提供可变运行状态（消费者数、积压量等），
  由 `python -m app.cli simulate` 注入故障、由 mock 修复动作恢复；
  这是演示「恢复判定」的关键：执行动作成功 ≠ 业务恢复。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

# 故障注入预设：scenario -> mock_state.json 内容
SCENARIOS: dict[str, dict] = {
    # F02：RabbitMQ 消费者停止，队列积压
    "rabbitmq": {
        "service": "mall-portal",
        "host": "mall-server-01",
        "service_health": "DOWN",
        "queue": {
            "name": "mall.order.status",
            "messages_ready": 130,
            "messages_unacked": 5,
            "consumers": 0,
        },
        "poisoned": False,
    },
    # F07：假恢复 —— 执行动作成功但消费者再次崩溃（poisoned）
    "stuck-rabbitmq": {
        "service": "mall-portal",
        "host": "mall-server-01",
        "service_health": "DOWN",
        "queue": {
            "name": "mall.order.status",
            "messages_ready": 130,
            "messages_unacked": 5,
            "consumers": 0,
        },
        "poisoned": True,
    },
    # F01：磁盘写满
    "disk": {
        "service": "mall-admin",
        "host": "mall-server-01",
        "service_health": "UP",
        "disk": {"path": "/data", "used_percent": 97.5, "total_gb": 50},
        "poisoned": False,
    },
    # Nginx 大量 502
    "nginx502": {
        "service": "mall-portal",
        "host": "mall-server-01",
        "service_health": "DOWN",
        "http": {"status_5xx_rate_1m": 0.42, "upstream_errors_last_1m": 87},
        "poisoned": False,
    },
    # MySQL 连接失败
    "mysql": {
        "service": "mall-admin",
        "host": "mall-server-01",
        "service_health": "DOWN",
        "db": {"reachable": False, "error": "connection refused"},
        "poisoned": False,
    },
}

# 各场景对应的模拟告警载荷
ALERT_TEMPLATES: dict[str, dict] = {
    "rabbitmq": {
        "source": "zabbix_mock",
        "event_kind": "PROBLEM",
        "host": "mall-server-01",
        "service": "mall-portal",
        "problem": "RabbitMQ 队列积压超过阈值且消费者数为 0",
        "severity": "HIGH",
        "metric": "rabbitmq_queue_messages_ready",
        "value": 130,
        "threshold": 100,
        "tags": {"env": "lab", "system": "mall", "queue": "mall.order.status"},
    },
    "stuck-rabbitmq": {
        "source": "zabbix_mock",
        "event_kind": "PROBLEM",
        "host": "mall-server-01",
        "service": "mall-portal",
        "problem": "RabbitMQ 队列积压超过阈值且消费者数为 0",
        "severity": "HIGH",
        "metric": "rabbitmq_queue_messages_ready",
        "value": 130,
        "threshold": 100,
        "tags": {"env": "lab", "system": "mall", "queue": "mall.order.status"},
    },
    "disk": {
        "source": "zabbix_mock",
        "event_kind": "PROBLEM",
        "host": "mall-server-01",
        "service": "mall-admin",
        "problem": "磁盘使用率超过 95%",
        "severity": "HIGH",
        "metric": "vfs_fs_used_percent",
        "value": 97.5,
        "threshold": 95,
        "tags": {"env": "lab", "path": "/data"},
    },
    "nginx502": {
        "source": "zabbix_mock",
        "event_kind": "PROBLEM",
        "host": "mall-server-01",
        "service": "mall-portal",
        "problem": "Nginx 5xx 比例异常升高",
        "severity": "HIGH",
        "metric": "nginx_http_5xx_rate",
        "value": 0.42,
        "threshold": 0.05,
        "tags": {"env": "lab", "system": "mall"},
    },
    "mysql": {
        "source": "zabbix_mock",
        "event_kind": "PROBLEM",
        "host": "mall-server-01",
        "service": "mall-admin",
        "problem": "MySQL 连接失败",
        "severity": "DISASTER",
        "metric": "mysql_ping_status",
        "value": 0,
        "threshold": 1,
        "tags": {"env": "lab", "system": "mall"},
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MockMonitoringPort:
    """从 data/mock_state.json 读取状态；文件不存在时视为一切正常。"""

    name = "mock"

    # ---------- 状态读写 ----------

    def _state_path(self) -> Path:
        return get_settings().data_dir / "mock_state.json"

    def load_state(self) -> dict:
        path = self._state_path()
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("mock state file unreadable, treating as healthy")
        return {"service_health": "UP", "poisoned": False}

    def write_state(self, scenario: str) -> dict:
        state = json.loads(json.dumps(SCENARIOS[scenario], ensure_ascii=False))
        state["scenario"] = scenario
        self._state_path().parent.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return state

    def clear_state(self) -> None:
        path = self._state_path()
        if path.exists():
            path.unlink()

    # ---------- MonitoringPort ----------

    def get_problem(self, event_id: str) -> dict:
        """从静态样本中查告警详情。"""
        fixture = Path(__file__).resolve().parents[2] / "fixtures" / "alerts.json"
        if fixture.exists():
            for item in json.loads(fixture.read_text(encoding="utf-8")):
                if str(item.get("event_id")) == str(event_id):
                    return {"found": True, "origin": "mock", "observed_at": _now_iso(), "data": item}
        return {"found": False, "event_id": event_id, "origin": "mock", "observed_at": _now_iso()}

    def get_host_metrics(self, host: str) -> dict:
        state = self.load_state()
        metrics: list[dict] = []
        queue = state.get("queue")
        if queue:
            metrics.append({"name": "rabbitmq_queue_messages_ready",
                            "value": queue["messages_ready"],
                            "threshold": get_settings().queue_backlog_threshold})
            metrics.append({"name": "rabbitmq_consumers", "value": queue["consumers"]})
        disk = state.get("disk")
        if disk:
            metrics.append({"name": "vfs_fs_used_percent",
                            "value": disk["used_percent"], "threshold": 95})
        http = state.get("http")
        if http:
            metrics.append({"name": "nginx_http_5xx_rate", "value": http["status_5xx_rate_1m"],
                            "threshold": 0.05})
        return {
            "host": host,
            "origin": "mock",
            "reliability": "simulated snapshot",
            "observed_at": _now_iso(),
            "metrics": metrics,
        }

    def get_service_health(self, service: str) -> dict:
        state = self.load_state()
        healthy = state.get("service_health", "UP") == "UP"
        return {
            "service": service,
            "origin": "mock",
            "reliability": "simulated",
            "observed_at": _now_iso(),
            "status": "UP" if healthy else "DOWN",
            "healthy": healthy,
        }

    def get_queue_stats(self, queue_name: str) -> dict:
        state = self.load_state()
        queue = state.get("queue", {})
        return {
            "queue": queue_name,
            "origin": "mock",
            "reliability": "simulated",
            "observed_at": _now_iso(),
            "messages_ready": queue.get("messages_ready", 0),
            "messages_unacked": queue.get("messages_unacked", 0),
            "consumers": queue.get("consumers", 2),
        }

    # ---------- 修复动作模拟 ----------

    def apply_restart_consumer(self) -> dict:
        """mock 修复：把消费者恢复为 2、积压降回阈值以下。

        poisoned 状态模拟「命令执行成功但业务未恢复」（F07）：
        返回执行成功，但不修改实际状态。
        """
        state = self.load_state()
        if state.get("poisoned"):
            return {
                "executed": True, "changed": False,
                "note": "restart command returned 0, but consumer crashed again (simulated)",
            }
        if "queue" in state:
            state["queue"]["consumers"] = 2
            state["queue"]["messages_ready"] = max(
                0, int(state["queue"]["messages_ready"]) - 120)
            state["service_health"] = "UP"
        else:
            state["service_health"] = "UP"
        self._state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"executed": True, "changed": True, "note": "consumers restored, backlog draining"}
