"""告警输入契约（架构文档 §3.1）：内部标准格式，不是 Zabbix 原生 JSON。

发送方（Zabbix Webhook 脚本 / 演示脚本）负责把原生载荷映射为该格式。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["INFO", "WARNING", "HIGH", "DISASTER"]


class AlertIn(BaseModel):
    source: Literal["zabbix", "zabbix_mock", "prometheus", "manual"] = "zabbix"
    event_id: str = Field(min_length=1, max_length=64)
    event_kind: Literal["PROBLEM", "RECOVERY"] = "PROBLEM"
    host: str = Field(min_length=1, max_length=128)
    service: str = Field(default="", max_length=128)
    problem: str = Field(min_length=1)
    severity: Severity = "WARNING"
    occurred_at: datetime
    metric: str = Field(default="", max_length=128)
    value: float | None = None
    threshold: float | None = None
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("event_id", "host")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v
