"""审批与执行策略（架构文档 §9）：纯函数、可单测、不依赖 LLM。

安全边界：
- 只有 lab 环境允许写动作；其他环境一律拒绝；
- 动作必须在白名单内；目标必须是预登记 target，不接受任意主机/路径/shell 字符串；
- 审批单必须 APPROVED、未过期、且与本次动作的 action_key + target 完全一致；
- 审批者与发起者分离：requester=ops-doctor-agent 时 approver 不能是 ops-doctor-agent。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class ApprovalView:
    """策略层看到的审批单最小视图（避免策略层直接依赖 ORM）。"""

    status: str
    action_key: str
    target: str
    requester: str
    approver: str
    expires_at: datetime | None


def is_expired(approval: ApprovalView, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if approval.expires_at is None:
        return False
    expires = approval.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires < now


def can_execute(
    action_key: str,
    target: str,
    approval: ApprovalView | None,
    environment: str,
) -> tuple[bool, str]:
    """执行前二次校验。返回 (allowed, reason)。任何一项不满足都拒绝。"""
    if environment != "lab":
        return False, f"environment={environment} 不允许执行写动作（仅 lab）"
    if action_key not in WRITABLE_ACTIONS:
        return False, f"action_key={action_key} 不在可执行白名单内"
    if target not in ALLOWED_TARGETS.get(action_key, set()):
        return False, f"target={target} 不是动作 {action_key} 的预登记目标"
    if approval is None:
        return False, "缺少审批单"
    if approval.status != "APPROVED":
        return False, f"审批状态为 {approval.status}，未获批准"
    if approval.action_key != action_key or approval.target != target:
        return False, "审批单与本次动作（action/target）不一致，禁止复用"
    if is_expired(approval):
        return False, "审批单已过期"
    if approval.approver and approval.approver == approval.requester:
        return False, "审批者与发起者相同，违反职责分离"
    return True, "allowed"


# ---------- 动作白名单（§9.1） ----------

# 可写（状态变更）动作：仅审批后执行
WRITABLE_ACTIONS: dict[str, str] = {
    "restart_lab_consumer": "有状态变更：重启实验环境指定消费者",
    "clear_test_temp_dir": "有状态变更：清理预登记测试临时目录（建议晚期启用）",
}

# 只读动作：无需审批
READONLY_ACTIONS: dict[str, str] = {
    "inspect_service_status": "只读：查询指定测试服务状态",
    "inspect_disk_usage": "只读：查询指定实验主机磁盘用量",
}

# 每个动作允许的预登记目标
ALLOWED_TARGETS: dict[str, set[str]] = {
    "restart_lab_consumer": {"mall-consumer-lab"},
    "clear_test_temp_dir": {"/tmp/ops-doctor-test"},
    "inspect_service_status": {"mall-portal", "mall-admin"},
    "inspect_disk_usage": {"mall-server-01"},
}

# 高风险动作：禁止一切自动执行，永远转人工（仅作为知识记录）
FORBIDDEN_ACTIONS: dict[str, str] = {
    "repair_order_data": "高风险：订单/库存数据修复，只能生成工单升级人工",
    "restart_mysql_production": "高风险：任意生产数据库，禁止",
}


def whitelist_action_keys() -> list[str]:
    """白名单全集（调查提示词中使用：模型只能从中推荐）。"""
    return list(WRITABLE_ACTIONS.keys()) + list(READONLY_ACTIONS.keys())
