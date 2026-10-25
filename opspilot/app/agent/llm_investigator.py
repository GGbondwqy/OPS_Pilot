"""LLM 调查器：把证据 + Runbook 片段交给模型，产出严格校验的调查报告。

契约（架构文档 §8.2）：模型输出必须通过 InvestigationReport Pydantic 校验；
证据、推断、不确定性分别标注；未通过校验 → INVALID_REPORT → 升级人工。
"""

from __future__ import annotations

import json
import logging

from app.adapters import MonitoringPort
from app.agent import llm
from app.persistence.models import Incident
from app.schemas.investigation import Evidence, InvestigationReport

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是资深 SRE 故障调查助手。你只能读取证据，不能执行任何变更操作。
根据提供的监控证据与运维手册（Runbook）片段，输出严格的 JSON 调查报告。

要求：
1. observations 中的每条"事实"必须来自给定的证据，并标注 source；禁止编造未提供的数据。
2. hypotheses 中的推断必须与事实区分，给出 confidence_label（HIGH/MEDIUM/LOW）与 rationale。
3. runbook_refs 引用给定片段的 doc_id，不要引用未提供的文档。
4. recommended_action_key 只能从【允许动作清单】中选择；清单为空时留空字符串。
5. uncertainty 写明尚不确定的内容；human_summary 用一句话向运维人员说明结论与建议。
6. 只输出 JSON，不要输出其他文字。"""

REPORT_JSON_SCHEMA = """{
  "diagnosis_status": "FULL 或 PARTIAL",
  "observations": [{"fact": "...", "source": "...", "observed_at": "..."}],
  "hypotheses": [{"cause": "...", "confidence_label": "MEDIUM", "rationale": "..."}],
  "runbook_refs": ["RUNBOOK-XXX-001"],
  "recommended_action_key": "",
  "requires_approval": true,
  "uncertainty": "...",
  "human_summary": "..."
}"""


def build_prompt(incident: Incident, evidence: Evidence, allowed_actions: list[str]) -> list[dict]:
    runbook_text = "\n\n".join(
        f"[{r['doc_id']} v{r['version']}] {r['title']} · {r['section']}\n{r['excerpt']}"
        for r in evidence.runbook
    ) or "（未检索到相关 Runbook）"

    user_content = f"""【事故信息】
- incident_id: {incident.id}
- 来源事件: {incident.source}/{incident.source_event_id}
- 主机: {incident.host}
- 服务: {incident.service or '未知'}
- 问题描述: {incident.problem}
- 严重级: {incident.severity}
- 指标: {incident.metric or '无'} 当前值={incident.value} 阈值={incident.threshold}

【监控证据（唯一可信事实来源）】
{json.dumps(evidence.monitoring, ensure_ascii=False, indent=1, default=str)}

【Runbook 片段】
{runbook_text}

【允许动作清单】{json.dumps(allowed_actions, ensure_ascii=False)}
（recommended_action_key 必须来自该清单；不在清单内一律留空并建议人工处理）

【输出格式】
{REPORT_JSON_SCHEMA}"""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def investigate_with_llm(incident: Incident, port: MonitoringPort, evidence: Evidence,
                         allowed_actions: list[str]) -> InvestigationReport:
    messages = build_prompt(incident, evidence, allowed_actions)
    data = llm.chat_json(messages)
    data.setdefault("incident_id", incident.id)
    report = InvestigationReport.model_validate(data)
    # 证据回填：模型报告中缺失来源的观察项补齐标记
    for obs in report.observations:
        if not obs.source:
            obs.source = "llm:unattributed"
    return report
