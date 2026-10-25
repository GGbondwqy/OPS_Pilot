"""MCP 只读工具服务（架构文档 §6.2）。

对外暴露 5 个只读工具：get_problem / get_host_metrics / get_service_health /
get_queue_stats / search_runbook。数据来自统一的 MonitoringPort 与 RAG 检索层。

运行方式（stdio，供 HolmesGPT 等 MCP Client 调用）：
    python -m app.mcp.server
注意：日志一律写 stderr，stdout 只保留 MCP 协议内容。
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from app.adapters import get_monitoring_port
from app.rag import retrieve_runbook

# stdio 模式下 stdout 是协议通道，日志必须走 stderr
import logging

logging.basicConfig(stream=sys.stderr, level=logging.INFO)

mcp = FastMCP("OpsPilot-ReadOnly")
_port = get_monitoring_port()


@mcp.tool()
def get_problem(event_id: str) -> dict:
    """只读：根据事件 ID 查询告警详情（主机、触发条件、严重级）。"""
    return _port.get_problem(event_id)


@mcp.tool()
def get_host_metrics(host: str) -> dict:
    """只读：返回该主机的关键监控指标（值、阈值、采集时间、来源与可靠性）。"""
    return _port.get_host_metrics(host)


@mcp.tool()
def get_service_health(service: str) -> dict:
    """只读：返回指定服务的健康状态（HTTP 健康、状态、时间）。"""
    return _port.get_service_health(service)


@mcp.tool()
def get_queue_stats(queue_name: str) -> dict:
    """只读：返回消息队列积压数、消费者数、采集时间。"""
    return _port.get_queue_stats(queue_name)


@mcp.tool()
def search_runbook(query: str, service: str = "", k: int = 3) -> dict:
    """只读：检索运维知识库（Runbook），返回相关片段、来源文档与版本。

    返回 results 为空表示知识库没有可靠匹配，此时应保守调查而非推测。
    """
    hits = retrieve_runbook(query, service=service or None, k=max(1, min(k, 5)))
    return {"query": query, "knowledge_status": "OK" if hits else "INSUFFICIENT", "results": hits}


if __name__ == "__main__":
    mcp.run()  # stdio 传输
