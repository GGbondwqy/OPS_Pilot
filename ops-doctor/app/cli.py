"""OpsDoctor 命令行工具：不写代码也能完成安装配置、审批、演示与周报。

常用命令：
    python -m app.cli setup                       # 配置向导（AI 模型 URL/Key/模型名，可回车跳过）
    python -m app.cli init                        # 初始化数据库与知识库索引
    python -m app.cli simulate --scenario rabbitmq  # 注入故障并发送模拟告警
    python -m app.cli status                      # 查看事故列表
    python -m app.cli show 1                      # 查看事故详情
    python -m app.cli approve 1 --approver ops    # 审批通过
    python -m app.cli weekly                      # 生成周报
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from app.config import REPO_ROOT, Settings, get_settings


def _write_env(updates: dict[str, str]) -> Path:
    """把配置写入 .env（保留用户已有的其他配置行）。"""
    env_path = REPO_ROOT / ".env"
    existing: dict[str, str] = {}
    lines: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, _, value = stripped.partition("=")
                existing[key.strip()] = value.strip()
            lines.append(line)

    for key, value in updates.items():
        prefixed = key if key.startswith("OPSDOCTOR_") else f"OPSDOCTOR_{key}"
        if prefixed in existing:
            lines = [
                f"{prefixed}={value}" if ln.strip().startswith(prefixed) else ln
                for ln in lines
            ]
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"{prefixed}={value}")
        existing[prefixed] = value

    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def cmd_setup(args) -> int:
    """配置向导：AI 模型三项支持留空（留空 = 规则诊断模式）。"""
    settings = Settings()
    print("=" * 56)
    print("  OpsDoctor 配置向导")
    print("  直接回车 = 保留默认 / 跳过（AI 模型可之后随时配置）")
    print("=" * 56)

    updates: dict[str, str] = {}

    # 1. AI 模型
    print("\n[1/4] AI 模型（任意 OpenAI 兼容接口；三项都填才启用大模型诊断）")
    print("      示例: https://api.openai.com/v1 | https://api.deepseek.com/v1 | http://127.0.0.1:11434/v1")
    base_url = args.llm_base_url or input(f"  模型 API 地址 (Base URL，回车跳过) [{settings.llm_base_url or '空'}]: ").strip()
    if base_url:
        api_key = args.llm_api_key or input("  API Key: ").strip()
        model = args.llm_model or input("  模型名称（如 gpt-4o-mini / deepseek-chat）: ").strip()
        if api_key and model:
            updates["OPSDOCTOR_LLM_BASE_URL"] = base_url
            updates["OPSDOCTOR_LLM_API_KEY"] = api_key
            updates["OPSDOCTOR_LLM_MODEL"] = model
            print("  ✓ 已启用大模型诊断")
        else:
            print("  ⚠ 三项未填全，本次跳过大模型配置（系统将使用内置规则诊断）")
    else:
        print("  - 已跳过，系统将使用内置规则诊断（可随时运行本向导补充）")

    # 2. 监控数据源
    print("\n[2/4] 监控数据源")
    source = args.monitoring or input(
        f"  数据源 mock=内置模拟 / zabbix=真实 Zabbix API [{settings.monitoring_source}]: "
    ).strip().lower()
    if source in {"mock", "zabbix"} and source != settings.monitoring_source:
        updates["OPSDOCTOR_MONITORING_SOURCE"] = source
    if source == "zabbix":
        zbx_url = input("  Zabbix 地址（如 http://10.0.0.5）: ").strip()
        zbx_token = input("  Zabbix API Token（只读）: ").strip()
        if zbx_url:
            updates["OPSDOCTOR_ZABBIX_URL"] = zbx_url
        if zbx_token:
            updates["OPSDOCTOR_ZABBIX_TOKEN"] = zbx_token

    # 3. 通知
    print("\n[3/4] 通知渠道（可跳过，默认写本地文件 data/notifications.jsonl）")
    mode = input(
        f"  file=本地文件 / webhook=通用 Webhook / dingtalk=钉钉机器人 [{settings.notification_mode}]: "
    ).strip().lower()
    if mode in {"file", "webhook", "dingtalk"} and mode != settings.notification_mode:
        updates["OPSDOCTOR_NOTIFICATION_MODE"] = mode
    if mode == "webhook":
        url = input("  Webhook URL: ").strip()
        if url:
            updates["OPSDOCTOR_NOTIFICATION_WEBHOOK_URL"] = url
    elif mode == "dingtalk":
        url = input("  钉钉机器人 Webhook URL: ").strip()
        if url:
            updates["OPSDOCTOR_DINGTALK_WEBHOOK_URL"] = url

    # 4. 完成初始化
    print("\n[4/4] 初始化")
    if updates:
        path = _write_env(updates)
        print(f"  ✓ 配置已写入 {path}")
    else:
        print("  - 未修改任何配置")

    # 重新加载配置并初始化
    get_settings.cache_clear()
    from app.persistence.database import init_db
    from app.rag import build_index

    init_db()
    index = build_index()
    settings2 = get_settings()
    settings2.webhook_token_resolved()
    settings2.admin_token_resolved()
    print(f"  ✓ 数据库就绪：{settings2.db_path}")
    print(f"  ✓ 知识库索引：{len(index.docs)} 份 Runbook")
    print(f"  ✓ Webhook 令牌：{settings2.data_dir / 'webhook_token'}")
    print(f"  ✓ 管理令牌：{settings2.data_dir / 'admin_token'}")
    llm_state = "已配置大模型诊断" if settings2.llm_configured else "规则诊断模式（未配置 LLM）"
    print(f"\n配置完成。当前诊断模式：{llm_state}")
    print("下一步：bash scripts/start.sh 启动服务，然后运行 python -m app.cli simulate 演示完整闭环。")
    return 0


def cmd_init(args) -> int:
    get_settings.cache_clear()
    from app.persistence.database import init_db
    from app.rag import build_index

    init_db()
    index = build_index()
    settings = get_settings()
    settings.webhook_token_resolved()
    settings.admin_token_resolved()
    print(f"✓ 数据库就绪：{settings.db_path}")
    print(f"✓ 知识库索引：{len(index.docs)} 份 Runbook / {len(index.chunks)} 个片段")
    print(f"✓ 诊断模式：{'LLM' if settings.llm_configured else '规则模式（LLM 未配置）'}")
    return 0


def cmd_info(args) -> int:
    settings = get_settings()
    info = {
        "版本": "0.1.0",
        "数据目录": str(settings.data_dir),
        "数据库": str(settings.db_path),
        "监控数据源": settings.monitoring_source,
        "诊断模式": "LLM" if settings.llm_configured else "规则模式（LLM 未配置）",
        "LLM Base URL": settings.llm_base_url or "（未配置）",
        "LLM Model": settings.llm_model or "（未配置）",
        "通知渠道": settings.notification_mode,
        "运行环境": settings.run_environment,
        "Webhook 令牌": str(settings.data_dir / "webhook_token"),
        "管理令牌": str(settings.data_dir / "admin_token"),
    }
    width = max(len(k) for k in info)
    for k, v in info.items():
        print(f"  {k:<{width}} : {v}")
    return 0


def cmd_status(args) -> int:
    from app.persistence import repository
    from app.persistence.database import SessionLocal

    with SessionLocal() as session:
        rows = repository.list_incidents(session, status=args.status, limit=args.limit)
        if not rows:
            print("（暂无事故。运行 python -m app.cli simulate 模拟一条）")
            return 0
        print(f"{'ID':<5}{'状态':<16}{'严重级':<10}{'服务':<14}问题")
        print("-" * 90)
        for r in rows:
            print(f"{r.id:<5}{r.status:<16}{r.severity:<10}{(r.service or '-'):<14}{r.problem[:40]}")
    return 0


def cmd_show(args) -> int:
    from app.persistence import repository
    from app.persistence.database import SessionLocal

    with SessionLocal() as session:
        incident = repository.get_incident(session, args.incident_id)
        if incident is None:
            print(f"事故 {args.incident_id} 不存在")
            return 1
        print(f"\n事故 #{incident.id}  [{incident.status}]  {incident.problem}")
        print(f"  来源: {incident.source}/{incident.source_event_id}  主机: {incident.host}  服务: {incident.service}")
        print(f"  指标: {incident.metric} = {incident.value}（阈值 {incident.threshold}）")
        print(f"  发生: {incident.occurred_at}")

        for inv in repository.list_investigations(session, incident.id):
            print(f"\n  ◆ 调查 #{inv.id}（{inv.investigator} / {inv.model}）→ {inv.diagnosis_status}")
            print(f"    结论: {inv.conclusion}")
            if inv.uncertainty:
                print(f"    不确定性: {inv.uncertainty}")

        for a in repository.list_approvals(session, incident_id=incident.id):
            print(f"\n  ◆ 审批 #{a.id}: {a.action_key} → {a.target}  [{a.status}]")
            print(f"    发起: {a.requester}  审批: {a.approver or '（待审批）'}  过期: {a.expires_at}")

        for run in repository.list_action_runs(session, incident.id):
            print(f"\n  ◆ 执行 #{run.id}: {run.action_key} → {run.result}")
            print(f"    详情: {run.detail[:200]}")

        for n in repository.list_notifications(session, incident.id):
            print(f"\n  ◆ 通知 #{n.id}: [{n.state}] {n.subject}（{n.channel}/{n.status}）")
    return 0


def cmd_approve(args) -> int:
    return _decide(args.approval_id, "approve", args.approver)


def cmd_reject(args) -> int:
    return _decide(args.approval_id, "reject", args.approver)


def _decide(approval_id: int, decision: str, approver: str) -> int:
    if not approver:
        approver = input("审批人姓名/标识: ").strip()
        if not approver:
            print("必须提供审批人身份")
            return 1
    from app.agent.remediation_pipeline import execute_approved_action
    from app.notifications import notify
    from app.persistence import repository
    from app.persistence.database import SessionLocal

    with SessionLocal() as session:
        approval = repository.decide_approval(session, approval_id, decision, approver)
        if approval is None:
            print(f"审批单 {approval_id} 不存在或已决策")
            return 1
        incident_id = approval.incident_id
        print(f"✓ 审批单 #{approval_id} 已标记 {('APPROVED' if decision == 'approve' else 'REJECTED')}")

    if decision == "approve":
        print("执行预定义动作并复验……")
        result = execute_approved_action(approval_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        notify(incident_id, "REJECTED", f"[OpsDoctor] 事故 #{incident_id} 审批被拒绝",
               f"审批人 {approver} 拒绝了动作，事故转人工处理。",
               evidence_refs=[f"approval:{approval_id}"])
        print(f"事故 #{incident_id} 已转人工处理")
    return 0


def cmd_investigate(args) -> int:
    from app.agent.orchestrator import investigate_incident

    result = investigate_incident(args.incident_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


def cmd_simulate(args) -> int:
    """注入模拟故障并发送模拟告警（演示入口）。"""
    from app.adapters.mock import ALERT_TEMPLATES, MockMonitoringPort
    from app.config import get_settings

    scenario = args.scenario
    if scenario not in ALERT_TEMPLATES:
        print(f"未知场景 {scenario}。可选：{', '.join(ALERT_TEMPLATES)}")
        return 1

    port = MockMonitoringPort()
    port.write_state(scenario)
    print(f"✓ 已注入模拟故障状态：{scenario}")

    settings = get_settings()
    alert = dict(ALERT_TEMPLATES[scenario])
    alert["event_id"] = args.event_id or f"SIM-{scenario}-{__import__('time').int_time()}"
    alert["occurred_at"] = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()

    import httpx

    token = settings.webhook_token_resolved()
    url = f"http://{settings.host}:{settings.port}/webhook/zabbix"
    try:
        resp = httpx.post(url, json=alert, headers={"X-OpsDoctor-Token": token}, timeout=10)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 发送失败：{exc}")
        print("  请确认服务已启动（bash scripts/start.sh），且地址端口正确。")
        return 1
    print(f"✓ 告警已接收：incident_id={body.get('incident_id')}  去重={body.get('deduplicated')}")
    print("  后台将自动调查；稍后运行 python -m app.cli status 查看进展，")
    if body.get("incident_id"):
        print(f"  待审批时运行 python -m app.cli approve <审批单ID> --approver 你的名字")
    return 0


def cmd_weekly(args) -> int:
    from app.reports.weekly import build_weekly_report

    report = build_weekly_report(args.week)
    print(report["markdown"])
    print(f"已保存：{report['path']}")
    return 0


def cmd_overdue(args) -> int:
    from app.reports.weekly import overdue_incidents

    rows = overdue_incidents()
    if not rows:
        print("（没有超期未闭环事故）")
        return 0
    for r in rows:
        print(f"  #{r['id']} [{r['severity']}] {r['status']} 已 {r['age_hours']}h（SLA {r['sla_hours']}h）: {r['problem'][:40]}")
    return 0


def cmd_worker_once(args) -> int:
    from app.worker import process_once

    n = process_once()
    print(f"本轮处理 {n} 项任务")
    return 0


def cmd_ui(args) -> int:
    url = f"http://{get_settings().host}:{get_settings().port}/docs"
    print(f"打开 API 文档：{url}")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="OpsDoctor 运维 Agent 命令行")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="配置向导（AI 模型/数据源/通知）")
    p.add_argument("--llm-base-url", default="")
    p.add_argument("--llm-api-key", default="")
    p.add_argument("--llm-model", default="")
    p.add_argument("--monitoring", default="", choices=["", "mock", "zabbix"])
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("init", help="初始化数据库与知识库")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("info", help="查看当前配置")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("status", help="事故列表")
    p.add_argument("--status", default=None)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("show", help="事故详情")
    p.add_argument("incident_id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("approve", help="批准审批单")
    p.add_argument("approval_id", type=int)
    p.add_argument("--approver", default="")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="拒绝审批单")
    p.add_argument("approval_id", type=int)
    p.add_argument("--approver", default="")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("investigate", help="手动触发调查")
    p.add_argument("incident_id", type=int)
    p.set_defaults(func=cmd_investigate)

    p = sub.add_parser("simulate", help="注入模拟故障并发送告警")
    p.add_argument("--scenario", default="rabbitmq",
                   choices=["rabbitmq", "stuck-rabbitmq", "disk", "nginx502", "mysql"])
    p.add_argument("--event-id", default="")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("weekly", help="生成周报")
    p.add_argument("--week", default=None, help="周一日期 YYYY-MM-DD")
    p.set_defaults(func=cmd_weekly)

    p = sub.add_parser("overdue", help="超期未闭环清单")
    p.set_defaults(func=cmd_overdue)

    p = sub.add_parser("worker-once", help="手动驱动一轮后台处理（调试用）")
    p.set_defaults(func=cmd_worker_once)

    p = sub.add_parser("ui", help="在浏览器打开 API 文档")
    p.set_defaults(func=cmd_ui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
