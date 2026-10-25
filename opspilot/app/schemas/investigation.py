"""结构化调查报告契约（架构文档 §8.2）。

LLM 输出必须通过该模型严格校验；未通过则标记 INVALID_REPORT 并升级人工，
绝不自动执行任何修复动作。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Observation(BaseModel):
    """一条事实观察，必须带来源。"""

    fact: str
    source: str = Field(description="证据来源，如 mcp:get_queue_stats / zabbix:event")
    observed_at: str = ""


class Hypothesis(BaseModel):
    """一条推断，必须带置信度标签与理由；事实与假设不得混淆。"""

    cause: str
    confidence_label: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    rationale: str = ""


class InvestigationReport(BaseModel):
    """调查报告：证据 → 假设 → 建议动作，分层清晰。"""

    incident_id: int | None = None
    diagnosis_status: Literal["FULL", "PARTIAL", "INVALID_REPORT"] = "PARTIAL"
    observations: list[Observation] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    runbook_refs: list[str] = Field(default_factory=list)
    recommended_action_key: str = ""
    requires_approval: bool = True
    uncertainty: str = ""
    human_summary: str = ""

    def is_valid(self) -> bool:
        """没有证据支撑的报告不允许进入审批/执行阶段（文档 §15.4 停止条件）。"""
        return self.diagnosis_status != "INVALID_REPORT" and len(self.observations) > 0


class Evidence(BaseModel):
    """调查时收集的证据包（写入 investigations.evidence_json）。"""

    monitoring: dict = Field(default_factory=dict, description="MonitoringPort 各工具返回值")
    runbook: list[dict] = Field(default_factory=list, description="RAG 检索片段及来源")
    knowledge_status: Literal["OK", "INSUFFICIENT"] = "OK"
    llm_mode: Literal["llm", "rules", "unavailable"] = "rules"
    errors: list[str] = Field(default_factory=list)
