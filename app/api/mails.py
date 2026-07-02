"""API endpoints — lista maili + szczegóły + akcje na draftach."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Mail, Account, Draft, VerificationLog, Lesson, AgentLog
from ..config import settings
from ..connectors import connector_for
from .. import agent_log
from ..sender_memory import get_sender_history


router = APIRouter(prefix="/api")


def get_user_email(request: Request) -> str:
    """Wyciąga email z Cloudflare Access header."""
    email = request.headers.get(settings.cf_access_user_header)
    if not email:
        # tryb dev (curl bez CF)
        return "anonymous"
    return email.lower()


def require_owner(request: Request):
    email = get_user_email(request)
    if email == "anonymous":
        # dev / direct curl
        return email
    if email not in [e.lower() for e in settings.owners]:
        raise HTTPException(403, f"Nie masz uprawnień ({email})")
    return email


@router.get("/me")
def me(request: Request):
    return {"email": get_user_email(request)}


@router.get("/stats")
def stats(user=Depends(require_owner), db: Session = Depends(get_session)):
    """Statystyki: koszty API, liczba maili per status, oszczędności czasu."""
    from datetime import timedelta
    from ..models import ApiCost
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    def cost_in(start):
        from sqlalchemy import func
        return float(db.scalar(
            select(func.coalesce(func.sum(ApiCost.cost_usd), 0.0)).where(ApiCost.timestamp >= start)
        ) or 0)

    cost_today = cost_in(today_start)
    cost_week = cost_in(week_start)
    cost_month = cost_in(month_start)

    from sqlalchemy import func as sql_func
    by_status = {
        row[0]: row[1]
        for row in db.execute(
            select(Mail.status, sql_func.count(Mail.id)).group_by(Mail.status)
        ).all()
    }

    from ..config import settings
    return {
        "cost_today_usd": round(cost_today, 4),
        "cost_week_usd": round(cost_week, 4),
        "cost_month_usd": round(cost_month, 4),
        "cost_today_pln": round(cost_today * 4, 2),
        "cost_week_pln": round(cost_week * 4, 2),
        "cost_month_pln": round(cost_month * 4, 2),
        "hard_limit_pln": settings.api_cost_hard_limit_pln,
        "percent_of_limit": round((cost_month * 4) / settings.api_cost_hard_limit_pln * 100, 1)
                            if settings.api_cost_hard_limit_pln else 0,
        "mails_by_status": by_status,
        "total_mails": sum(by_status.values()),
    }


@router.get("/accounts")
def list_accounts(user=Depends(require_owner), db: Session = Depends(get_session)):
    from sqlalchemy import func as sql_func
    accs = db.scalars(select(Account).where(Account.active.is_(True))).all()

    # Liczniki maili per konto (status != archived/sent/auto_archived)
    pending_rows = db.execute(
        select(Mail.account_id, sql_func.count(Mail.id))
        .where(Mail.status.notin_(["archived", "sent", "auto_archived", "ignored"]))
        .group_by(Mail.account_id)
    ).all()
    pending_by_acc = {row[0]: row[1] for row in pending_rows}

    total_rows = db.execute(
        select(Mail.account_id, sql_func.count(Mail.id)).group_by(Mail.account_id)
    ).all()
    total_by_acc = {row[0]: row[1] for row in total_rows}

    return [
        {
            "id": a.id,
            "email": a.email,
            "label": a.label,
            "connector_type": a.connector_type,
            "active": a.active,
            "last_fetch_at": a.last_fetch_at.isoformat() + "Z" if a.last_fetch_at else None,
            "last_fetch_error": a.last_fetch_error,
            "pending_count": pending_by_acc.get(a.id, 0),
            "total_count": total_by_acc.get(a.id, 0),
        }
        for a in accs
    ]


@router.get("/mails")
def list_mails(
    user=Depends(require_owner),
    db: Session = Depends(get_session),
    status: str | None = Query(None),
    account_id: int | None = Query(None),
    auto_replied: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Lista maili, najnowsze pierwsze."""
    q = select(Mail).order_by(desc(Mail.received_at), desc(Mail.id))
    if status:
        q = q.where(Mail.status == status)
    if account_id:
        q = q.where(Mail.account_id == account_id)
    if auto_replied:
        # Maile gdzie najnowszy draft został wysłany przez klon-auto*
        q = q.join(Draft, Draft.mail_id == Mail.id).where(
            Draft.sent_by_user.like("klon-auto%")
        ).distinct()
    q = q.limit(limit).offset(offset)
    mails = db.scalars(q).all()
    return [_serialize_mail(m, short=True, db=db) for m in mails]


