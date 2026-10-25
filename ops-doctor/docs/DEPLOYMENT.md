# OpsDoctor 部署手册

适用环境：Linux（Ubuntu 22.04/24.04、Debian 12、Rocky 9 等，其他发行版替换包管理命令即可）。
本文覆盖 OpsDoctor 自身的完整部署：安装、常驻服务、对外暴露、备份升级、安全加固与排障。

> 只想快速体验？看 [README 快速开始](../README.md)。
> 要部署整套系统（OpsDoctor + mall + Zabbix）？看 mall-deploy 仓库的《全链路部署总纲》。

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| 系统 | Linux x86_64 / arm64 |
| Python | ≥ 3.10（Ubuntu 22.04/24.04 系统自带满足） |
| 磁盘 / 内存 | ≥ 2 GB 磁盘；OpsDoctor 本体 < 300 MB 内存 |
| 依赖服务 | 无强制依赖（SQLite 内置）；可选接入 Zabbix 只读 API |
| 网络 | 出网：访问 LLM API（若配置）；入网：仅监控/内网网段 |

## 2. 三种部署形态

| 形态 | 适用场景 | 参考章节 |
| --- | --- | --- |
| A. 独立最小部署 | 只跑 OpsDoctor + 内置模拟数据源，开箱演示闭环 | 本文 §3–§6 |
| B. 接入现有 Zabbix | 已有 Zabbix，把告警接入 Agent | 本文 §5 + [zabbix-integration.md](zabbix-integration.md) |
| C. 全链路演示 | mall + Zabbix + OpsDoctor 完整链路 | mall-deploy 仓库《全链路部署总纲》 |

## 3. 标准安装

以部署到 `/opt/ops-doctor` 为例（也可装在用户家目录，把 `/opt/ops-doctor` 换掉即可）：

```bash
# 1) 基础依赖
sudo apt update && sudo apt install -y git curl ca-certificates python3-venv

# 2) 获取代码
sudo mkdir -p /opt && sudo chown "$USER" /opt
cd /opt
git clone https://github.com/GGbondwqy/OpsDoctor.git
cd OpsDoctor/ops-doctor

# 3) 一键安装（venv + 依赖 + .env 模板 + 数据库 + 知识库索引 + 自动生成令牌）
bash scripts/install.sh

# 4) 验证
.venv/bin/python -m app.cli info
.venv/bin/python -m app.cli init
```

**离线/内网环境**（目标机不能出网）：

```bash
# 在有网的同架构机器上：
pip download -r requirements.txt -d vendor/
# 把整个项目目录（含 vendor/）拷贝到目标机，然后：
python3 -m venv .venv && source .venv/bin/activate
pip install --no-index --find-links=vendor/ -r requirements.txt
python -m app.cli init
```

**容器方式**（可选，与 systemd 二选一）：

```bash
docker build -t ops-doctor:latest .
docker run -d --name ops-doctor -p 8000:8000 -v ops-doctor-data:/data \
  -e OPSDOCTOR_LLM_BASE_URL=https://api.deepseek.com/v1 \
  -e OPSDOCTOR_LLM_API_KEY=sk-xxx \
  -e OPSDOCTOR_LLM_MODEL=deepseek-chat \
  ops-doctor:latest
```

## 4. 配置

配置读取优先级：环境变量 > `.env` 文件 > 默认值。三项 AI 模型配置**留空即用**（规则诊断模式）。

### 4.1 交互式向导（推荐）

```bash
.venv/bin/python -m app.cli setup
```

按提示填写：AI 模型（可回车跳过）→ 监控数据源 → 通知渠道。配置写入 `.env`。

### 4.2 常用配置速查

