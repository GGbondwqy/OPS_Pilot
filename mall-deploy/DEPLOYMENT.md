# OpsDoctor × mall 全链路部署总纲

一套系统、四类组件（OpsDoctor / mall / Zabbix / LLM），本总纲回答三件事：
**从哪些仓库拉什么代码、按什么顺序部署、每一步怎么算成功**。

各组件的详细手册（ops-doctor 部分位于 **ops-doctor 仓库**，mall 部分位于本仓库）：

| 组件 | 手册位置 |
| --- | --- |
| OpsDoctor（Agent 本体） | ops-doctor 仓库 → `docs/DEPLOYMENT.md`（部署/常驻/备份/加固） |
| mall 电商系统 | 本仓库 [README.md](README.md) |
| Zabbix → OpsDoctor 对接 | ops-doctor 仓库 → `docs/zabbix-integration.md`（Webhook 模板在 `scripts/zabbix_webhook.js`） |

## 0. 仓库划分与拉取清单

本系统采用 **单仓库（monorepo）** 管理：两个项目目录在同一仓库中，克隆一次即可获得全部代码与文档。

| 目录 | 内容 | 部署到 |
| --- | --- | --- |
| `ops-doctor/` | Python 运维 Agent（自研、开源、自包含） | OpsDoctor 所在 VM |
| `mall-deploy/` | mall 部署套件（中间件 Compose + 部署文档） | mall 所在 VM / 构建机 |
| mall 源码 | 开源上游 [macrozheng/mall](https://github.com/macrozheng/mall)（单独克隆并固定版本） | mall 所在 VM / 构建机 |

```bash
git clone https://github.com/GGbondwqy/OpsDoctor.git
cd OpsDoctor

# 双机部署：两台 VM 各自克隆本仓库，按目录取用即可；
# 单机部署：克隆一次，所有内容都在本地。
```

> 目录边界按「可拆分」设计：`ops-doctor/` 完全自包含，将来若要独立成单独的开源仓库，把它提出来即可，代码零改动。

## 1. 拓扑方案

| 方案 | 布局 | 资源建议 | 适用 |
| --- | --- | --- | --- |
| **A：单机最小**（推荐起步） | 1 台 VM：仅 OpsDoctor（内置模拟数据源） | 2 vCPU / 2 GB | 10 分钟验证 Agent 全部能力，零外部依赖 |
| **B：单机全家桶** | 1 台 VM：OpsDoctor + mall + 中间件 + Zabbix | 8 vCPU / 16 GB | 演示资源紧张时；注意分阶段启动 |
| **C：双机**（推荐演示） | VM1 = mall + MySQL/Redis/RabbitMQ（6–8 GB）；VM2 = OpsDoctor + Zabbix（8–10 GB） | 合计 16 GB 宿主即可 | 架构文档推荐；链路最清晰 |

LLM 走外部 API（OpenAI 兼容端点），不占本地方案资源；内网环境可配内部网关。

## 2. 端口矩阵

| 组件 | 端口 | 允许访问方 |
| --- | --- | --- |
| OpsDoctor API | 8000 | Zabbix 服务器 + 运维网段 |
| mall-portal | 8085（以固定版本为准） | 实验网段 |
| mall Actuator/Prometheus | 随应用端口 | 仅 Zabbix / 运维网段 |
| MySQL | 3306 | 仅 VM 内部 |
| Redis | 6379 | 仅 VM 内部 |
| RabbitMQ AMQP / 管理 | 5672 / 15672 | 仅 VM 内部 / 管理网段 |
| Zabbix Web | 80 / 443 | 管理网段 |
| Zabbix Server / Agent2 | 10051 / 10050 | 组件互访 |

原则：所有端口不暴露公网；VM 间只开上表所需端口（`ufw`/安全组）；双机部署先验证互通：

```bash
# VM2 → VM1（OpsDoctor 查 mall 指标走 Zabbix API，不直连 mall；此测试验证网络即可）
ping -c2 <VM1_IP> && curl -sI http://<VM1_IP>:8085/actuator/health | head -1
# VM1 → VM2（webhook 推送方向，必须通）
curl -s http://<VM2_IP>:8000/health
```

## 3. 部署顺序总览

```
阶段1 OpsDoctor ──▶ 阶段2 LLM(可选) ──▶ 阶段3 mall+中间件 ──▶ 阶段4 Zabbix
                                                            │
阶段6 联调演练 ◀── 阶段5 打通监控链路（mall→Zabbix→OpsDoctor）◀┘
```

每个阶段都有「验证成功判据」，**不达标不进入下一阶段**。回退方法：各组件独立，删除对应目录/容器即可，互不影响。

### 阶段 1：部署 OpsDoctor（约 10 分钟）

按 **ops-doctor 仓库 `docs/DEPLOYMENT.md`** §3–§7 执行。

**成功判据**：`/health` 返回 UP；`python -m app.cli simulate` 跑出 RESOLVED 完整闭环；`data/notifications.jsonl` 有通知记录。

### 阶段 2：配置 LLM（可选，5 分钟）

```bash
.venv/bin/python -m app.cli setup   # 填 Base URL / API Key / 模型名
curl -s http://127.0.0.1:8000/health | grep llm   # "configured"
```

**成功判据**：health 显示 `llm: configured`；对一条新告警的调查报告 `investigator=llm`。
失败不阻塞：LLM 不可用时系统自动回退规则诊断（会明确标注），后续修好再配。

### 阶段 3：部署 mall + 中间件（半天）

按本仓库 [README.md](README.md)：固定 mall 版本 → `docker compose up -d mysql redis rabbitmq` → 导入官方 SQL → 编译启动 mall-portal。

**成功判据**：三个中间件容器 healthy；`/actuator/health` 返回 UP；能查商品、创建订单、查订单状态。

### 阶段 4：部署 Zabbix（半天）

Ubuntu 包或官方容器方式均可（以 Zabbix 官方文档当前版本为准）。装好后：

1. Web 登录正常；
2. 添加 Host `mall-server-01`（指向 VM1）；
3. 先只配一个 HTTP agent 监控项（mall `/actuator/health`）+ 一个触发器；
4. 按帧再逐步加 RabbitMQ 队列指标。

**成功判据**：Zabbix 最新数据里能看到 mall 健康值；手动停一下测试服务能变 PROBLEM。

### 阶段 5：打通监控链路（1–2 小时）

1. OpsDoctor `.env`：`OPSDOCTOR_MONITORING_SOURCE=zabbix` + Zabbix 地址 + 只读 API Token，重启；
2. Zabbix 配置 Webhook Media Type + Action（模板 `ops-doctor/scripts/zabbix_webhook.js`，步骤见对接文档）；
3. 验证：

```bash
# 在 Zabbix 触发一次测试告警后，OpsDoctor 侧：
python -m app.cli status                 # 出现同 event_id 的事故
# 让 Zabbix 对同一事件重复推送 3 次 → OpsDoctor 仍只有 1 条事故（幂等去重）
```

**成功判据**：Zabbix Problem → OpsDoctor 收到并自动调查 → 待审批/升级；Recovery 推送被关联到原事故。

### 阶段 6：联调演练与验收（半天）

按 mall-deploy/README.md §4 打开演练开关，完成三条验收链路并保留日志/截图：

| # | 演练 | 操作 | 预期 |
| --- | --- | --- | --- |
| 1 | 成功恢复 | 暂停消费者 → 告警 → 审批批准 | 积压回落 → 复验通过 → RESOLVED → 恢复通知 |
| 2 | 审批拒绝 | 同上，但审批选拒绝 | 无任何动作执行 → ESCALATED → 拒绝通知 |
| 3 | 假恢复拦截 | `stuck-rabbitmq` 场景（mock）或执行后人为保持业务故障 | 执行返回成功但复验失败 → ESCALATED，**绝不出现 RESOLVED** |

```bash
# 每条演练后收集证据
python -m app.cli show <incident_id> > evidence-<n>.txt
cat data/notifications.jsonl | tail -5 >> evidence-<n>.txt
python -m app.cli weekly
```

## 4. 日常运维

**备份矩阵**：

| 数据 | 位置 | 方式 | 频率 |
| --- | --- | --- | --- |
| OpsDoctor 状态 | `data/`（数据库/令牌/通知） | ops-doctor 仓库 `scripts/backup.sh` | 每日 cron |
| mall 数据库 | docker volume `mysql-data` | mysqldump | 每日 cron |
| 配置 | 各仓库 `.env` | 纳入运维私有保管（不入 Git） | 变更时 |

**升级顺序**：组件间只通过 Zabbix API 与 Webhook 弱耦合，可独立升级：先备份 → 升 OpsDoctor（验证 /health + simulate）→ 升 mall（验证业务链）→ 升 Zabbix（验证事件与 webhook）。任何一步失败回退该组件即可，不影响其他组件运行。

**时间同步**：跨 VM 部署必须配置 NTP/chrony（`timedatectl status` 确认同步），否则审批过期判断与时间窗口会错乱。

## 5. 全链路故障排查速查

| 现象 | 优先检查 |
| --- | --- |
| Zabbix 显示告警但 OpsDoctor 没收到 | Zabbix Action 是否启用、Media type 测试按钮、`curl <VM2>:8000/health` 网络连通、token 一致 |
| OpsDoctor 收到但不去重 | 比对 `source_event_id` 是否稳定（webhook 参数映射） |
| 调查一直缺数据 | OpsDoctor 的 Zabbix Token 权限（event/problem/item/host.get）；`OPSDOCTOR_MONITORING_SOURCE` 是否切到 zabbix |
| 执行动作后复验失败 | 先看这是否就是「假恢复拦截」在正确工作（预期行为）；检查演练开关是否真的恢复了消费者 |
| 双机不通 | `ss -lntp` 监听地址、ufw/安全组、NAT 网络 |
| mall 起不来 | 按 mall 固定版本的 `application-*.yml` 核对中间件地址与口令 |
| 时间对不上导致审批异常 | chrony 同步、时区（存储 UTC、显示 Asia/Shanghai） |

各组件内部排障见各自手册的故障排查章节。
