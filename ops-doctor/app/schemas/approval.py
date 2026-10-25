"""审批契约：申请与决策。模型（LLM）禁止调用决策接口。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ApprovalRequest(BaseModel):
    """申请一个预定义动作。action_key 必须在白名单内，target 必须是预登记目标。"""

    action_key: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=128)
    requester: str = Field(default="ops-doctor-agent", max_length=64)


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = Field(min_length=1, max_length=64, description="人工审批者身份标识")