| 配置 | 默认 | 何时需要改 |
| --- | --- | --- |
| `OPSDOCTOR_HOST` | `127.0.0.1` | Zabbix/其他机器要访问时改 `0.0.0.0`（配合防火墙） |
| `OPSDOCTOR_PORT` | `8000` | 端口冲突时 |
| `OPSDOCTOR_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | 空 | 启用大模型诊断时三项都填 |
| `OPSDOCTOR_MONITORING_SOURCE` | `mock` | 接真实 Zabbix 改 `zabbix` |
| `OPSDOCTOR_ZABBIX_URL` / `_TOKEN` | 空 | 接 Zabbix 时填（Token 需只读权限） |
| `OPSDOCTOR_NOTIFICATION_MODE` | `file` | 要发钉钉/自建系统时改 `dingtalk`/`webhook` |
| `OPSDOCTOR_RUN_ENVIRONMENT` | `lab` | **保持 `lab`**；其他值会禁用一切修复动作 |
| `OPSDOCTOR_WEBHOOK_TOKEN` | 自动生成 | 需要固定令牌时手动指定 |

令牌说明：`OPSDOCTOR_WEBHOOK_TOKEN` 与 `OPSDOCTOR_ADMIN_TOKEN` 留空时，首次启动自动生成强随机值并写入 `data/webhook_token`、`data/admin_token`。**轮换方法**：删除这两个文件并重启服务（注意同步更新 Zabbix Webhook 参数）。

完整清单见 [.env.example](../.env.example)。

## 5. 对外暴露与防火墙

OpsDoctor 有两类调用方，按需暴露：

| 调用方 | 接口 | 开放范围 |
| --- | --- | --- |
| Zabbix Webhook | `POST /webhook/zabbix` | 仅 Zabbix 服务器所在网段 |
| 运维人员 / CLI | 其余接口 | 仅运维网段或本机 |

```bash
# Ubuntu ufw 示例（把网段换成实际值）
sudo ufw allow from 10.0.0.0/24 to any port 8000 proto tcp comment 'zabbix->ops-doctor'
# 其他来源默认拒绝；确认 ufw 启用
sudo ufw status verbose
```

原则：

- **不要把 8000 端口暴露到公网**。审批接口虽有管理令牌保护，但应留在内网；
- `/docs`（Swagger）仅用于调试，生产环境建议通过反代屏蔽或限制网段；
- 跨机访问时 `.env` 必须 `OPSDOCTOR_HOST=0.0.0.0` 并重启服务。

### 可选：Nginx 反向代理 + HTTPS

Zabbix Webhook 脚本默认走内网 HTTP 即可；若必须走 HTTPS：

```nginx
server {
    listen 8443 ssl;
    server_name ops-doctor.internal.example.com;
    ssl_certificate     /etc/nginx/ssl/ops-doctor.crt;
    ssl_certificate_key /etc/nginx/ssl/ops-doctor.key;

    # 生产建议屏蔽交互式文档
    location /docs { deny all; }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

此时 Zabbix Webhook 参数 `ops-doctor_url` 填 `https://...:8443/webhook/zabbix`，需保证 Zabbix 信任证书（内网自签证书需导入信任或用 IP 直连 http）。

## 6. 常驻运行（systemd，推荐）

```bash
# 1) （推荐）专用系统用户运行
sudo useradd -r -s /usr/sbin/nologin ops-doctor
sudo chown -R ops-doctor:ops-doctor /opt/ops-doctor

# 2) 安装服务（把路径替换为实际部署路径）
sudo cp scripts/ops-doctor.service /etc/systemd/system/ops-doctor.service
sudo sed -i 's#/opt/ops-doctor#/opt/ops-doctor#g' /etc/systemd/system/ops-doctor.service  # 路径不同才需要改
sudo systemctl daemon-reload
sudo systemctl enable --now ops-doctor

# 3) 检查
systemctl status ops-doctor
curl -s http://127.0.0.1:8000/health
journalctl -u ops-doctor -f          # 实时日志
```

前置条件：`.env` 中需有 `OPSDOCTOR_HOST` 与 `OPSDOCTOR_PORT` 两行（`install.sh` 复制的模板自带；手动配置过的请确认）。

没有 systemd 的环境：用 `nohup bash scripts/start.sh &` 或 supervisor 托管。

## 7. 部署验证清单

```bash
# 1) 健康检查
curl -s http://127.0.0.1:8000/health     # {"status":"UP", ...}

# 2) 完整闭环演练（mock 数据源）
.venv/bin/python -m app.cli simulate
sleep 5 && .venv/bin/python -m app.cli status       # 出现 NEEDS_APPROVAL
.venv/bin/python -m app.cli approve 1 --approver ops
.venv/bin/python -m app.cli status                  # RESOLVED

# 3) 幂等验证：再执行一次 simulate（不同 event_id 会是新事故；同 event_id 重发是去重）
# 4) 通知与周报
cat data/notifications.jsonl
.venv/bin/python -m app.cli weekly
```

接真实 Zabbix 后的验证步骤见 [zabbix-integration.md](zabbix-integration.md) §2.6。

## 8. 备份与恢复

`data/` 目录包含全部运行状态（数据库、令牌、通知、模拟状态）。使用内置脚本：

```bash
bash scripts/backup.sh                  # 备份到 backups/ops-doctor-<时间戳>/
bash scripts/backup.sh /mnt/nas/backup  # 备份到指定目录
```

恢复：停止服务 → 用备份覆盖 `data/`（`env.backup` 改名 `.env`）→ 启动服务 → `python -m app.cli init`。

建议：变更/升级前必备份；生产化后用 cron 每日备份并异地存放（备份含令牌与 `.env`，注意保管）。

## 9. 升级流程

```bash
bash scripts/backup.sh
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.cli init
sudo systemctl restart ops-doctor
curl -s http://127.0.0.1:8000/health
```

> 注意：MVP 阶段未引入数据库迁移工具（Alembic），升级前**必须**先备份；跨大版本升级请看仓库 CHANGELOG/Release Notes 的破坏性变更说明。

## 10. 安全加固清单

- [ ] 专用低权限用户运行（非 root），`scripts/ops-doctor.service` 已带 NoNewPrivileges/ProtectSystem
- [ ] `.env` 权限 600；绝不提交到 Git（`.gitignore` 已排除）
- [ ] 8000 端口仅对监控/运维网段开放，无公网暴露
- [ ] Webhook / Admin 双令牌分离，定期轮换（删 `data/*_token` 重启）
- [ ] `OPSDOCTOR_RUN_ENVIRONMENT=lab` 保持不变，除非你明确实现并测试了真实动作执行器
- [ ] MCP 只读服务（若启用）仅供可信 MCP Client 使用，不存在任何写接口
- [ ] 日志/通知中不含凭据（系统设计已脱敏，抽查 `data/notifications.jsonl`）

## 11. 故障排查

| 现象 | 优先检查 | 处理 |
| --- | --- | --- |
| systemd 启动失败 | `journalctl -u ops-doctor -n 50` | 部署路径不对、Python 版本、`.env` 缺 HOST/PORT 行 |
| `/health` 不通 | 端口占用 `ss -lntp`；监听地址 | 改端口；HOST 需 `0.0.0.0` 才能被外部访问 |
| Webhook 返回 401 | Zabbix 参数里的 token vs `data/webhook_token` | 轮换过令牌后必须同步 Zabbix |
| Webhook 返回 422 | 载荷字段/类型 | 对照 [zabbix-integration.md](zabbix-integration.md) 载荷契约 |
| 事故一直 RECEIVED | worker 是否在跑 | 默认 `OPSDOCTOR_WORKER_ENABLED=true`；看日志；或手动 `.venv/bin/python -m app.cli worker-once` |
| LLM 调查失败（事故被升级） | `.env` 三项是否齐全、目标机能否出网 | 修好后 `python -m app.cli investigate <id>` 重试 |
| Zabbix API 报错 | Token 权限、URL 是否到 `api_jsonrpc.php` | 用只读 Token（event/problem/item/host.get）；URL 填 Zabbix 根地址 |
| `database is locked` | 是否多进程共库 | MVP 单实例设计；不要多 worker/多副本共用同一 data 目录 |
| 检索总是 INSUFFICIENT | 知识库为空 | 确认 `knowledge/runbooks/` 存在；重跑 `python -m app.cli init` |
| 审批单显示已过期 | 30 分钟 TTL | 重新发起审批；或调 `repository.py` 中 `APPROVAL_TTL_MINUTES` |

## 12. 卸载

```bash
sudo systemctl disable --now ops-doctor
sudo rm /etc/systemd/system/ops-doctor.service
rm -rf /opt/ops-doctor          # data/ 里的历史数据随之删除，需要留档先 backup
```
