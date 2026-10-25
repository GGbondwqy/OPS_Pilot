---
doc_id: RUNBOOK-RMQ-001
title: RabbitMQ 消费者停止与队列积压排查
service: mall-portal
version: 1
risk_level: medium
last_reviewed: 2026-10-24
---

## 症状
队列消息积压（messages_ready）持续增加，消费者数（consumers）降为 0；订单异步状态不再推进；管理界面显示队列无消费者。

## 只读诊断
1. 读取队列 ready / unacked / consumers 三个指标，确认积压量与消费者数量。
2. 检查应用健康状态（/actuator/health）与最近的错误日志。
3. 核对最近一次部署时间与消费者的连接错误记录。
4. 确认 RabbitMQ Broker 本身可达、其他队列是否有消费者。

## 处置前提
确认影响范围仅为实验环境；对测试环境的消费者执行预定义恢复动作（restart_lab_consumer）必须先经过人工审批；禁止直接向队列注入或删除消息。

## 成功验收
消费者数恢复为大于 0，队列积压呈持续下降趋势，订单异步状态恢复正常推进。

## 升级条件
消费者反复崩溃、消息无法消费（疑似 poison message）、出现订单数据不一致，立即升级人工处理。
