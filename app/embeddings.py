"""Embeddingi tekstów (OpenAI text-embedding-3-small).

Wektor zapisywany jako numpy float32 -> bytes w SQLite (LargeBinary).
Wyszukiwanie cosine similarity w Pythonie (numpy) — dla <10k wektorów wystarczy.
"""
from __future__ import annotations

import numpy as np
import httpx
from typing import Iterable

from .config import settings


EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536  # wymiar wektora dla text-embedding-3-small


def _client() -> httpx.Client:
    if not settings.openai_api_key:
        raise RuntimeError("Brak OPENAI_API_KEY w .env")
    return httpx.Client(
        base_url="https://api.openai.com/v1",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        timeout=30.0,
    )


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    """Generuje embeddingi dla listy tekstów. Zwraca listę wektorów np.float32."""
    if not texts:
        return []
    # OpenAI ma limit 8191 tokenów per input; tnijmy długie maile
    cleaned = [t[:8000] if t else "(pusty)" for t in texts]
    with _client() as c:
        resp = c.post("/embeddings", json={
            "model": EMBED_MODEL,
            "input": cleaned,
        })
        resp.raise_for_status()
        data = resp.json()
    return [np.array(item["embedding"], dtype=np.float32) for item in data["data"]]


def embed_one(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def vec_to_bytes(v: np.ndarray) -> bytes:
    return v.astype(np.float32).tobytes()


def bytes_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity. Zakłada wektory tej samej długości."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def find_top_k(query_vec: np.ndarray,
               candidates: Iterable[tuple[int, np.ndarray]],
               k: int = 3) -> list[tuple[int, float]]:
    """Zwraca top-k najbardziej podobnych: [(id, similarity), ...]"""
    scored = [(cid, cosine_similarity(query_vec, vec)) for cid, vec in candidates]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def cost_for_tokens(token_count: int) -> float:
    """Koszt USD dla text-embedding-3-small. $0.02 / 1M tokenów."""
    return token_count * 0.02 / 1_000_000
