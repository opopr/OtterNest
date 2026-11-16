"""Tests for vector embedding and hybrid search engine."""
from __future__ import annotations

import pytest

from otternest.models import Memory
from otternest.vector import (
    cosine_similarity,
    dumps_embedding,
    embed_text,
    loads_embedding,
    search_hybrid,
)


def test_vector_deterministic_and_normalized():
    vec1 = embed_text("系统架构设计与微服务拆分")
    vec2 = embed_text("系统架构设计与微服务拆分")
    assert vec1 == vec2
    assert len(vec1) == 64
    # Norm check (unit length)
    import math
    norm = math.sqrt(sum(x * x for x in vec1))
    assert 0.99 <= norm <= 1.01


def test_vector_cosine_similarity():
    v1 = embed_text("PostgreSQL 数据库并发测试")
    v2 = embed_text("PostgreSQL 数据库高并发与事务")
    v3 = embed_text("前端界面主题与样式优化")

    sim_similar = cosine_similarity(v1, v2)
    sim_different = cosine_similarity(v1, v3)
    assert sim_similar > sim_different
    assert 0.5 <= sim_similar <= 1.0


def test_embedding_serialization():
    vec = [0.123, -0.456, 0.789]
    serialized = dumps_embedding(vec)
    deserialized = loads_embedding(serialized)
    assert deserialized == pytest.approx(vec)


def test_search_hybrid_ranking():
    m1 = Memory(
        id="m1", project_id="p1", kind="fact",
        title="PostgreSQL 事务锁管理",
        content="在并发写入任务时，通过 SELECT FOR UPDATE 行级锁避免竞态条件。",
        source="doc", sensitivity="internal", status="published", version=2,
    )
    m2 = Memory(
        id="m2", project_id="p1", kind="fact",
        title="Web 控制台前端重构",
        content="使用原生 CSS Token 支持浅深色切换并优化模态对话框交互体验。",
        source="doc", sensitivity="internal", status="published", version=1,
    )
    m3 = Memory(
        id="m3", project_id="p1", kind="fact",
        title="受限高敏感度配置",
        content="内部私有密钥仅对 restricted 权限主体开放。",
        source="doc", sensitivity="restricted", status="published", version=1,
    )

    memories = [m1, m2, m3]

    # Search for database concurrency with 'internal' sensitivity
    results = search_hybrid(memories, "PostgreSQL 事务并发锁", allowed_sensitivity="internal")
    assert len(results) >= 1
    assert results[0]["id"] == "m1"
    assert "dense_similarity" in results[0]
    assert "score" in results[0]
    # m3 should be filtered out by sensitivity
    assert not any(r["id"] == "m3" for r in results)

    # Search with 'restricted' sensitivity allows m3
    results_restricted = search_hybrid(memories, "私有密钥", allowed_sensitivity="restricted")
    assert any(r["id"] == "m3" for r in results_restricted)