def _serialize_mail(m: Mail, *, short: bool, db: Session | None = None):
    base = {
        "id": m.id,
        "account_id": m.account_id,
        "from_email": m.from_email,
        "from_name": m.from_name,
        "subject": m.subject or "(bez tematu)",
        "received_at": m.received_at.isoformat() + "Z" if m.received_at else None,
        "status": m.status,
        "category": m.category,
        "urgency": m.urgency,
        "summary": m.summary,
        "is_reply": m.is_reply,
        "has_attachments": m.has_attachments,
        "draft_strategy": m.draft_strategy,
        "injection_risk": m.injection_risk,
        "auto_replied": False,  # uzupełniane niżej jeśli db dostępne
    }
    if db is not None and m.status == "sent":
        last_draft = db.scalar(
            select(Draft).where(Draft.mail_id == m.id).order_by(Draft.version.desc()).limit(1)
        )
        if last_draft and last_draft.sent_by_user and last_draft.sent_by_user.startswith("klon-auto"):
            base["auto_replied"] = True
    if not short:
        base.update({
            "to_emails": json.loads(m.to_emails or "[]"),
            "cc_emails": json.loads(m.cc_emails or "[]"),
            "body_text": m.body_text,
            "body_html": m.body_html,
            "thread_id": m.thread_id,
            "external_id": m.external_id,
            "in_reply_to": m.in_reply_to,
            "suggested_action": m.suggested_action,
            "action_reason": m.action_reason,
            "sentiment": m.sentiment,
            "decision_options": json.loads(m.decision_options) if m.decision_options else [],
            "chosen_decision": m.chosen_decision,
        })
        if db:
            # ostatni draft (current version)
            d = db.scalar(
                select(Draft).where(Draft.mail_id == m.id).order_by(Draft.version.desc()).limit(1)
            )
            if d:
                base["draft"] = {
                    "id": d.id,
                    "version": d.version,
                    "body": d.edited_body or d.body,
                    "original_body": d.body,
                    "was_edited": d.was_edited,
                    "confidence": d.confidence,
                    "facts_used": json.loads(d.facts_used or "[]"),
                    "placeholders": json.loads(d.placeholders or "[]"),
                    "uncertain_claims": json.loads(d.uncertain_claims or "[]"),
                    "needs_human_decision": d.needs_human_decision,
                    "sent_at": d.sent_at.isoformat() + "Z" if d.sent_at else None,
                    "model_used": d.model_used,
                    "verifier": {
                        "flags": json.loads(d.verifier_flags or "[]"),
                        "can_send": d.verifier_can_send,
                        "needs_ack": d.verifier_needs_ack,
                        "run_at": d.verifier_run_at.isoformat() + "Z" if d.verifier_run_at else None,
                    },
                }
    return base


@router.get("/mails/{mail_id}")
def get_mail_full(mail_id: int, user=Depends(require_owner), db: Session = Depends(get_session)):
    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404, "Mail nie istnieje")
    # log view
    db.add(VerificationLog(mail_id=m.id, user_email=user, action="viewed", timestamp=datetime.utcnow()))
    db.commit()
    data = _serialize_mail(m, short=False, db=db)
    # Per-sender memory
    data["sender_history"] = get_sender_history(db, m.from_email, exclude_mail_id=m.id)
    return data


