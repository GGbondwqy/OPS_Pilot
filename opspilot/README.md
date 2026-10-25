# OpsPilot — 开箱即用的智能运维 Agent

接收 Zabbix 告警 → 收集只读证据 → 检索运维知识库（RAG）→ AI 生成诊断报告 → **人工审批** → 执行预定义恢复动作 → **独立复验** → 通知与周报闭环。

> 核心理念：**模型只调查、不执行**。任何修复动作必须经过白名单与人工审批；命令执行成功 ≠ 故障恢复，必须由独立的业务复验决定是否闭环。

```
Zabbix / 模拟告警 ──▶ POST /webhook/zabbix（幂等去重，202 异步）
                        │
                        ▼
              ┌── 后台 Worker ─────────────────────────────┐
              │  1. 收集证据（监控适配层：mock / Zabbix API）│
              │  2. Runbook 检索（RAG，带来源引用）          │
              │  3. 诊断（LLM 或内置规则模式）               │
              │  4. 判定：白名单动作 → 待审批 / 否则升级人工  │
              └──────────────┬─────────────────────────────┘
                             ▼
              人工审批（CLI / API）──▶ 执行预定义动作（仅 lab）
                             ▼
              独立复验（健康检查 + 业务指标）──▶ RESOLVED / 假恢复→升级
                             ▼
              通知（本地文件 / Webhook / 钉钉） + 周报 + 超期追踪
```

## 特性

- **零配置即可运行**：不填任何 AI 配置也能完整走通闭环（内置规则诊断 + 模拟监控数据源）
- **AI 模型随时可配**：支持任意 OpenAI 兼容接口（OpenAI / DeepSeek / 通义 / Moonshot / Ollama / 内部网关），只需 URL + API Key + 模型名三项
- **安全边界清晰**：模型无执行权限；动作白名单 + 预登记目标 + 30 分钟审批有效期 + 职责分离；非 lab 环境禁止一切写操作
- **假恢复拦截**：执行成功但业务未恢复 → 自动升级人工，绝不谎报 RESOLVED
- **开箱即用**：一条命令安装，一条命令看完整演示

## 快速开始（Linux，3 条命令）

要求：Linux（Ubuntu 22.04/24.04 等）+ Python ≥ 3.10。

```bash
# 1. 安装（自动创建虚拟环境、装依赖、初始化数据库与知识库）
bash scripts/install.sh

# 2. 启动服务（保持窗口开着；Ctrl+C 停止）
bash scripts/start.sh

# 3. 新开一个终端，模拟一次「RabbitMQ 消费者停止」故障并观看完整闭环
python -m app.cli simulate
```

`simulate` 会注入故障数据并向本系统发送一条模拟告警。几秒后查看：

```bash
python -m app.cli status          # 事故列表（已自动调查，进入待审批）
python -m app.cli show 1          # 查看事故 #1 的证据、诊断、审批单
python -m app.cli approve 1 --approver 你的名字   # 审批通过 → 自动执行并复验
python -m app.cli status          # 状态变为 RESOLVED
```

想看更完整的一键演示（含等待与自动审批）：

```bash
bash scripts/demo_e2e.sh                # 正常恢复闭环
bash scripts/demo_e2e.sh stuck-rabbitmq # 假恢复演示：执行成功但业务未恢复 → 拦截并升级人工
```

> Windows 开发环境也可以运行（`python -m uvicorn app.main:app` 启动），但生产部署请使用 Linux。

## 配置 AI 模型（可选，随时可配）

不配置也能用（内置规则诊断，所有调查会明确标注 `rules` 模式）。配置后自动切换为大模型诊断：

```bash
python -m app.cli setup
```

按向导填写三项即可：

| 配置项 | 说明 | 示例 |
| --- | --- | --- |
| API 地址 (Base URL) | 任意 OpenAI 兼容接口 | `https://api.openai.com/v1` |
| API Key | 服务商提供的密钥 | `sk-...` |
| 模型名称 | 选择要用的模型 | `gpt-4o-mini` / `deepseek-chat` |

也可以一条命令直接写好（适合脚本化部署）：

```bash
python -m app.cli setup \
  --llm-base-url "https://api.deepseek.com/v1" \
  --llm-api-key "sk-xxxx" \
  --llm-model "deepseek-chat"
```

