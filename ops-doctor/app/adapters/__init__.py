"""数据源适配层（架构文档 §6.4）：统一 Port 接口，mock / zabbix 可替换。

所有返回统一结构：origin / observed_at / reliability，避免把旧指标当实时数据。
"""

from __future__ import annotations

from typing import Protocol

from app.config import get_settings


class MonitoringPort(Protocol):
    """只读监控数据接口。MCP 工具与调查编排共用这一层。"""

    def get_problem(self, event_id: str) -> dict: ...
    def get_host_metrics(self, host: str) -> dict: ...
    def get_service_health(self, service: str) -> dict: ...
    def get_queue_stats(self, queue_name: str) -> dict: ...


def get_monitoring_port() -> MonitoringPort:
    settings = get_settings()
    if settings.monitoring_source == "zabbix":
        from app.adapters.zabbix import ZabbixMonitoringPort

        return ZabbixMonitoringPort()
    from app.adapters.mock import MockMonitoringPort

    return MockMonitoringPort()