@router.get("/sender/{email}/history")
def sender_history(email: str, user=Depends(require_owner), db: Session = Depends(get_session)):
    return get_sender_history(db, email)


class DecisionBody(BaseModel):
    label: str  # wybrany kierunek (z decision_options lub własny tekst)


@router.post("/mails/{mail_id}/decide")
def decide(mail_id: int, body: DecisionBody, user=Depends(require_owner),
           db: Session = Depends(get_session)):
    """Adam wybrał kierunek odpowiedzi dla maila needs_decision → generuję draft."""
    from ..workers.drafter import generate_draft

    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    m.chosen_decision = body.label
    agent_log.say(
        f"Wybrano kierunek „{body.label}” dla maila od {m.from_email or '?'} — generuję draft",
        event="decision_made", level="action", mail_id=m.id, db=db,
    )
    draft = generate_draft(db, m, decision_label=body.label)
    db.commit()
    if not draft:
        raise HTTPException(500, "Nie udało się wygenerować draftu")
    return _serialize_mail(m, short=False, db=db)


@router.post("/mails/{mail_id}/regenerate")
def regenerate_draft(mail_id: int, user=Depends(require_owner),
                     db: Session = Depends(get_session)):
    """Generuj draft od nowa (kolejna wersja)."""
    from ..workers.drafter import generate_draft

    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    draft = generate_draft(db, m, decision_label=m.chosen_decision)
    db.add(VerificationLog(mail_id=m.id, user_email=user, action="regenerated"))
    db.commit()
    if not draft:
        raise HTTPException(500)
    return _serialize_mail(m, short=False, db=db)


class EditDraftBody(BaseModel):
    body: str


@router.post("/mails/{mail_id}/edit-draft")
def edit_draft(mail_id: int, body: EditDraftBody, user=Depends(require_owner),
               db: Session = Depends(get_session)):
    """Zapis edycji draftu (przed wysłaniem)."""
    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    d = db.scalar(
        select(Draft).where(Draft.mail_id == m.id).order_by(Draft.version.desc()).limit(1)
    )
    if not d:
        raise HTTPException(404, "Brak draftu")
    d.edited_body = body.body
    d.was_edited = True
    db.add(VerificationLog(mail_id=m.id, draft_id=d.id, user_email=user,
                            action="edited", final_body=body.body))
    db.commit()
    return {"ok": True}


