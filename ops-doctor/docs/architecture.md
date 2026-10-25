# OpsDoctor 架构说明

## 1. 总体架构

OpsDoctor 是一个单机可运行的智能运维 Agent（MVP），由五个模块层组成：

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 接入层 | `app/main.py` | 告警 Webhook（鉴权/校验/幂等/202 异步）、事故与审批 REST API |
| 数据层 | `app/persistence/` | SQLite + SQLAlchemy：事故、原始回调、调查、审批、动作、通知七张表 |
| 证据层 | `app/adapters/` `app/rag/` | 监控数据源适配（mock/zabbix）+ Runbook 检索（可溯源） |
| 调查层 | `app/agent/` | LLM 调查器（OpenAI 兼容）/ 规则调查器（降级）/ 编排与复验流水线 |
| 执行层 | `app/policy/` `app/remediation/` | 动作白名单、审批策略（纯函数）、预定义动作执行器（无 shell 入口） |

外围：`app/worker.py`（后台轮询流水线）、`app/notifications/`（通知）、`app/reports/`（周报/超期）、`app/mcp/`（MCP 只读工具服务）、`app/cli.py`（命令行）。

## 2. 事故状态机

```
RECEIVED → TRIAGING → DIAGNOSED
                        ├→ NEEDS_APPROVAL → APPROVED → EXECUTING → VERIFYING
                        │                                           ├→ RESOLVED
                        │                                           └→ ESCALATED
                        ├→ ESCALATED
                        └→ OBSERVING → RESOLVED / ESCALATED
```

实现要点：

- 迁移必须经 `repository.try_transition()`（条件更新），防止并发状态倒退；
- 允许的迁移集合硬编码在 `app/persistence/models.py::ALLOWED_TRANSITIONS`；
- 语义区分（不可混写）：事件已接收 ≠ 已确认 ≠ 动作执行成功 ≠ 业务恢复 ≠ 闭环。

## 3. 三个信任域

1. **公网与不可信输入域**：告警文本、RAG 文档、日志可能含恶意提示。只校验、脱敏、存储，绝不作为指令。
2. **调查域（只读）**：LLM、MCP、RAG 只能访问白名单只读工具；输出一律视为「建议」。
3. **执行域（写操作）**：与模型完全隔离。`can_execute()` 要求：lab 环境 + 动作在白名单 + 目标预登记 + 审批 APPROVED 且未过期 + action/target 与审批单完全一致 + 审批者≠发起者。

## 4. 调查流水线

```
RECEIVED 事故
  → collect_evidence()      # get_problem / get_service_health / get_queue_stats …
  → retrieve_runbook()      # top-3 片段（doc_id + 版本 + 路径）；空结果 → INSUFFICIENT
  → investigate_with_llm()  # 配置了 LLM；输出经 InvestigationReport 严格校验
     或 investigate_with_rules()  # 未配置 LLM；同样契约，标注 rules
  → 落盘 investigations     # 证据、结论、不确定性分字段保存
  → apply_report()          # 白名单动作 → NEEDS_APPROVAL + 自动创建审批单
                            # 否则 → ESCALATED（含通知）
```

安全规则：

- LLM 输出未通过 Pydantic 校验 → 标 `INVALID_REPORT` → 升级人工，绝不执行；
- LLM 调用失败 → 记录错误 → 升级人工；
- `INVALID_REPORT` 与无证据报告不允许进入审批环节。

## 5. 执行与复验流水线

```
审批 APPROVED
  → can_execute() 二次校验   # 任一条件不满足 → REFUSED + 升级
  → 消费审批单（APPROVED→EXECUTED，一次批准只执行一次）
  → execute_action()         # 只接收 action_key + 预登记 target
  → VERIFYING                # 独立复验，不信任执行返回值
      ├ service_health == UP
      ├ queue_consumers > 0
      └ queue_backlog < 阈值
  → 全部通过 → RESOLVED；否则 → ESCALATED（假恢复拦截）
```

## 6. 配置与令牌

- 所有配置经 `app/config.py` 的 Settings 读取（环境变量 > `.env` > 默认值），前缀 `OPSDOCTOR_`；
- 令牌留空时首次启动自动生成强随机值写入 `data/`，避免零配置用户跑在不安全状态；
- Webhook 与管理接口使用不同令牌，常量时间比较。

## 7. 可扩展点（保持上层契约不变）

| 现在 | 生产化方向 |
| --- | --- |
| SQLite | PostgreSQL（加事务/行级锁/Alembic 迁移） |
| 内置 TF-IDF 检索 | Chroma/向量检索（`retrieve_runbook` 契约不变） |
| MockMonitoringPort | ZabbixMonitoringPort（已内置，配 Token 即可） |
| 单进程 worker | 数据库事务队列 / Outbox / 消息队列 |
| stdio MCP | Streamable HTTP MCP |
| 本地 JSONL 通知 | 钉钉/飞书/短信网关 |

## 8. 与 mall 电商系统（项目 B）的关系

mall 是诊断目标与演示业务系统：它的 Actuator 健康端点、RabbitMQ 队列指标由 Zabbix 采集，告警推送到 OpsDoctor。部署说明见仓库根目录 `mall-deploy/README.md`。
