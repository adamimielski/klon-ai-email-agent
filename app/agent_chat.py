"""Chat z Klonem — pole input + LLM z function calling.

4 tools dostępne:
- add_style_rule(rule, why) → tworzy Proposal type=klon_md, kind=style_rule
- add_playbook_rule(category, instruction) → Proposal type=klon_md, kind=playbook
- add_sender_exception(email, action, why) → Proposal kind=sender_rule (akceptacja w UI)
- search_mails(query) → top 10 maili pasujących do query (po from/subject/body/category)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, or_, desc, func

from .config import settings
from .db import SessionLocal
from .models import Mail, Proposal, AgentLog, Account
from .llm import MODEL_MINI, cost_usd, _client, _check_budget
from . import agent_log


# ====================================================================
# TOOLS DEFINITIONS (OpenAI function calling format)
# ====================================================================
TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "add_style_rule",
            "description": (
                "Dodaj regułę stylu pisania do KLON.md (jak Klon ma pisać). "
                "Używaj gdy Adam mówi: 'pisz krócej', 'nie używaj X', 'zawsze używaj Y'. "
                "Reguła trafia jako Proposal i wymaga akceptacji w UI."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "description": "Krótki tytuł reguły (5-8 słów)"},
                    "rule": {"type": "string", "description": "Treść reguły (1-3 zdania, konkretnie)"},
                    "why": {"type": "string", "description": "Dlaczego Adam tego chce"},
                },
                "required": ["title", "rule", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_playbook_rule",
            "description": (
                "Dodaj regułę do playbooka konkretnej kategorii (jak Klon ma odpowiadać na typy maili). "
                "Używaj gdy Adam mówi: 'dla reklamacji zawsze pytaj o numer zamówienia', "
                "'przy lead_cena nie proponuj rozmowy w pierwszym mailu'. "
                "Trafia jako Proposal."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "lead_cena", "lead_demo", "lead_pytanie",
                            "klient_pytanie", "klient_reklamacja", "klient_potwierdzenie",
                            "klient_support", "platnosc_problem", "partnerstwo",
                        ],
                        "description": "Kategoria maila której dotyczy reguła",
                    },
                    "title": {"type": "string", "description": "Krótki tytuł"},
                    "rule": {"type": "string", "description": "Treść reguły (must_include / must_avoid / instruction)"},
                    "why": {"type": "string", "description": "Powód"},
                },
                "required": ["category", "title", "rule", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_sender_exception",
            "description": (
                "Dodaj wyjątek dla konkretnego nadawcy (np. wyłącz auto-reply dla jan@firma.pl, "
                "albo zawsze eskaluj maile od ważnego klienta). Trafia jako Proposal."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "email": {"type": "string", "description": "Email nadawcy"},
                    "action": {
                        "type": "string",
                        "enum": ["disable_auto_reply", "always_escalate", "always_priority"],
                        "description": "Co zrobić z mailami od tego nadawcy",
                    },
                    "why": {"type": "string", "description": "Powód wyjątku"},
                },
                "required": ["email", "action", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_mails",
            "description": (
                "Wyszukaj maile w skrzynkach. Działa po: from_email, from_name, subject, body, category, status. "
                "Używaj gdy user pyta: 'pokaż maile od Marka', 'co przyszło dziś', "
                "'wszystkie reklamacje', 'maile o cenie z tego tygodnia'."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string", "description": "Co szukać (imię, email, fragment subject/body, lub kategoria)"},
                    "category": {"type": "string", "description": "Opcjonalnie: filtr po kategorii (lead_cena, klient_reklamacja, ...). Pusty string = wszystkie.", "default": ""},
                    "days_back": {"type": "integer", "description": "Ile dni wstecz (max 30). 0 = bez limitu", "default": 0},
                    "limit": {"type": "integer", "description": "Max wyników (max 20)", "default": 10},
                },
                "required": ["query", "category", "days_back", "limit"],
            },
        },
    },
]


# ====================================================================
# TOOL IMPLEMENTATIONS
# ====================================================================

def tool_add_style_rule(db, args: dict) -> dict:
    p = Proposal(
        target="klon_md",
        kind="style_rule",
        title=args["title"][:255],
        description=args["why"],
        proposed_change=args["rule"],
        evidence=json.dumps({"source": "chat_with_klon"}, ensure_ascii=False),
        confidence=0.6,
        status="pending",
    )
    db.add(p)
    db.flush()
    return {
        "ok": True,
        "proposal_id": p.id,
        "message": f"Zapisałem propozycję reguły stylu (#{p.id}). Czeka na akceptację w sekcji Propozycje.",
    }


def tool_add_playbook_rule(db, args: dict) -> dict:
    p = Proposal(
        target="klon_md",  # playbooki są częścią KLON.md w UX (target=klon_md = auto-zapis po akceptacji)
        kind="playbook",
        title=f"[{args['category']}] {args['title']}"[:255],
        description=args["why"],
        proposed_change=f"Kategoria: {args['category']}\n\n{args['rule']}",
        evidence=json.dumps({"category": args["category"], "source": "chat_with_klon"}, ensure_ascii=False),
        confidence=0.6,
        status="pending",
    )
    db.add(p)
    db.flush()
    return {
        "ok": True,
        "proposal_id": p.id,
        "message": f"Zapisałem propozycję reguły dla kategorii '{args['category']}' (#{p.id}). Czeka na akceptację.",
    }


def tool_add_sender_exception(db, args: dict) -> dict:
    p = Proposal(
        target="firma_yaml",
        kind="sender_rule",
        title=f"Wyjątek dla {args['email']}: {args['action']}",
        description=args["why"],
        proposed_change=f"sender_rules:\n  - email: {args['email']}\n    action: {args['action']}\n    why: {args['why']}",
        evidence=json.dumps({"email": args["email"], "action": args["action"]}, ensure_ascii=False),
        confidence=0.7,
        status="pending",
    )
    db.add(p)
    db.flush()
    return {
        "ok": True,
        "proposal_id": p.id,
        "message": f"Zapisałem wyjątek dla {args['email']} (#{p.id}). UWAGA: dla firma.yaml musisz potem ręcznie dopisać do pliku.",
    }


def tool_search_mails(db, args: dict) -> dict:
    q_text = (args.get("query") or "").strip()
    category = (args.get("category") or "").strip()
    days_back = max(0, min(30, args.get("days_back") or 0))
    limit = max(1, min(20, args.get("limit") or 10))

    q = select(Mail).order_by(desc(Mail.received_at))

    if q_text:
        like = f"%{q_text}%"
        q = q.where(or_(
            Mail.from_email.ilike(like),
            Mail.from_name.ilike(like),
            Mail.subject.ilike(like),
            Mail.body_text.ilike(like),
            Mail.summary.ilike(like),
        ))
    if category:
        q = q.where(Mail.category == category)
    if days_back:
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        q = q.where(Mail.received_at >= cutoff)

    q = q.limit(limit)
    mails = db.scalars(q).all()

    return {
        "ok": True,
        "count": len(mails),
        "mails": [
            {
                "id": m.id,
                "from": m.from_name or m.from_email,
                "subject": m.subject,
                "category": m.category,
                "status": m.status,
                "received_at": m.received_at.isoformat() + "Z" if m.received_at else None,
                "summary": m.summary,
            }
            for m in mails
        ],
    }


TOOL_IMPL = {
    "add_style_rule": tool_add_style_rule,
    "add_playbook_rule": tool_add_playbook_rule,
    "add_sender_exception": tool_add_sender_exception,
    "search_mails": tool_search_mails,
}


# ====================================================================
# SYSTEM PROMPT
# ====================================================================

SYSTEM_PROMPT = """Jesteś Klonem — agentem mailowym Adama. Rozmawiasz z Adamem (operatorem) w panelu.

