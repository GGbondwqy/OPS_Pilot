"""RAG 检索单测（验收标准：可检索、带来源与版本；未知问题正确拒答）。"""

from __future__ import annotations

from app.rag import retrieve_runbook


def test_rabbitmq_query_hits_correct_runbook():
    hits = retrieve_runbook("RabbitMQ 队列积压 消费者停止", k=3)
    assert hits, "RabbitMQ 场景应能检索到 Runbook"
    assert hits[0]["doc_id"] == "RUNBOOK-RMQ-001"
    assert hits[0]["source_path"]
    assert hits[0]["version"]
    assert hits[0]["score"] > 0


def test_disk_query_hits_disk_runbook():
    hits = retrieve_runbook("磁盘使用率过高 No space left", k=3)
    assert hits
    assert any(h["doc_id"] == "RUNBOOK-DISK-001" for h in hits[:2])


def test_unknown_problem_returns_empty():
    hits = retrieve_runbook("量子计算机过热导致机房空调爆炸", k=3)
    assert hits == [], "知识库没有的问题应返回空（INSUFFICIENT），不允许编造"


def test_service_filter_downweights_others():
    hits = retrieve_runbook("数据库连接失败", service="mall-admin", k=3)
    assert hits
    assert hits[0]["doc_id"] in {"RUNBOOK-MYSQL-001", "RUNBOOK-REDIS-001"}


def test_results_have_required_fields():
    hits = retrieve_runbook("Nginx 502", k=1)
    if hits:
        h = hits[0]
        for key in {"doc_id", "title", "section", "excerpt", "score", "source_path", "version"}:
            assert key in h