常用服务商的 Base URL 供参考：

| 服务商 | Base URL |
| --- | --- |
| OpenAI | `https://api.openai.com/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| Moonshot | `https://api.moonshot.cn/v1` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` |
| 公司内部网关 | 咨询管理员（任何 OpenAI 兼容端点均可） |

配置写入项目根目录 `.env` 文件；也可以直接复制 `.env.example` 为 `.env` 手工编辑。

## 配置项总览

所有配置通过环境变量或 `.env` 文件设置（前缀 `OPSPILOT_`），完整清单见 [.env.example](.env.example)。常用的有：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `OPSPILOT_HOST` / `OPSPILOT_PORT` | `127.0.0.1` / `8000` | 监听地址；供 Zabbix 访问时 host 改 `0.0.0.0` |
| `OPSPILOT_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | 空 | AI 模型三项；**留空 = 规则诊断模式** |
| `OPSPILOT_MONITORING_SOURCE` | `mock` | `mock`=内置模拟数据源；`zabbix`=真实 Zabbix API |
| `OPSPILOT_ZABBIX_URL` / `_TOKEN` | 空 | Zabbix 地址与只读 API Token |
| `OPSPILOT_NOTIFICATION_MODE` | `file` | `file` 本地 JSONL / `webhook` 通用 / `dingtalk` 钉钉 |
| `OPSPILOT_RUN_ENVIRONMENT` | `lab` | **只有 `lab` 允许执行修复动作** |
| `OPSPILOT_WEBHOOK_TOKEN` | 自动生成 | Zabbix 回调令牌；留空则自动生成到 `data/webhook_token` |
| `OPSPILOT_ADMIN_TOKEN` | 自动生成 | 内部接口令牌；自动生成到 `data/admin_token` |

> 令牌类配置留空时，系统会在首次启动时自动生成强随机令牌并保存到 `data/` 目录，安全且无需人工干预。

## 接入真实 Zabbix

1. OpsPilot 侧：`.env` 设置 `OPSPILOT_MONITORING_SOURCE=zabbix`、`OPSPILOT_ZABBIX_URL`、`OPSPILOT_ZABBIX_TOKEN`（在 Zabbix 中创建**只读** API Token）。
2. Zabbix 侧：创建 Webhook Media Type，把告警推送过来。脚本模板与逐步配置说明见 [docs/zabbix-integration.md](docs/zabbix-integration.md)，模板文件为 [scripts/zabbix_webhook.js](scripts/zabbix_webhook.js)。

## 生产部署

正式环境部署（systemd 常驻、防火墙、Nginx+HTTPS、备份升级、安全加固、容器镜像）见 **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**。

模拟模式下想快速体验故障场景：

```bash
python -m app.cli simulate --scenario rabbitmq   # 队列积压/消费者停止
python -m app.cli simulate --scenario disk       # 磁盘写满
python -m app.cli simulate --scenario nginx502   # Nginx 5xx
python -m app.cli simulate --scenario mysql      # MySQL 连接失败（只升级人工）
```

## API 一览

启动后访问 `http://127.0.0.1:8000/docs` 查看交互式文档（Swagger）。

| 方法/路径 | 作用 | 鉴权 |
| --- | --- | --- |
| `GET /health` | 健康检查 | 无 |
| `POST /webhook/zabbix` | 接收告警（202 异步） | `X-OpsPilot-Token` |
| `GET /incidents` / `GET /incidents/{id}` | 事故列表 / 详情（证据、审批、动作、通知） | 无（建议内网） |
| `POST /incidents/{id}/investigate` | 手动触发调查 | `X-OpsPilot-Admin-Token` |
| `POST /incidents/{id}/approvals` | 申请预定义动作 | `X-OpsPilot-Admin-Token` |
| `POST /approvals/{id}/decision` | 审批决策（**仅供人工调用**） | `X-OpsPilot-Admin-Token` |
| `GET /reports/weekly` / `GET /reports/overdue` | 周报 / 超期清单 | 无（建议内网） |

命令行等价操作：`python -m app.cli status / show / approve / reject / weekly / overdue / info`。

## MCP 只读工具（进阶：接入 HolmesGPT 等外部 Agent）

