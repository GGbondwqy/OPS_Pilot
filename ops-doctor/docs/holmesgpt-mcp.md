# HolmesGPT / MCP 接入指南（进阶）

OpsDoctor 自带的调查器（LLM 或规则模式）已能满足闭环需求。如果希望复用 [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) 的 SRE 调查能力，可以把 OpsDoctor 作为 MCP Server 挂给 HolmesGPT：HolmesGPT 在调查时调用 OpsDoctor 的只读工具获取监控证据与 Runbook。

> 版本提示：MCP SDK 与 HolmesGPT 迭代较快，以下以 `mcp>=1.28,<2` 与 HolmesGPT CLI 当前文档为准，安装后先用官方最小示例验证再集成。

## 1. 启动 MCP 只读服务

```bash
pip install 'mcp>=1.28,<2'

# 先独立验证协议（不做任何模型调用）
python -m py_compile app/mcp/server.py
python -m app.mcp.server &
# stdio Server 启动后无输出是正常的：它在等待 MCP Client 连接
```

工具清单（全部只读）：

| 工具 | 输入 | 输出 |
| --- | --- | --- |
| `get_problem` | event_id | 告警详情 |
| `get_host_metrics` | host | 指标值/阈值/采集时间/来源 |
| `get_service_health` | service | 健康状态 |
| `get_queue_stats` | queue_name | 积压/消费者数 |
| `search_runbook` | query, service, k | Runbook 片段 + doc_id + 版本 |

## 2. 用 MCP Client 自测（可选但推荐）

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=".venv/bin/python", args=["-m", "app.mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])
            result = await session.call_tool("search_runbook", {"query": "RabbitMQ 积压"})
            print(result.content)

asyncio.run(main())
```

## 3. 配置 HolmesGPT

编辑 `~/.holmes/config.yaml`（修改前备份），把路径换成本机绝对路径：

```yaml
mcp_servers:
  ops-doctor_monitoring:
    description: "Read-only OpsDoctor monitoring tools (mock or Zabbix-backed)"
    config:
      mode: stdio
      command: "/home/USER/ops-doctor/.venv/bin/python"
      args:
        - "-m"
        - "app.mcp.server"
      # 在 OpsDoctor 仓库目录下运行时可用 cwd 指定；否则把路径写为绝对路径
    allowed_tools:
      - get_problem
      - get_host_metrics
      - get_service_health
      - get_queue_stats
      - search_runbook
    llm_instructions: >
      These tools return monitoring evidence and runbook excerpts.
      Prefer tool facts over speculation. Do not remediate or execute shell.
```

HolmesGPT 侧配置模型 API 后验证：

```bash
holmes toolset refresh
holmes ask 'Investigate event 10001 using OpsDoctor tools; do not remediate.'
```

## 4. 集成边界（重要）

- `allowed_tools` 只是 HolmesGPT 侧的暴露过滤，**不等于**底层权限；OpsDoctor 的 MCP 工具本身只有只读实现，不存在任何 shell/写接口；
- HolmesGPT 的输出仍然是「建议」：要执行修复，必须回到 OpsDoctor 的审批接口（白名单 + 人工审批 + 复验），不要让 HolmesGPT 直接操作目标系统；
- 调用痕迹（工具参数、返回值、模型回答）建议分开落盘，便于回放与审计；
- 没有可用的模型 API 时，仍可用第 2 步的 MCP Client 自测证明协议链路正常，但不要宣称完成了模型自主调查。