@router.post("/mails/{mail_id}/learn-from-edit")
def learn_from_edit(mail_id: int, body: EditDraftBody, user=Depends(require_owner),
                    db: Session = Depends(get_session)):
    """Zapisuje poprawkę i UCZE Klona — generuje regułę stylu z różnicy AI vs human.

    Wynik: Proposal (KLON.md target) który czeka na Twoją akceptację w sekcji "Propozycje".
    """
    from ..llm import chat_json, MODEL_MINI
    from ..models import Lesson, Proposal, ApiCost

    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    d = db.scalar(
        select(Draft).where(Draft.mail_id == m.id).order_by(Draft.version.desc()).limit(1)
    )
    if not d:
        raise HTTPException(404, "Brak draftu")

    ai_version = d.body
    human_version = body.body

    # Zapisz edycję (jak edit-draft)
    d.edited_body = human_version
    d.was_edited = True
    db.add(VerificationLog(
        mail_id=m.id, draft_id=d.id, user_email=user,
        action="edited", final_body=human_version,
        note="learn-from-edit",
    ))

    # Jeśli brak różnicy, nie ucz
    if ai_version.strip() == human_version.strip():
        db.commit()
        return {"ok": True, "learned": False, "reason": "Brak różnicy AI vs Twoja wersja"}

    # Capture Lesson
    lesson = Lesson(draft_id=d.id, ai_version=ai_version, human_version=human_version)
    db.add(lesson)
    db.flush()

    # LLM ekstrahuje regułę z różnicy
    system_prompt = """Jesteś trenerem stylu pisania Adama.

Dostajesz wersję AI i poprawioną wersję Adama. Twoje zadanie: wyodrębnij 1 KONKRETNĄ regułę stylu którą AI powinno zapamiętać.

ZASADY:
- Reguła musi być KONKRETNA i ogólna (nadawana do wielu maili w przyszłości), nie jednorazowa.
- Format: "Zamiast X używaj Y" / "Nie używaj X" / "Zawsze rób Y w sytuacji Z".
- Jeśli różnica jest tylko stylistyczna (przecinek tu, słowo tam) i nie da się wyciągnąć ogólnej reguły, zwróć rule_kind="trivial" i pomiń.

Przykłady dobrych reguł:
- "Zamiast 'Pozdrawiam serdecznie' kończę po prostu imieniem 'Adam'"
- "Nie używaj długich myślników (em-dash). Zamiast nich przecinek lub kropka."
- "Gdy klient pyta o cenę, nie dodawaj propozycji rozmowy w pierwszym mailu (tylko cena + 1 zdanie kontekstu)."

OUTPUT: JSON ze strukturą.
"""

    schema = {
        "name": "style_rule",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rule_kind": {"type": "string", "enum": ["concrete", "trivial"]},
                "title": {"type": "string", "description": "Krótki tytuł reguły (5-8 słów)"},
                "rule": {"type": "string", "description": "Pełna treść reguły (1-2 zdania)"},
                "rationale": {"type": "string", "description": "Krótkie uzasadnienie z konkretnej różnicy"},
            },
            "required": ["rule_kind", "title", "rule", "rationale"],
        },
    }

    try:
        parsed, meta = chat_json(
            model=MODEL_MINI,
            system=system_prompt,
            user=f"Wersja AI:\n{ai_version}\n\n---\n\nWersja Adama:\n{human_version}\n\nWyodrębnij regułę.",
            json_schema=schema,
            temperature=0.2,
            max_tokens=300,
        )
        db.add(ApiCost(
            operation="learn", model=meta["model"],
            tokens_input=meta["tokens_in"], tokens_output=meta["tokens_out"],
            cost_usd=meta["cost_usd"], mail_id=m.id,
        ))
    except Exception as e:
        db.commit()
        return {"ok": True, "learned": False, "reason": f"LLM error: {e}"}

    if parsed.get("rule_kind") == "trivial":
        db.commit()
        return {"ok": True, "learned": False, "reason": "Różnica zbyt drobna, nic do nauki"}

    # Stwórz Proposal (czeka na akceptację Adama w UI)
    prop = Proposal(
        target="klon_md",
        kind="style_rule",
        title=parsed.get("title", "Reguła stylu z poprawki"),
        description=parsed.get("rationale", ""),
        proposed_change=parsed.get("rule", ""),
        evidence=json.dumps({"lesson_id": lesson.id, "mail_id": m.id}, ensure_ascii=False),
        confidence=0.7,
        status="pending",
    )
    db.add(prop)

    # Agent log
    agent_log.say(
        f"📚 Uczę się z poprawki: „{parsed.get('title', '')}” — propozycja czeka na akceptację",
        event="learn", level="action", mail_id=m.id, db=db,
    )

    db.commit()
    return {
        "ok": True, "learned": True,
        "proposal_id": prop.id,
        "title": parsed.get("title"),
        "rule": parsed.get("rule"),
    }