OpsPilot 可作为 MCP Server 对外提供 5 个只读诊断工具（`get_problem`、`get_host_metrics`、`get_service_health`、`get_queue_stats`、`search_runbook`）：

```bash
pip install 'mcp>=1.28,<2'
python -m app.mcp.server   # stdio 模式，供 MCP Client 调用
```

接入 HolmesGPT 的配置模板见 [docs/holmesgpt-mcp.md](docs/holmesgpt-mcp.md)。

## 目录结构

```
opspilot/
├── app/
│   ├── main.py               # FastAPI：告警接入、事故、审批、报告
│   ├── config.py             # 统一配置中心（.env / 环境变量）
│   ├── cli.py                # 命令行：setup 向导 / 审批 / 模拟 / 周报
│   ├── worker.py             # 后台调查与执行流水线
│   ├── schemas/              # Pydantic 契约（告警/调查报告/审批）
│   ├── persistence/          # SQLite + SQLAlchemy（状态机/去重/审计）
│   ├── adapters/             # 监控数据源适配层（mock / zabbix）
│   ├── rag/                  # Runbook 检索（零依赖 TF-IDF，可换 Chroma）
│   ├── agent/                # LLM 调查器 / 规则调查器 / 编排 / 复验
│   ├── policy/               # 动作白名单与审批策略（纯函数可单测）
│   ├── remediation/          # 预定义动作执行器（无任意 shell 入口）
│   ├── notifications/        # 通知（JSONL / Webhook / 钉钉，去重）
│   ├── reports/              # 周报与超期追踪
│   └── mcp/                  # MCP 只读工具服务（可选依赖）
├── knowledge/runbooks/       # 运维知识库（Markdown + front matter）
├── fixtures/                 # 模拟告警样本
├── scripts/                  # install / start / demo / zabbix_webhook.js
├── tests/                    # pytest（API/幂等/策略/RAG/端到端/周报）
├── docs/                     # 架构说明、Zabbix 对接、HolmesGPT 接入
├── data/                     # 运行时数据（.gitignore，勿提交）
└── .env.example              # 配置模板
```

## 测试

```bash
python -m pytest tests/ -v
```

覆盖：告警鉴权与 422、同事件去重幂等、恢复事件关联、审批策略（越权/过期/自审/目标不符全部拒绝）、RAG 检索与正确拒答、完整恢复闭环、假恢复拦截升级、通知去重、周报口径。

## 安全模型（必读）

三个信任域，模型被隔离在最内层：

1. **不可信输入域**：告警文本、RAG 文档、日志都视为不可信数据，只做校验与展示，绝不当作指令。
2. **调查域（只读）**：LLM / MCP / RAG 只能查询白名单内的只读工具，无任何写权限。
3. **执行域（写操作）**：与模型完全隔离。动作必须满足：白名单内 + 预登记目标 + lab 环境 + 人工审批 + 有效期 30 分钟 + 一次批准只执行一次。

**禁止**：把模型输出直接当命令执行；审批接口暴露给自动化调用方；通过改数据库状态把真实未恢复的故障写成 RESOLVED。

## 常见问题

**Q：不配置 AI 能用吗？**
能用。系统用内置规则诊断（基于证据关键词与指标判断），所有报告明确标注 `rules` 模式。配置 LLM 后无需重启即可自动切换。

**Q：端口/地址在哪改？**
`.env` 中的 `OPSPILOT_HOST` / `OPSPILOT_PORT`，或启动时用环境变量覆盖：`OPSPILOT_PORT=9000 bash scripts/start.sh`。

**Q：怎么彻底重置演示数据？**
停止服务后删除 `data/` 目录（数据库、令牌、模拟状态都在里面），重新运行 `python -m app.cli init`。

**Q：LLM 调查失败会怎样？**
按安全策略升级人工（ESCALATED），不执行任何动作，失败原因记录在调查证据中。

**Q：能接真实 Zabbix/RabbitMQ 吗？**
能。OpsPilot 侧改 `OPSPILOT_MONITORING_SOURCE=zabbix` 即可（见上文）；修复动作要操作真实服务时，在 `app/remediation/actions.py` 的 `_restart_lab_consumer` 中按预登记 target 实现 systemctl/docker 调用——保持白名单机制不变。

更多排查项见架构文档同名录《常见故障与排查》。

## License

MIT
