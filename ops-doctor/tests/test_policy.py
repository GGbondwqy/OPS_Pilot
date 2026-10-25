"""审批策略单测（验收标准 §0.1：审批前不能执行 / 批准与目标绑定）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.policy.rules import ApprovalView, can_execute


def _approval(**overrides) -> ApprovalView:
    base = dict(
        status="APPROVED", action_key="restart_lab_consumer",
        target="mall-consumer-lab", requester="ops-doctor-agent",
        approver="ops-admin",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    base.update(overrides)
    return ApprovalView(**base)


def test_allow_valid_approval():
    allowed, reason = can_execute(
        "restart_lab_consumer", "mall-consumer-lab", _approval(), "lab"
    )
    assert allowed, reason


def test_reject_without_approval():
    allowed, reason = can_execute(
        "restart_lab_consumer", "mall-consumer-lab", None, "lab"
    )
    assert not allowed
    assert "缺少审批单" in reason


def test_reject_pending_approval():
    allowed, _ = can_execute(
        "restart_lab_consumer", "mall-consumer-lab",
        _approval(status="PENDING"), "lab",
    )
    assert not allowed


def test_reject_wrong_target():
    allowed, _ = can_execute(
        "restart_lab_consumer", "mall-server-99", _approval(), "lab"
    )
    assert not allowed


def test_reject_action_target_mismatch():
    # 批准的是 A 动作，想执行 B 动作：禁止复用
    allowed, _ = can_execute(
        "clear_test_temp_dir", "/tmp/ops-doctor-test", _approval(), "lab"
    )
    assert not allowed


def test_reject_expired_approval():
    expired = _approval(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    allowed, reason = can_execute(
        "restart_lab_consumer", "mall-consumer-lab", expired, "lab"
    )
    assert not allowed
    assert "过期" in reason


def test_reject_non_lab_environment():
    allowed, _ = can_execute(
        "restart_lab_consumer", "mall-consumer-lab", _approval(), "production"
    )
    assert not allowed


def test_reject_self_approval():
    allowed, reason = can_execute(
        "restart_lab_consumer", "mall-consumer-lab",
        _approval(approver="ops-doctor-agent"), "lab",
    )
    assert not allowed
    assert "职责分离" in reason


def test_reject_unlisted_action():
    allowed, _ = can_execute(
        "restart_mysql_production", "any", None, "lab"
    )
    assert not allowed
