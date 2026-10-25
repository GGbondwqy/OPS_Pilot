# OpsDoctor（运维医生）— AI 值班运维 Agent

**看名字就知道它干嘛：像医生一样给你的服务器看病。**

收集症状（接收监控告警）→ 化验检查（只读证据采集）→ 诊断（AI + 运维知识库 RAG）→ 开处方（修复建议）→ 家属签字（人工审批）→ 治疗（执行预定义动作）→ 复诊（独立业务复验）→ 病历归档（审计与周报）。

[![CI](https://github.com/GGbondwqy/OpsDoctor/actions/workflows/ci.yml/badge.svg)](https://github.com/GGbondwqy/OpsDoctor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

接收 Zabbix 告警 → 收集只读证据 → 检索运维知识库（RAG）→ AI 生成诊断报告 → **人工审批** → 执行预定义恢复动作 → **独立复验** → 通知与周报闭环。

> 核心理念：**模型只调查、不执行**。任何修复动作必须经过白名单与人工审批；命令执行成功 ≠ 故障恢复，必须由独立的业务复验决定是否闭环。

本仓库包含两个项目：

| 目录 | 项目 | 技术栈 | 说明 |
| --- | --- | --- | --- |
| [`ops-doctor/`](ops-doctor/) | **项目 A：OpsDoctor 智能运维 Agent**（自研） | Python 3.10+ / FastAPI / SQLite | 告警接入、证据调查、RAG、审批式修复、复验闭环。零配置可跑，已内置测试与 CI |
| [`mall-deploy/`](mall-deploy/) | **项目 B：mall 电商系统部署套件**（复用开源 [macrozheng/mall](https://github.com/macrozheng/mall)） | Java 17 / Spring Boot | Linux 部署清单、中间件 Compose、可观测性改造与故障演练说明 |

## 系统架构

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

与 mall 的配合：mall 的 Actuator 健康端点与 RabbitMQ 队列指标由 Zabbix 采集并触发告警，推送给 OpsDoctor 处理；修复动作经审批后作用于实验环境的 mall 服务。

## 特性

- **零配置即用**：不填任何 AI 配置也能完整走通闭环（内置规则诊断 + 模拟监控数据源）
- **AI 模型随时可配**：任意 OpenAI 兼容接口（OpenAI / DeepSeek / 通义 / Moonshot / Ollama / 内部网关），只需 URL + API Key + 模型名
- **安全边界清晰**：模型无执行权限；动作白名单 + 预登记目标 + 审批有效期 + 职责分离；非 lab 环境禁止一切写操作
- **假恢复拦截**：执行成功但业务未恢复 → 自动升级人工，绝不谎报 RESOLVED
- **MCP 只读工具**：可作为 MCP Server 接入 HolmesGPT 等外部 Agent
- **完整可观测**：事故状态机、证据落盘、审批审计、通知去重、周报统计

## 快速开始（Linux，3 条命令）

要求：Linux + Python ≥ 3.10。

```bash
git clone https://github.com/GGbondwqy/OpsDoctor.git
cd OpsDoctor/ops-doctor
bash scripts/install.sh     # 安装：venv + 依赖 + 数据库 + 知识库 + 自动生成令牌
```

```bash
bash scripts/start.sh       # 终端 1：启动服务（127.0.0.1:8000）
python -m app.cli simulate  # 终端 2：模拟一次「RabbitMQ 消费者停止」故障
```

然后查看并完成闭环：

```bash
python -m app.cli status                          # 自动调查完成，进入待审批
python -m app.cli show 1                          # 查看证据、诊断、审批单
python -m app.cli approve 1 --approver 你的名字    # 审批 → 自动执行 → 复验 → RESOLVED
```

> Windows 也可开发运行（`python -m uvicorn app.main:app`），生产部署请用 Linux。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [ops-doctor/README.md](ops-doctor/README.md) | Agent 功能、CLI、API、安全模型、FAQ |
| [mall-deploy/DEPLOYMENT.md](mall-deploy/DEPLOYMENT.md) | **全链路部署总纲（从这里开始）**：拓扑方案、端口矩阵、6 个部署阶段与验证判据、联调演练 |
| [ops-doctor/docs/DEPLOYMENT.md](ops-doctor/docs/DEPLOYMENT.md) | OpsDoctor 部署手册：systemd 常驻、防火墙、备份升级、安全加固、排障 |
| [ops-doctor/docs/zabbix-integration.md](ops-doctor/docs/zabbix-integration.md) | Zabbix 对接：Webhook 脚本与逐步配置 |
| [ops-doctor/docs/holmesgpt-mcp.md](ops-doctor/docs/holmesgpt-mcp.md) | MCP 只读服务与 HolmesGPT 接入 |
| [ops-doctor/docs/architecture.md](ops-doctor/docs/architecture.md) | 架构细节：状态机、信任域、流水线、扩展点 |
| [mall-deploy/README.md](mall-deploy/README.md) | mall 固定版本、中间件、可观测性改造 |

## 仓库结构

```
OpsDoctor/
├── ops-doctor/                  # 项目 A：智能运维 Agent（自研，MIT）
│   ├── app/                   #   FastAPI / worker / agent / rag / policy / mcp / cli
│   ├── knowledge/runbooks/    #   运维知识库（6 份合成 Runbook）
│   ├── tests/                 #   29 个 pytest（API/幂等/策略/RAG/端到端/周报）
│   ├── scripts/               #   install / start / demo / backup / systemd / zabbix webhook
│   ├── docs/                  #   部署手册、架构说明、Zabbix/MCP 对接
│   ├── Dockerfile             #   可选容器部署
│   └── .env.example           #   配置模板（AI 三项留空即用）
├── mall-deploy/               # 项目 B：mall 部署套件
│   ├── docker-compose.yml     #   MySQL / Redis / RabbitMQ
│   ├── DEPLOYMENT.md          #   全链路部署总纲
│   └── README.md              #   mall 部署与可观测性改造
└── .github/workflows/         # CI：ubuntu + Python 3.10/3.12 跑测试与冒烟
```

> 目录边界按「可拆分」设计：`ops-doctor/` 完全自包含，将来可独立成单独仓库发布，代码零改动。

## 部署顺序速览

1. **OpsDoctor**（10 分钟）：`ops-doctor/` 内 3 条命令，零依赖跑通完整闭环
2. **AI 模型**（可选，5 分钟）：`python -m app.cli setup` 填三项配置
3. **mall + 中间件**（半天）：`mall-deploy/` 按 README 固定版本后部署
4. **Zabbix 联调**（1 小时）：Webhook 模板现成，步骤见对接文档

详细分阶段说明与验证判据：[mall-deploy/DEPLOYMENT.md](mall-deploy/DEPLOYMENT.md)。

## 开源边界

- **OpsDoctor 为本项目自研**：Webhook 契约与幂等、事故状态机、MCP 只读工具、Runbook RAG、LLM 调查与规则降级、审批白名单、假恢复拦截、周报闭环与测试基准。
- **mall 为开源复用**（macrozheng/mall）：本项目的贡献是可观测性埋点方案、监控适配、故障演练开关与业务一致性验证；mall 版权与许可证归属原作者，部署前请查阅其仓库说明。
- **HolmesGPT 为可选进阶集成**，不属于本项目自研范围。

## License

[MIT](LICENSE)（ops-doctor 部分）