@router.post("/mails/{mail_id}/save-as-gmail-draft")
def save_as_gmail_draft(mail_id: int, user=Depends(require_owner),
                         db: Session = Depends(get_session)):
    """Zapisuje draft jako szkic w skrzynce (Gmail Drafts albo IMAP Drafts)."""
    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    d = db.scalar(
        select(Draft).where(Draft.mail_id == m.id).order_by(Draft.version.desc()).limit(1)
    )
    if not d:
        raise HTTPException(404)
    acc = db.get(Account, m.account_id)
    conn = connector_for(acc)
    body_text = d.edited_body or d.body
    subject = ("Re: " + (m.subject or "")) if not (m.subject or "").startswith("Re:") else (m.subject or "")
    try:
        ext_id = conn.create_draft(to=[m.from_email], subject=subject, body=body_text)
    except Exception as e:
        raise HTTPException(500, f"Błąd tworzenia szkicu: {e}")
    db.add(VerificationLog(mail_id=m.id, draft_id=d.id, user_email=user,
                            action="saved_as_gmail_draft", final_body=body_text))
    db.commit()
    return {"ok": True, "external_id": ext_id}


@router.post("/mails/{mail_id}/send")
def send_mail(mail_id: int, user=Depends(require_owner), db: Session = Depends(get_session)):
    """Wysyła mail przez konto skrzynki."""
    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    d = db.scalar(
        select(Draft).where(Draft.mail_id == m.id).order_by(Draft.version.desc()).limit(1)
    )
    if not d:
        raise HTTPException(404)
    if not d.verifier_can_send:
        raise HTTPException(400, "Verifier zablokował wysyłkę — czerwone flagi")
    acc = db.get(Account, m.account_id)
    conn = connector_for(acc)
    body_text = d.edited_body or d.body
    subject = ("Re: " + (m.subject or "")) if not (m.subject or "").startswith("Re:") else (m.subject or "")
    try:
        ext_id = conn.send(to=[m.from_email], subject=subject, body=body_text)
    except Exception as e:
        raise HTTPException(500, f"Błąd wysyłki: {e}")
    d.sent_at = datetime.utcnow()
    d.sent_by_user = user
    d.external_sent_id = ext_id
    m.status = "sent"
    db.add(VerificationLog(mail_id=m.id, draft_id=d.id, user_email=user,
                            action="sent", final_body=body_text))
    agent_log.say(
        f"Wysłano odpowiedź do {m.from_email or '?'} (subject: {(subject or '')[:60]})",
        event="sent", level="success", mail_id=m.id, db=db,
    )

    # Capture lesson — jeśli human znacząco zmienił AI draft, to lekcja stylu
    if d.body and body_text and d.body.strip() != body_text.strip():
        lesson = Lesson(
            draft_id=d.id,
            ai_version=d.body,
            human_version=body_text,
        )
        db.add(lesson)

    db.commit()
    return {"ok": True, "external_id": ext_id, "lesson_captured": d.body.strip() != body_text.strip()}


@router.post("/mails/{mail_id}/archive")
def archive_mail(mail_id: int, user=Depends(require_owner), db: Session = Depends(get_session)):
    m = db.get(Mail, mail_id)
    if not m:
        raise HTTPException(404)
    m.status = "archived"
    m.archived_at = datetime.utcnow()
    db.add(VerificationLog(mail_id=m.id, user_email=user, action="archived"))
    agent_log.say(
        f"Zarchiwizowano mail od {m.from_email or '?'}",
        event="archived", level="info", mail_id=m.id, db=db,
    )
    db.commit()
    return {"ok": True}


# ============ AGENT LOG / STATUS ============


class ChatBody(BaseModel):
    messages: list[dict]   # [{role: "user"|"assistant", content: str}]


@router.post("/agent/chat")
def agent_chat(body: ChatBody, user=Depends(require_owner)):
    """Chat z Klonem (LLM + function calling)."""
    from ..agent_chat import chat_turn
    if not body.messages:
        raise HTTPException(400, "Pusta historia")
    # Walidacja: max 30 wiadomości w sesji (bezpieczeństwo kosztów)
    if len(body.messages) > 30:
        raise HTTPException(400, "Za długa historia (max 30 wiadomości w sesji). Resetuj.")
    return chat_turn(body.messages)


