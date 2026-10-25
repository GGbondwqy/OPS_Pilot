"""ORM 模型（架构文档 §4）。

状态机（§3.2）：
RECEIVED → TRIAGING → DIAGNOSED
                        ├→ NEEDS_APPROVAL → APPROVED → EXECUTING → VERIFYING
                        │                                           ├→ RESOLVED
                        │                                           └→ ESCALATED
                        ├→ ESCALATED
                        └→ OBSERVING → RESOLVED / ESCALATED
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, types
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(types.TypeDecorator):
    """统一时区存储：入库一律转 UTC naive，出库一律补 UTC tzinfo。

    SQLite 不保留时区信息；直接存 aware datetime 会丢失偏移量并导致
    naive/aware 混比错误。该类型保证全库时间语义一致（UTC）。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is not None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class Base(DeclarativeBase):
    pass


class Incident(Base):
    """主事件表：一个源问题一条记录，UNIQUE(source, source_event_id)。"""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    service: Mapped[str] = mapped_column(String(128), default="")
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="WARNING")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RECEIVED")
    metric: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    recovery_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    assignee: Mapped[str] = mapped_column(String(64), default="")

    __table_args__ = (
        UniqueConstraint("source", "source_event_id", name="uq_incident_source_event"),
    )


class InboundEvent(Base):
    """原始回调审计：所有进入的 Webhook 载荷（含重复与恢复事件）。"""

    __tablename__ = "inbound_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="PROBLEM")
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_redacted: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source", "source_event_id", "event_kind", "payload_hash",
            name="uq_inbound_event",
        ),
    )


class Investigation(Base):
    """调查记录：证据、结论、不确定性分开落盘。"""

    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), default="")
    investigator: Mapped[str] = mapped_column(String(32), default="rules")  # rules | llm
    diagnosis_status: Mapped[str] = mapped_column(String(24), default="PARTIAL")
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    conclusion: Mapped[str] = mapped_column(Text, default="")
    uncertainty: Mapped[str] = mapped_column(Text, default="")
    recommended_action_key: Mapped[str] = mapped_column(String(64), default="")
    requires_approval: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Approval(Base):
    """审批单：一个批准只授权一个特定动作与目标，带过期时间。"""

    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    requester: Mapped[str] = mapped_column(String(64), default="opspilot-agent")
    approver: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ActionRun(Base):
    """动作执行审计：returncode/输出摘要/复验结果。"""

    __tablename__ = "action_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    approval_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(128), default="")
    result: Mapped[str] = mapped_column(String(24), nullable=False)  # OK | FAILED | REFUSED
    detail: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Notification(Base):
    """通知记录：dedupe_key 唯一防重复。"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    subject: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent | failed
    payload_redacted: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------- 状态机定义 ----------

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "RECEIVED": {"TRIAGING"},
    "TRIAGING": {"DIAGNOSED", "ESCALATED"},
    "DIAGNOSED": {"NEEDS_APPROVAL", "ESCALATED", "OBSERVING", "RESOLVED"},
    "NEEDS_APPROVAL": {"APPROVED", "ESCALATED", "DIAGNOSED"},
    "APPROVED": {"EXECUTING", "ESCALATED"},
    "EXECUTING": {"VERIFYING", "ESCALATED"},
    "VERIFYING": {"RESOLVED", "ESCALATED", "OBSERVING"},
    "OBSERVING": {"RESOLVED", "ESCALATED"},
    "RESOLVED": set(),
    "ESCALATED": {"RESOLVED"},  # 人工处理完成可关闭
}

TERMINAL_STATUSES = {"RESOLVED"}
OPEN_STATUSES = {
    "RECEIVED", "TRIAGING", "DIAGNOSED",
    "NEEDS_APPROVAL", "APPROVED", "EXECUTING", "VERIFYING", "OBSERVING", "ESCALATED",
}
