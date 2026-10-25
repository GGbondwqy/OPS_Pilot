# Zabbix 对接指南

目标：Zabbix 产生告警 → Webhook 推送到 OpsPilot → 自动调查与闭环。

## 第一步：OpsPilot 侧准备

1. 编辑 `.env`（或 `python -m app.cli setup` 向导）：

```ini
OPSPILOT_MONITORING_SOURCE=zabbix
OPSPILOT_ZABBIX_URL=http://<zabbix服务器地址>
OPSPILOT_ZABBIX_TOKEN=<只读API Token>
```

> Token 创建：Zabbix → User settings → API tokens（或 Users → API tokens，按版本 UI 有差异）。授予 `event.get`、`problem.get`、`item.get`、`host.get` 只读权限的最小用户。

2. 获取 Webhook 回调令牌：

```bash
cat data/webhook_token
```

（若尚未启动过服务，先运行 `python -m app.cli init` 自动生成。）

3. 对外暴露服务（供 Zabbix 访问）：`.env` 设置 `OPSPILOT_HOST=0.0.0.0`，重启服务。确保仅监控网段可访问 8000 端口（防火墙/安全组限制），不要暴露公网。

## 第二步：Zabbix 侧配置

按顺序执行，每步验证后再进行下一步：

### 2.1 创建 Host

- Data collection → Hosts → Create host（名称如 `mall-server-01`，指向业务机 IP）。

### 2.2 最小监控项（先跑通再扩展）

| 监控项 | 类型 | 说明 |
| --- | --- | --- |
| 服务健康 | HTTP agent | URL 指向 `/actuator/health`，检查状态码/body |
| 队列积压 | RabbitMQ 模板或管理 API item | `rabbitmq_queue_messages_ready` |
| 消费者数 | 同上 | `rabbitmq_consumers` |

### 2.3 创建 Trigger（避免误报）

示例语义：**连续 2~3 次采样**积压 > 100 且消费者 = 0 才触发 PROBLEM；恢复时产生 RECOVERY 事件。先建 1~2 个触发器，不要一次配 20 个。

### 2.4 配置 Webhook Media Type

1. Alerts → Media types → Create media type：
   - Type 选 **Webhook**；
   - Parameters 添加（见下表）；
   - Script 粘贴 [scripts/zabbix_webhook.js](../scripts/zabbix_webhook.js) 的内容。

| 参数名 | 值 |
| --- | --- |
| `opspilot_url` | `http://<opspilot主机>:8000/webhook/zabbix` |
| `opspilot_token` | `data/webhook_token` 文件内容 |
| `event_id` | `{EVENT.ID}` |
| `event_kind` | `{EVENT.OBJECT}` |
| `host` | `{HOST.NAME}` |
| `problem` | `{EVENT.NAME}` |
| `severity` | `{EVENT.SEVERITY}` |
| `occurred_at` | `{EVENT.DATE}T{EVENT.TIME}` |

2. Message 模板：分别为 Problem 与 Recovery 操作各定义一条（Recovery 事件必须能推送出来，OpsPilot 用它做监控对账）。

### 2.5 配置 Action

- Alerts → Actions → Trigger actions → Create action：
  - Conditions：选好触发器/主机组；
  - Operations：Send message to 用户（该用户绑定上面的 Webhook media type）；
  - Recovery operations：同样发送 → 保证 PROBLEM/RECOVERY 都会推送。

### 2.6 验证

1. 在 Zabbix 手动触发一个实验告警（或停一个测试服务）；
2. 观察 Monitoring → Problems 出现告警；
3. OpsPilot 侧 `python -m app.cli status` 应出现同 event_id 的事故；
4. 让 Zabbix 重复推送同一事件 3 次 → OpsPilot 侧仍只有 1 条事故（幂等去重）；
5. 修复后观察 RECOVERY 推送 → 事故 `recovery_seen_at` 被记录（用于监控对账）。

## 载荷契约

OpsPilot 只接受内部标准格式（不是 Zabbix 原生 JSON），Webhook 脚本负责映射：

```json
{
  "source": "zabbix",
  "event_id": "10001",
  "event_kind": "PROBLEM",
  "host": "mall-server-01",
  "service": "mall-portal",
  "problem": "RabbitMQ 队列积压超过阈值",
  "severity": "HIGH",
  "occurred_at": "2026-10-24T19:00:00+08:00",
  "metric": "rabbitmq_queue_messages_ready",
  "value": 130,
  "threshold": 100,
  "tags": {"env": "lab", "system": "mall"}
}
```

要点：

- **RECOVERY 事件的 `event_id` 必须填原 PROBLEM 事件的 ID**（Zabbix 的恢复事件自身 ID 放可选字段），这样恢复事件才能与原事故关联，而不是生成新事故；
- 幂等键为 `(source, event_id, event_kind, payload_hash)`：同载荷重试只入库一次；
- 鉴权失败返回 401，载荷非法返回 422，成功返回 202（调查异步进行）。

## 安全注意事项

- `opspilot_token` 不要打印到 Zabbix 告警日志或 Notification message 模板；
- OpsPilot 8000 端口只对监控网段开放；
- 先在测试主机验证 Media Type（Media type 的 Test 按钮），再挂到真实 Action。
