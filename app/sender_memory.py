"""Pamięć agenta o nadawcach — historia kontaktów per email."""
from datetime import datetime
from typing import Any

from sqlalchemy import select, func, desc

from .models import Mail, Draft, SentExample


def get_sender_history(db, email: str | None, *, exclude_mail_id: int | None = None) -> dict[str, Any]:
    """Zbiera kontekst o nadawcy — co już o nim wiemy.

    Zwraca słownik z polami:
    - total_received: ile maili od niego dostaliśmy
    - first_seen: kiedy pierwszy mail
    - last_seen: kiedy ostatni mail
    - categories: lista kategorii poprzednich maili (z licznikiem)
    - last_categories: 3 ostatnie kategorie chronologicznie
    - sent_to_him: ile maili Adam wysłał do niego (z sent_examples)
    - last_sent_at: kiedy Adam ostatnio do niego pisał
    - last_sent_subject: temat ostatniej odpowiedzi Adama
    - status_summary: krótkie podsumowanie ("nowy lead" / "kupił" / "long-time klient" / itd.)
    """
    if not email:
        return {"total_received": 0, "status_summary": "Nieznany nadawca."}

    email_lower = email.lower().strip()

    # Wszystkie maile od tego nadawcy (oprócz aktualnego)
    q = select(Mail).where(func.lower(Mail.from_email) == email_lower)
    if exclude_mail_id:
        q = q.where(Mail.id != exclude_mail_id)
    mails = db.scalars(q.order_by(Mail.received_at.desc())).all()

    total = len(mails)
    if total == 0 and not exclude_mail_id:
        return {"total_received": 0, "status_summary": "To pierwszy mail od tego nadawcy."}

    first_seen = mails[-1].received_at if mails else None
    last_seen = mails[0].received_at if mails else None

    # Kategorie
    cat_counts: dict[str, int] = {}
    last_categories: list[str] = []
    for m in mails:
        if m.category:
            cat_counts[m.category] = cat_counts.get(m.category, 0) + 1
            if len(last_categories) < 3 and m.category not in last_categories:
                last_categories.append(m.category)

    # Czy Adam mu odpisywał (sent_examples — wysyłki Adama)
    sent_to_him = db.scalars(
        select(SentExample)
        .where(func.lower(SentExample.to_email) == email_lower)
        .order_by(desc(SentExample.sent_at))
    ).all()
    last_sent = sent_to_him[0] if sent_to_him else None

    # Czy mamy też wysyłki przez nasz system (sent draft)
    sent_via_agent = db.scalars(
        select(Draft).join(Mail, Draft.mail_id == Mail.id)
        .where(
            func.lower(Mail.from_email) == email_lower,
            Draft.sent_at.isnot(None),
        )
        .order_by(desc(Draft.sent_at))
    ).all()

    # Status summary
    summary_parts = []
    has_lead = any(c.startswith("lead_") for c in cat_counts)
    has_klient = any(c.startswith("klient_") for c in cat_counts)
    if has_klient:
        summary_parts.append("istniejący klient")
    elif has_lead:
        summary_parts.append("lead")
    if total >= 5:
        summary_parts.append(f"{total} maili w historii")
    elif total > 0:
        summary_parts.append(f"{total} mail{'i' if total > 1 else ''} wcześniej")

    if last_sent:
        days_ago = (datetime.utcnow() - last_sent.sent_at).days if last_sent.sent_at else None
        if days_ago is not None:
            summary_parts.append(f"Adam pisał do niego {days_ago} dni temu" if days_ago > 0 else "Adam pisał dzisiaj")
    elif sent_via_agent:
        last_via = sent_via_agent[0].sent_at
        days_ago = (datetime.utcnow() - last_via).days if last_via else None
        if days_ago is not None:
            summary_parts.append(f"agent odpisywał {days_ago} dni temu")
    elif total > 0:
        summary_parts.append("brak odpowiedzi od nas")

    status_summary = ". ".join(summary_parts).capitalize() + "." if summary_parts else "Brak historii."

    return {
        "total_received": total,
        "first_seen": first_seen.isoformat() if first_seen else None,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "categories": cat_counts,
        "last_categories": last_categories,
        "sent_to_him": len(sent_to_him),
        "sent_via_agent": len(sent_via_agent),
        "last_sent_at": last_sent.sent_at.isoformat() if last_sent and last_sent.sent_at else None,
        "last_sent_subject": last_sent.subject if last_sent else None,
        "last_sent_excerpt": (last_sent.body_text or "")[:300] if last_sent else None,
        "status_summary": status_summary,
    }


def history_for_prompt(history: dict) -> str:
    """Sformatuj historię dla wstrzyknięcia do prompta drafera."""
    if history.get("total_received", 0) == 0:
        return "To pierwszy mail od tego nadawcy. Nie mamy o nim wcześniejszych informacji."

    lines = [history["status_summary"]]
    if history.get("last_categories"):
        lines.append(f"Ostatnie kategorie maili od niego: {', '.join(history['last_categories'])}")
    if history.get("last_sent_at"):
        subj = history.get("last_sent_subject") or ""
        lines.append(f"Adam ostatnio do niego pisał: {history['last_sent_at'][:10]}, temat: „{subj[:80]}”")
        if history.get("last_sent_excerpt"):
            lines.append(f"Fragment ostatniej odpowiedzi (jako referencja stylu/treści):\n{history['last_sent_excerpt']}")
    return "\n".join(lines)
