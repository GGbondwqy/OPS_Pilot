---
doc_id: RUNBOOK-MYSQL-001
title: MySQL 连接失败排查
service: mall-admin
version: 1
risk_level: high
last_reviewed: 2026-10-24
---

## 症状
应用报数据库连接失败（connection refused / timeout / too many connections），业务接口大面积 5xx。

## 只读诊断
1. 确认数据库进程存活与端口可达（ping 风格指标 mysql_ping_status）。
2. 检查连接数是否耗尽、最近是否有慢查询导致堆积。
3. 查看应用侧连接池配置与错误日志。
4. 核对最近是否有数据库配置/网络变更。

## 处置前提
数据库属于高风险对象：任何针对 MySQL 的修复动作（repair_order_data、restart_mysql_production）均禁止自动执行，一律升级人工；Agent 只允许收集只读证据。

## 成功验收
由人工完成处理后，数据库连通性恢复，业务接口错误率回落，订单创建/查询正常。

## 升级条件
本 Runbook 默认全部升级人工。Agent 的价值在于快速收集证据（连接数、错误日志、变更时间线）供人工决策。
