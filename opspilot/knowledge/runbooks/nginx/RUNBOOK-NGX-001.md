---
doc_id: RUNBOOK-NGX-001
title: Nginx 5xx 比例异常升高排查
service: mall-portal
version: 1
risk_level: medium
last_reviewed: 2026-10-24
---

## 症状
Nginx 返回 5xx 的比例异常升高（如 1 分钟内超过 5%），用户请求大量失败，上游（应用服务）报连接错误或超时。

## 只读诊断
1. 确认 5xx 集中在哪个 upstream、哪个 location。
2. 检查对应应用服务健康状态（/actuator/health）与进程是否存活。
3. 查看应用错误日志：连接池耗尽、OOM、依赖超时是最常见原因。
4. 核对最近发布与配置变更时间点。

## 处置前提
重启测试服务（如 restart_lab_consumer / 预登记的进程动作）必须审批后执行；不要盲目重启 Nginx 本身。

## 成功验收
5xx 比例回落到基线水平，应用健康检查恢复 UP，用户请求成功率恢复。

## 升级条件
应用反复假恢复（进程在但业务不通过）、所有 upstream 同时故障、或疑似基础设施级故障，升级人工。
