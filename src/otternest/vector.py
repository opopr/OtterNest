"""Vector embedding and hybrid search engine for OtterNest Context Broker.

Supports semantic similarity retrieval, pgvector-compatible vector structures,
and dense + lexical hybrid ranking while preserving project tenancy and
sensitivity boundaries.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Sequence

from .models import Memory
from .security import sensitivity_allowed

EMBEDDING_DIM = 64
_WORD_REGEX = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def embed_text(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Generate a deterministic, normalized float vector from text.

    Uses character n-gram and term feature hashing with L2-normalization.
    Guarantees reproducible vectors across all platforms without external ML models,
    while being fully compatible with pgvector column storage and cosine distance.
    """
    if not text or not text.strip():
        return [0.0] * dim

    vec = [0.0] * dim
    terms = _WORD_REGEX.findall(text.lower())
    if not terms:
        terms = [text.strip().lower()]

    for idx, term in enumerate(terms):
        h = int(hashlib.md5(term.encode("utf-8")).hexdigest()[:8], 16)
        slot = h % dim
        sign = 1.0 if ((h >> 8) & 1) else -1.0
        # Position-discounted term frequency weighting
        weight = 1.0 / math.sqrt(idx + 1)
        vec[slot] += sign * weight

        # Substring 2-3 char n-grams for typo and inflection tolerance
        for n in (2, 3):
            if len(term) >= n:
                for i in range(len(term) - n + 1):
                    sub = term[i:i + n]
                    sub_h = int(hashlib.md5(sub.encode("utf-8")).hexdigest()[:8], 16)
                    sub_slot = sub_h % dim
                    sub_sign = 1.0 if ((sub_h >> 8) & 1) else -1.0
                    vec[sub_slot] += sub_sign * 0.3

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-9:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def dumps_embedding(vec: list[float]) -> str:
    """Format vector for database storage (JSON array, compatible with pgvector)."""
    return json.dumps(vec)


def loads_embedding(raw: str | None) -> list[float] | None:
    """Parse vector from database storage."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, list) and all(isinstance(x, (int, float)) for x in data):
            return [float(x) for x in data]
    except Exception:
        pass
    return None


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Calculate cosine similarity between two unit vectors, range [0.0, 1.0]."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    # Map dot product [-1.0, 1.0] to [0.0, 1.0]
    sim = (dot + 1.0) / 2.0
    return max(0.0, min(1.0, sim))


def tokenize_terms(text: str) -> set[str]:
    """Tokenize query and documents for lexical matching."""
    return set(_WORD_REGEX.findall(text.lower()))


def score_lexical(title: str, content: str, query_terms: set[str], version: int = 1) -> int:
    """Compute lexical score based on keyword frequency and version boost."""
    if not query_terms:
        return version
    title_terms = tokenize_terms(title)
    body_terms = tokenize_terms(content)
    title_hits = len(query_terms & title_terms) * 4
    body_hits = len(query_terms & body_terms)
    return title_hits + body_hits + min(version, 5)


def search_hybrid(
    memories: list[Memory],
    query: str,
    allowed_sensitivity: str = "internal",
    limit: int = 20,
    dense_weight: float = 0.6,
) -> list[dict]:
    """Execute hybrid retrieval combining semantic vector similarity and lexical matching."""
    query_clean = query.strip()
    query_terms = tokenize_terms(query_clean) if query_clean else set()
    query_vec = embed_text(query_clean) if query_clean else None

    scored = []
    for m in memories:
        if not sensitivity_allowed(m.sensitivity, allowed_sensitivity):
            continue

        lexical = score_lexical(m.title, m.content, query_terms, m.version)
        if query_terms and lexical <= min(m.version, 5):
            lexical_score = 0.0
        else:
            lexical_score = float(lexical)

        dense_score = 0.0
        if query_vec is not None:
            m_vec = loads_embedding(m.embedding)
            if m_vec is None:
                # Lazy embedding fallback for existing records
                m_vec = embed_text(f"{m.title}\n{m.content}")
            dense_score = cosine_similarity(query_vec, m_vec)

        if not query_clean:
            # Default ranking by recency & version when query is empty
            total_score = float(m.version)
        else:
            # Normalized hybrid combination
            total_score = round(lexical_score * (1.0 - dense_weight) + dense_score * 10.0 * dense_weight, 3)
            if total_score <= 0.1:
                continue

        scored.append((total_score, dense_score, lexical_score, m))

    scored.sort(key=lambda s: (-s[0], -s[3].version, s[3].id))
    results = []
    for total, dense, lex, m in scored[:limit]:
        results.append({
            "id": m.id,
            "kind": m.kind,
            "title": m.title,
            "content": m.content,
            "source": m.source,
            "sensitivity": m.sensitivity,
            "status": m.status,
            "version": m.version,
            "score": total,
            "dense_similarity": round(dense, 3),
            "lexical_score": lex,
        })
    return results