Adam może Cię prosić o:
1. Dodanie reguły stylu pisania (np. "pisz krócej", "nie używaj X") → użyj tool add_style_rule
2. Dodanie reguły dla konkretnej kategorii maili (np. "przy reklamacji zawsze pytaj o numer zamówienia") → use add_playbook_rule
3. Wyjątek dla konkretnego nadawcy (np. "dla jan@firma.pl nie odpisuj automatycznie") → use add_sender_exception
4. Wyszukanie maili (np. "pokaż maile od Marka", "co przyszło dziś", "wszystkie reklamacje") → use search_mails
5. Pytanie o stan ("co się działo", "ile maili obsłużyłeś") → odpowiedz krótko bazując na kontekście który dostałeś

ZASADY:
- Mów naturalnie, krótko, bezpośrednio. Po polsku, "ty/ciebie" do Adama.
- NIE wymyślaj danych. Jeśli nie wiesz, powiedz "nie wiem, sprawdź X".
- Po wykonaniu narzędzia (tool) potwierdź krótko co zrobiłeś. Np. "Zapisałem regułę. Sprawdź sekcję Propozycje."
- NIE próbuj edytować plików sam. Wszystkie zmiany idą jako Proposal — Adam akceptuje w UI.
- Nie używaj długich myślników (em-dash, en-dash). Używaj kropki, przecinka, dwukropka.
- Nie używaj emoji w odpowiedziach (poza wyjątkami gdy Adam pyta o coś radosnego).
- Jeśli pytanie Adama jest niejasne, dopytaj zanim wywołasz narzędzie.

