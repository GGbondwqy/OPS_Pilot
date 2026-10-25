"""Zabbix 只读适配器（JSON-RPC API）。

实验性实现：只读，凭据从配置读取，禁止任何写 API。
生产使用前请先在 Zabbix 界面创建只读 API Token（event.get / problem.get / item.get 权限）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 5.0


class ZabbixMonitoringPort:
    name = "zabbix"

    def _rpc(self, method: str, params: dict) -> dict:
        settings = get_settings()
        if not settings.zabbix_url or not settings.zabbix_token:
            raise RuntimeError("Zabbix 未配置：请填写 OPSPILOT_ZABBIX_URL 和 OPSPILOT_ZABBIX_TOKEN")
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }
        headers = {
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {settings.zabbix_token}",
        }
        url = settings.zabbix_url.rstrip("/") + "/api_jsonrpc.php"
        resp = httpx.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"Zabbix API error: {data['error']}")
        return data.get("result", {})

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_problem(self, event_id: str) -> dict:
        """event.get：取事件详情（含主机、触发器名、严重级、恢复状态）。"""
        try:
            events = self._rpc("event.get", {
                "eventids": event_id,
                "output": ["eventid", "name", "severity", "clock", "r_eventid"],
                "selectHosts": ["host", "name"],
                "selectRelatedObject": ["triggerid", "description"],
            })
            if not events:
                return {"found": False, "event_id": event_id, "origin": "zabbix",
                        "observed_at": self._now_iso()}
            ev = events[0]
            return {
                "found": True,
                "origin": "zabbix",
                "observed_at": self._now_iso(),
                "reliability": "live",
                "data": {
                    "event_id": ev.get("eventid"),
                    "problem": ev.get("name"),
                    "severity": ev.get("severity"),
                    "clock": ev.get("clock"),
                    "hosts": ev.get("hosts", []),
                },
            }
        except Exception as exc:  # noqa: BLE001 —— 调查继续但标记缺数据
            logger.warning("zabbix get_problem failed: %s", exc)
            return {"found": False, "origin": "zabbix", "error": str(exc),
                    "observed_at": self._now_iso()}

    def get_host_metrics(self, host: str) -> dict:
        """item.get：按主机名查最近监控项值。仅返回少量关键项，防 token 溢出。"""
        try:
            hosts = self._rpc("host.get", {"filter": {"host": [host]}, "output": ["hostid"]})
            if not hosts:
                return {"host": host, "origin": "zabbix", "observed_at": self._now_iso(),
                        "reliability": "live", "metrics": [], "error": "host not found"}
            items = self._rpc("item.get", {
                "hostids": hosts[0]["hostid"],
                "output": ["itemid", "name", "key_", "lastvalue", "units", "lastclock"],
                "filter": {"status": 0},
                "limit": 10,
                "search": {"key_": ["rabbitmq", "vfs", "nginx", "mysql"]},
                "searchByAny": True,
            })
            metrics = [
                {"name": i.get("key_"), "value": i.get("lastvalue"),
                 "unit": i.get("units"), "sample_at": i.get("lastclock")}
                for i in items
            ]
            return {"host": host, "origin": "zabbix", "observed_at": self._now_iso(),
                    "reliability": "live", "metrics": metrics}
        except Exception as exc:  # noqa: BLE001
            logger.warning("zabbix get_host_metrics failed: %s", exc)
            return {"host": host, "origin": "zabbix", "error": str(exc),
                    "observed_at": self._now_iso(), "metrics": []}

    def get_service_health(self, service: str) -> dict:
        """通过 HTTP agent item（若有）或最近事件粗判服务健康；实验实现。"""
        try:
            problems = self._rpc("problem.get", {
                "output": ["eventid", "name", "severity"],
                "recent": True,
                "limit": 10,
            })
            related = [p for p in problems if service and service in str(p.get("name", ""))]
            return {
                "service": service,
                "origin": "zabbix",
                "observed_at": self._now_iso(),
                "reliability": "derived from recent problems (experimental)",
                "status": "DOWN" if related else "UP",
                "healthy": not related,
                "active_problems": related,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("zabbix get_service_health failed: %s", exc)
            return {"service": service, "origin": "zabbix", "error": str(exc),
                    "observed_at": self._now_iso(), "healthy": None}

    def get_queue_stats(self, queue_name: str) -> dict:
        """RabbitMQ 队列指标来自 Zabbix 的 item（模板或自定义监控项）。"""
        try:
            items = self._rpc("item.get", {
                "output": ["itemid", "name", "key_", "lastvalue", "lastclock"],
                "search": {"key_": [queue_name]},
                "limit": 5,
            })
            stats: dict = {"queue": queue_name, "origin": "zabbix",
                           "observed_at": self._now_iso(), "reliability": "live"}
            for item in items:
                key = str(item.get("key_", ""))
                if "messages_ready" in key or "messages" in key:
                    stats["messages_ready"] = int(float(item.get("lastvalue") or 0))
                elif "consumers" in key:
                    stats["consumers"] = int(float(item.get("lastvalue") or 0))
            stats.setdefault("messages_ready", None)
            stats.setdefault("consumers", None)
            return stats
        except Exception as exc:  # noqa: BLE001
            logger.warning("zabbix get_queue_stats failed: %s", exc)
            return {"queue": queue_name, "origin": "zabbix", "error": str(exc),
                    "observed_at": self._now_iso()}
