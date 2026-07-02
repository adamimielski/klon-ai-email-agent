"""Wrapper na OpenAI Chat Completions API.

Wspiera structured JSON output (response_format z JSON schema) — wymusza
poprawną strukturę, brak halucynacji formatu.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import settings


# Modele dostępne — dobieramy do zadania
MODEL_MINI = "gpt-4o-mini"     # analyzer, verifier, injection detector
MODEL_PRO = "gpt-4o"           # drafter (jakość)


# Cennik (USD per 1M tokens, stan 2026-05)
PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICING.get(model, {"input": 1.0, "output": 5.0})
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1_000_000


def _client() -> httpx.Client:
    if not settings.openai_api_key:
        raise RuntimeError("Brak OPENAI_API_KEY w .env")
    return httpx.Client(
        base_url="https://api.openai.com/v1",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        timeout=60.0,
    )


def _check_budget():
    """Hard cap — jeśli przekroczono settings.api_cost_hard_limit_pln, blokujemy."""
    from datetime import datetime
    from sqlalchemy import select, func
    try:
        from .db import SessionLocal
        from .models import ApiCost
    except Exception:
        return  # podczas init
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        spent_usd = db.scalar(
            select(func.coalesce(func.sum(ApiCost.cost_usd), 0.0))
            .where(ApiCost.timestamp >= month_start)
        ) or 0
    spent_pln = float(spent_usd) * 4
    limit = settings.api_cost_hard_limit_pln
    if limit and spent_pln >= limit:
        raise RuntimeError(
            f"HARD LIMIT: wydano {spent_pln:.2f} PLN w tym miesiącu, limit {limit} PLN. "
            f"Zwiększ API_COST_HARD_LIMIT_PLN w .env albo poczekaj do nowego miesiąca."
        )


def chat_json(
    *,
    model: str,
    system: str,
    user: str,
    json_schema: dict | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    retries: int = 2,
) -> tuple[dict, dict]:
    """Wywołuje Chat Completions, wymusza JSON output.

    Zwraca (parsed_json, meta) gdzie meta zawiera tokens_in, tokens_out,
    cost_usd, model.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_schema:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": json_schema.get("name", "response"),
                "strict": True,
                "schema": json_schema["schema"],
            },
        }
    else:
        body["response_format"] = {"type": "json_object"}

    _check_budget()
    last_err = None
    for attempt in range(retries + 1):
        try:
            with _client() as c:
                resp = c.post("/chat/completions", json=body)
                if resp.status_code == 429 and attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            usage = data.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            meta = {
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd(model, tokens_in, tokens_out),
            }
            return parsed, meta
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM chat_json failed after {retries+1} attempts: {last_err}")


def chat_plain(*, model: str, system: str, user: str,
               temperature: float = 0.3, max_tokens: int = 1500) -> tuple[str, dict]:
    """Wywołuje LLM bez wymuszania JSON. Dla draftów (tekst + JSON metadanych mieszanych)."""
    with _client() as c:
        resp = c.post("/chat/completions", json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        resp.raise_for_status()
        data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)
    meta = {
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd(model, tokens_in, tokens_out),
    }
    return text, meta