Pamiętaj: jesteś OPERATORSKIM agentem, nie chatbotem do gadania. Trzymaj się tematu maili i pracy."""


# ====================================================================
# MAIN CHAT LOOP
# ====================================================================

def _build_context_summary(db) -> str:
    """Zwraca krótki kontekst stanu (co Klon wie o swojej skrzynce)."""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    by_status = dict(db.execute(
        select(Mail.status, func.count(Mail.id)).group_by(Mail.status)
    ).all())
    today_received = db.scalar(
        select(func.count(Mail.id)).where(Mail.received_at >= today)
    ) or 0
    last_logs = db.scalars(
        select(AgentLog).order_by(desc(AgentLog.id)).limit(8)
    ).all()
    log_lines = [f"  - [{l.event}] {l.message}" for l in reversed(last_logs)]
    return (
        f"Stan skrzynek (kontekst):\n"
        f"  - Maile dziś: {today_received}\n"
        f"  - Status: {by_status}\n"
        f"Ostatnie 8 zdarzeń:\n" + "\n".join(log_lines)
    )


def chat_turn(messages: list[dict], max_rounds: int = 3) -> dict:
    """Wykonuje turę rozmowy z Klonem.

    messages = [{role: "user"|"assistant"|"tool", content: str, ...}]
    Zwraca {"reply": str, "actions": [{tool, args, result}], "cost_usd": float}
    """
    if not settings.openai_api_key:
        return {"reply": "Brak klucza OpenAI (OPENAI_API_KEY).", "actions": [], "cost_usd": 0}

    _check_budget()

    with SessionLocal() as db:
        context = _build_context_summary(db)
        full_messages = [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context}
        ] + messages

        actions = []
        total_cost = 0.0
        final_reply = ""

        for round_idx in range(max_rounds):
            body = {
                "model": MODEL_MINI,
                "messages": full_messages,
                "tools": TOOLS_SPEC,
                "tool_choice": "auto",
                "temperature": 0.3,
                "max_tokens": 800,
            }
            try:
                with _client() as c:
                    resp = c.post("/chat/completions", json=body)
                    resp.raise_for_status()
                    data = resp.json()
            except httpx.HTTPError as e:
                return {"reply": f"Błąd LLM: {e}", "actions": actions, "cost_usd": total_cost}

            usage = data.get("usage", {})
            total_cost += cost_usd(MODEL_MINI, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))

            msg = data["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final_reply = msg.get("content") or ""
                break

            # Dodaj assistant message z tool_calls
            full_messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            # Wykonaj każdy tool_call
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                impl = TOOL_IMPL.get(fn_name)
                if not impl:
                    result = {"ok": False, "error": f"Nieznane narzędzie: {fn_name}"}
                else:
                    try:
                        result = impl(db, args)
                    except Exception as e:
                        result = {"ok": False, "error": str(e)}

                actions.append({"tool": fn_name, "args": args, "result": result})
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                })

        # Commit wszystkich zmian (proposals)
        db.commit()

        # Log
        if actions:
            agent_log.say(
                f"💬 Adam pisał do mnie. Wykonałem: {', '.join(a['tool'] for a in actions)}",
                event="chat", level="action",
            )

        return {
            "reply": final_reply or "(brak odpowiedzi)",
            "actions": actions,
            "cost_usd": round(total_cost, 5),
        }