@router.get("/agent/log")
def get_agent_log(
    user=Depends(require_owner),
    db: Session = Depends(get_session),
    since_id: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Wpisy agent_log nowsze niż since_id (live tail)."""
    q = select(AgentLog).order_by(desc(AgentLog.id)).limit(limit)
    if since_id:
        q = select(AgentLog).where(AgentLog.id > since_id).order_by(AgentLog.id).limit(limit)
    rows = db.scalars(q).all()
    if not since_id:
        rows = list(reversed(rows))  # najstarsze pierwsze (chronologia w UI)
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() + "Z" if r.timestamp else None,
            "actor": r.actor,
            "level": r.level,
            "event": r.event,
            "message": r.message,
            "mail_id": r.mail_id,
            "account_id": r.account_id,
        }
        for r in rows
    ]


@router.get("/agent/status")
def get_agent_status(user=Depends(require_owner), db: Session = Depends(get_session)):
    """Status agenta: ostatnia akcja, pending decyzje/drafty, koszt dziś, najpilniejszy."""
    from sqlalchemy import func as sql_func
    from datetime import timedelta
    last = db.scalar(select(AgentLog).order_by(desc(AgentLog.id)).limit(1))

    pending_decisions = db.scalar(
        select(sql_func.count(Mail.id))
        .where(Mail.draft_strategy == "needs_decision",
               Mail.status.in_(["analyzed", "drafted"]),
               Mail.chosen_decision.is_(None))
    ) or 0
    pending_drafts = db.scalar(
        select(sql_func.count(Mail.id)).where(Mail.status == "drafted")
    ) or 0
    pending_analyze = db.scalar(
        select(sql_func.count(Mail.id)).where(Mail.status == "new")
    ) or 0

    # Auto-archived dziś
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    auto_archived_today = db.scalar(
        select(sql_func.count(Mail.id))
        .where(Mail.status == "auto_archived", Mail.archived_at >= today)
    ) or 0

    # Auto-replied (Klon sam wysłał) — wszystkie + dziś
    auto_replied_total = db.scalar(
        select(sql_func.count(Draft.id.distinct()))
        .where(Draft.sent_by_user.like("klon-auto%"))
    ) or 0
    auto_replied_today = db.scalar(
        select(sql_func.count(Draft.id.distinct()))
        .where(Draft.sent_by_user.like("klon-auto%"), Draft.sent_at >= today)
    ) or 0

    # Najpilniejszy mail (najwyższa pilność, najstarszy z wymagających odpowiedzi)
    urgent = db.scalar(
        select(Mail)
        .where(Mail.status.in_(["analyzed", "drafted"]),
               Mail.suggested_action.in_(["odpowiedz", "eskaluj_adam", "eskaluj_martyna"]))
        .order_by(Mail.urgency.desc().nullslast(), Mail.received_at.asc())
        .limit(1)
    )
    urgent_info = None
    if urgent:
        urgent_info = {
            "id": urgent.id,
            "from": urgent.from_name or urgent.from_email,
            "subject": urgent.subject,
            "category": urgent.category,
            "urgency": urgent.urgency,
            "received_at": urgent.received_at.isoformat() + "Z" if urgent.received_at else None,
        }

    return {
        "last_action": {
            "message": last.message if last else "Klon czeka na nowe maile",
            "level": last.level if last else "info",
            "event": last.event if last else "idle",
            "timestamp": last.timestamp.isoformat() + "Z" if last and last.timestamp else None,
        },
        "pending": {
            "decisions": pending_decisions,
            "drafts_to_review": pending_drafts,
            "to_analyze": pending_analyze,
            "auto_archived_today": auto_archived_today,
            "auto_replied_today": auto_replied_today,
            "auto_replied_total": auto_replied_total,
        },
        "urgent": urgent_info,
    }
