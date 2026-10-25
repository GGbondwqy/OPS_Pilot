"""内置规则调查器（LLM 未配置时的降级方案，保证开箱即用）。

产出与 LLM 调查器完全相同的 InvestigationReport 契约，但明确标注
investigator=rules，结论为「初步判断」，提示用户配置 LLM 获得更准确诊断。
绝不在规则模式下凭空编造证据 —— 所有 observation 都来自真实收集的证据。
"""

from __future__ import annotations

from app.adapters import MonitoringPort
from app.persistence.models import Incident
from app.schemas.investigation import Evidence, InvestigationReport


def _queue_rule(incident: Incident, port: MonitoringPort, evidence: Evidence) -> InvestigationReport | None:
    """RabbitMQ 积压场景：consumers=0 → 消费者不可用。"""
    queue_tags = {}
    try:
        import json as _json
        queue_tags = _json.loads(incident.tags_json or "{}")
    except Exception:  # noqa: BLE001
        pass
    queue_name = queue_tags.get("queue") or getattr(incident, "service", "") or "default"

    stats = port.get_queue_stats(queue_name)
    evidence.monitoring["queue_stats"] = stats
    observations = [{
        "fact": f"队列 {queue_name} ready={stats.get('messages_ready')} consumers={stats.get('consumers')}",
        "source": "monitoring:get_queue_stats",
        "observed_at": stats.get("observed_at", ""),
    }]

    backlog = stats.get("messages_ready")
    consumers = stats.get("consumers")
    if isinstance(consumers, (int, float)) and consumers == 0:
        return InvestigationReport(
            incident_id=incident.id,
            diagnosis_status="PARTIAL",
            observations=observations,
            hypotheses=[{
                "cause": "消费者不可用（consumers=0），消息无人消费导致积压",
                "confidence_label": "MEDIUM",
                "rationale": "队列消费者数为 0 且积压超过阈值，符合消费者停止的典型特征",
            }],
            runbook_refs=["RUNBOOK-RMQ-001"],
            recommended_action_key="restart_lab_consumer",
            requires_approval=True,
            uncertainty="消费者停止的具体原因（崩溃/OOM/误操作/部署）尚不确定，规则模式无法进一步定位",
            human_summary=(
                f"队列积压 {backlog} 条且消费者数为 0，初步判断消费者不可用；"
                "建议审批后对实验环境执行预定义恢复动作。"
            ),
        )
    if isinstance(backlog, (int, float)) and backlog > (incident.threshold or 0):
        return InvestigationReport(
            incident_id=incident.id,
            diagnosis_status="PARTIAL",
            observations=observations,
            hypotheses=[{
                "cause": "消费速度跟不上生产速度",
                "confidence_label": "LOW",
                "rationale": "积压超过阈值但消费者数大于 0",
            }],
            runbook_refs=["RUNBOOK-RMQ-001"],
            recommended_action_key="",
            requires_approval=False,
            uncertainty="消费者存活但积压增长，规则模式无法判断瓶颈来源，建议人工分析",
            human_summary="消费者存活但队列积压超阈值，建议人工分析消费能力与流量变化。",
        )
    return None


def _health_rule(incident: Incident, port: MonitoringPort, evidence: Evidence) -> InvestigationReport:
    """通用兜底：收集健康与主机指标，按严重级给出保守建议。"""
    health = port.get_service_health(incident.service or incident.host)
    evidence.monitoring["service_health"] = health
    metrics = port.get_host_metrics(incident.host)
    evidence.monitoring["host_metrics"] = metrics

    observations = [{
        "fact": f"服务 {incident.service or incident.host} 健康状态: {health.get('status')}",
        "source": "monitoring:get_service_health",
        "observed_at": health.get("observed_at", ""),
    }]
    for m in metrics.get("metrics", [])[:3]:
        observations.append({
            "fact": f"指标 {m.get('name')}={m.get('value')}",
            "source": "monitoring:get_host_metrics",
            "observed_at": metrics.get("observed_at", ""),
        })

    disk_used = next((m for m in metrics.get("metrics", []) if m.get("name") == "vfs_fs_used_percent"), None)
    if disk_used and isinstance(disk_used.get("value"), (int, float)) and disk_used["value"] > 95:
        return InvestigationReport(
            incident_id=incident.id,
            diagnosis_status="PARTIAL",
            observations=observations,
            hypotheses=[{
                "cause": "磁盘接近写满，可能影响应用写入",
                "confidence_label": "MEDIUM",
                "rationale": f"磁盘使用率 {disk_used['value']}% 超过 95%",
            }],
            runbook_refs=["RUNBOOK-DISK-001"],
            recommended_action_key="",
            requires_approval=False,
            uncertainty="磁盘增长来源未定位；清理动作仅允许针对预登记测试目录且需审批",
            human_summary="磁盘使用率过高，建议人工定位增长来源；自动清理仅限预登记测试目录。",
        )

    return InvestigationReport(
        incident_id=incident.id,
        diagnosis_status="PARTIAL",
        observations=observations,
        hypotheses=[{
            "cause": "需人工进一步排查",
            "confidence_label": "LOW",
            "rationale": "规则模式未能匹配到明确的已知故障模式",
        }],
        runbook_refs=[],
        recommended_action_key="",
        requires_approval=False,
        uncertainty="规则模式诊断能力有限，配置 LLM 后可获得更准确的分析",
        human_summary="已收集只读证据，未匹配到可自动处置的已知模式，转人工处理。",
    )


def investigate_with_rules(incident: Incident, port: MonitoringPort, evidence: Evidence) -> InvestigationReport:
    """规则调查入口：优先匹配队列场景，否则走通用健康规则。"""
    if "rabbitmq" in (incident.metric or "").lower() or "积压" in (incident.problem or ""):
        report = _queue_rule(incident, port, evidence)
        if report is not None:
            return report
    return _health_rule(incident, port, evidence)
