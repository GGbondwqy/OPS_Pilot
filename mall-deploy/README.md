# mall 电商系统 Linux 部署套件（项目 B）

[mall](https://github.com/macrozheng/mall) 是开源 Java 电商项目（Spring Boot 3.5+ / JDK 17），本目录提供把它部署到 Linux 作为 **OpsDoctor 诊断目标** 的部署清单与演练说明。mall 本身的业务代码不做修改，我们在其上补充可观测性与故障演练能力。

> **部署整套系统（OpsDoctor + mall + Zabbix）请先看全链路部署总纲：[DEPLOYMENT.md](DEPLOYMENT.md)**——含拓扑方案、部署顺序、端口矩阵与逐阶段验证。本 README 只覆盖 mall 自身的部署与改造。

## 0. 强制步骤：先固定版本

mall 主线更新较快，教程可能落后于主线。部署前必须先确定 commit/tag：

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/macrozheng/mall.git
cd mall
git rev-parse --short HEAD        # 记下 commit SHA
git branch --show-current
java -version                      # 需要 JDK 17
mvn -version
find mall-portal/src/main/resources -maxdepth 2 -type f | head -30
```

然后**查看该版本**的 `mall-portal/src/main/resources/application*.yml` 与 `pom.xml`，列出实际依赖的中间件（MySQL / Redis / RabbitMQ / MongoDB / Elasticsearch…），按需启动——不要照抄旧教程的 Compose 文件。

## 1. 中间件（docker compose 方式）

`docker-compose.yml` 提供 MySQL 8 / Redis 7 / RabbitMQ 3.12 三个基础组件（版本请按你固定的 mall 版本核对调整）：

```bash
cd mall-deploy
docker compose up -d mysql redis rabbitmq
docker compose ps

# 健康检查
docker compose exec mysql mysqladmin ping -uroot -proot
docker compose exec redis redis-cli ping          # PONG
curl -s http://127.0.0.1:15672/api/overview -u mall:mall | head -c 200
```

RabbitMQ 管理界面：http://<主机IP>:15672（mall/mall，仅限内网访问）。

## 2. 编译并启动 mall 模块

```bash
cd ~/projects/mall

# 导入官方 SQL（文件名以固定版本为准）
docker compose -f ../mall-deploy/docker-compose.yml exec -T mysql \
  mysql -uroot -proot < documents/sql/mall.sql

# 编译目标模块（含其依赖）
mvn -pl mall-portal -am clean package -DskipTests

# 启动（示例；JVM 参数、profile 按实际版本调整）
java -jar mall-portal/target/*.jar --spring.profiles.active=dev
```

启动成功判据：

- 日志无认证失败 / Bean 注入异常；
- `curl http://127.0.0.1:8085/actuator/health` 返回 `{"status":"UP"}`（端口以固定版本配置为准）；
- 商品/订单接口可正常返回（用 Swagger 确认路径，不要凭记忆猜 URL）。

> 提示：完整检索模块（mall-search）需要 Elasticsearch，第一阶段不启动，先跑通订单核心链路。

## 3. 可观测性改造（我们在 mall 上的自研部分）

在 mall 的 `pom.xml` 中加入（版本与 Spring Boot 匹配）：

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

对应 Profile 增量配置（合并进 `application-*.yml`，不要覆盖原文件）：

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  endpoint:
    health:
      show-details: never   # 不向无权限者暴露内部细节
```

建议新增的业务指标（低基数标签，禁止用订单号/用户 ID 当 tag）：

| 指标 | 类型 | 含义 |
| --- | --- | --- |
| `mall_order_created_total` | Counter | 创建订单次数 |
| `mall_order_create_failed_total` | Counter | 创建订单失败次数 |
| `mall_order_processing_seconds` | Timer | 订单处理耗时 |
| `mall_inventory_reject_total` | Counter | 库存不足拒绝次数 |
| `mall_order_consistency_violation_total` | Counter | 业务不变量异常（仅实验） |

## 4. 故障演练开关（仅实验环境）

为演示「消费者停止 → 队列积压 → 告警 → OpsDoctor 闭环」，需要一个可控的演练手段，例如：

- 在测试 Profile 提供一个仅内网可达的演练端点：`POST /ops-demo/consumer/pause`、`POST /ops-demo/consumer/resume`；
- 或直接用 `docker compose stop mall-portal` / `start mall-portal` 模拟服务级故障；
- 或在 RabbitMQ 管理界面手动移除消费者连接。

**禁止在生产环境执行任何演练。**

## 5. 与 Zabbix、OpsDoctor 的联调链路

```
mall (Actuator/指标) ──采集──▶ Zabbix ──Trigger──▶ Webhook ──▶ OpsDoctor
                                                                 │
        mall-portal ◀──预定义动作(审批后)── 复验通过 ◀── 调查/审批 ──┘
```

1. Zabbix 添加 `mall-server-01` Host，先只配 `/actuator/health` 一个监控项 + 一个 Trigger；
2. 配置 Webhook Media Type 推送到 OpsDoctor（模板见 `ops-doctor/scripts/zabbix_webhook.js`，步骤见 `ops-doctor/docs/zabbix-integration.md`）；
3. 暂停消费者（演练开关）→ 观察：Zabbix Problem → OpsDoctor 收告警 → 调查 → 待审批 → 审批 → 恢复动作 → 复验通过 → RESOLVED；
4. 记录演练时间线与截图（验收要求保留 1 条成功恢复、1 条审批拒绝、1 条未恢复升级）。

## 6. 部署检查清单

- [ ] 记录 mall 的 commit SHA、JDK/Maven 版本、Spring Profile
- [ ] 中间件版本与固定版本 mall 的要求核对过
- [ ] 官方 SQL 导入成功、关键表可见
- [ ] mall 模块启动无异常，`/actuator/health` 返回 UP
- [ ] 走通一条业务链：查商品 → 创建订单 → 查订单状态
- [ ] Prometheus 指标端点可访问（仅运维网段）
- [ ] 演练开关只在测试 Profile 生效
- [ ] 数据库用独立账号独立库，口令不入 Git
- [ ] OpsDoctor 能收到 Zabbix 告警且重复推送被去重
