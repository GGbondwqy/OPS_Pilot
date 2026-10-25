"""预定义动作注册表与执行器（架构文档 §9.1）。

安全设计：
- 执行器只接收 action_key 和预登记 target，映射到固定程序或模拟实现；
- 绝不接受用户/模型传入的 shell 字符串、路径拼接或任意主机地址；
- 默认 mock 执行器只修改模拟数据源状态，保证演示闭环且开箱安全；
- 接入真实 lab 环境时，在 _execute_real 中实现 systemctl/docker 调用（进阶配置）。
"""

from __future__ import annotations

import logging

from app.adapters import get_monitoring_port
from app.config import get_settings

logger = logging.getLogger(__name__)

# 调查后自动创建审批单时使用的预登记目标
AUTO_APPROVAL_TARGETS: dict[str, str] = {
    "restart_lab_consumer": "mall-consumer-lab",
}


def execute_action(action_key: str, target: str) -> dict:
    """执行预定义动作。返回 {executed, changed, detail}。

    所有动作在这里集中实现；不存在通用 shell 执行入口。
    """
    settings = get_settings()
    if settings.run_environment != "lab":
        return {"executed": False, "changed": False,
                "detail": f"refused: environment={settings.run_environment}"}

    if action_key == "restart_lab_consumer":
        return _restart_lab_consumer(target)
    if action_key == "inspect_service_status":
        return _inspect_service_status(target)
    if action_key == "inspect_disk_usage":
        return _inspect_disk_usage(target)
    return {"executed": False, "changed": False, "detail": f"unknown action {action_key}"}


def _restart_lab_consumer(target: str) -> dict:
    """恢复实验消费者。

    mock 模式：更新模拟数据源状态（恢复消费者、积压回落）。
    真实 lab（进阶）：可在此按 target 调用 systemctl/docker/API，
    实现必须仍然只针对预登记 target。
    """
    if target != "mall-consumer-lab":
        return {"executed": False, "changed": False, "detail": f"unregistered target {target}"}
    port = get_monitoring_port()
    result = port.apply_restart_consumer()  # type: ignore[attr-defined]
    logger.info("restart_lab_consumer(%s): %s", target, result)
    return {
        "executed": bool(result.get("executed")),
        "changed": bool(result.get("changed")),
        "detail": result.get("note", ""),
    }


def _inspect_service_status(target: str) -> dict:
    port = get_monitoring_port()
    health = port.get_service_health(target)
    return {"executed": True, "changed": False, "detail": health}


def _inspect_disk_usage(target: str) -> dict:
    port = get_monitoring_port()
    metrics = port.get_host_metrics(target)
    return {"executed": True, "changed": False, "detail": metrics}
