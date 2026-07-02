"""Self-tuning worker — generuje propozycje aktualizacji.

Pętla B: lekcje (AI vs Human) → propozycje reguł stylu do KLON.md
Pętla C: wzorce maili → luki w firma.yaml

Wszystkie propozycje wymagają RĘCZNEGO zatwierdzenia w dashboardzie.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func

from ..db import SessionLocal
from ..models import Lesson, Mail, Proposal, ApiCost
from ..llm import chat_json, MODEL_MINI


log = logging.getLogger("proposer")


# =====================================================================
# Pętla B — propozycje stylu (KLON.md) z lekcji
# =====================================================================

STYLE_PROPOSER_SYSTEM = """Jesteś trenerem stylu pisania.

Dostajesz listę par (wersja_AI, wersja_człowieka) — czyli to jak AI napisała mail i jak Adam ją poprawił przed wysłaniem.

Twoje zadanie: znajdź WZORCE w poprawkach Adama. Sformułuj 1-3 KONKRETNE reguły stylu które poprawiłyby przyszłe drafty.

PRZYKŁADY DOBRYCH REGUŁ:
- "Nie używaj 'serdecznie pozdrawiam' — Adam zawsze kończy '—Adam'"
- "Skróć powitanie — 'Cześć [imię]' zamiast 'Dzień dobry, Panie [imię]'"
- "Nie używaj 'oferta promocyjna' — Adam mówi 'cena dla Ciebie'"
- "Pisz krótsze zdania, max 12-15 słów"

ZASADY:
- Tylko POWTARZALNE wzorce (3+ przykładów w lekcjach)
- Konkretne, krótkie, działania (nie ogólniki typu "pisz lepiej")
- Reguły muszą być ZASTOSOWALNE w prompcie — coś AI może zrobić deterministycznie

NIE proponuj reguł jeśli wzorzec nie jest oczywisty. Lepiej "(brak propozycji)" niż zła reguła.

OUTPUT: JSON z listą propozycji."""


STYLE_PROPOSER_SCHEMA = {
    "name": "style_proposals",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "description": "Krótki tytuł reguły (5-10 słów)"},
                        "rule": {"type": "string", "description": "Konkretna reguła do dodania do KLON.md"},
                        "reasoning": {"type": "string", "description": "Co Adam zmienia w jakich sytuacjach"},
                        "evidence_indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "Indeksy lekcji (0-based) na które się powołujesz",
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["title", "rule", "reasoning", "evidence_indices", "confidence"],
                },
            },
        },
        "required": ["proposals"],
    },
}


def propose_style_rules(min_lessons: int = 5, lookback_days: int = 14) -> int:
    """Analizuje ostatnie lekcje i tworzy propozycje stylu."""
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    with SessionLocal() as db:
        lessons = db.scalars(
            select(Lesson)
            .where(Lesson.created_at > cutoff, Lesson.incorporated_to_klon.is_(False))
            .order_by(Lesson.created_at.desc())
            .limit(30)
        ).all()
        if len(lessons) < min_lessons:
            log.info("Za mało lekcji (%d < %d), pomijam style proposer", len(lessons), min_lessons)
            return 0

        # Budowa prompta
        lesson_blocks = []
        for i, l in enumerate(lessons):
            lesson_blocks.append(
                f"### Lekcja {i}\n"
                f"AI napisała:\n{l.ai_version[:1500]}\n\n"
                f"Adam poprawił na:\n{l.human_version[:1500]}\n"
            )
        user_prompt = "\n\n---\n\n".join(lesson_blocks)
        user_prompt += "\n\n---\nProszę o 1-3 reguły stylu na podstawie wzorców w poprawkach Adama."

        try:
            parsed, meta = chat_json(
                model=MODEL_MINI,
                system=STYLE_PROPOSER_SYSTEM,
                user=user_prompt,
                json_schema=STYLE_PROPOSER_SCHEMA,
                temperature=0.3,
                max_tokens=1500,
            )
        except Exception as e:
            log.exception("Style proposer error: %s", e)
            return 0

        n_created = 0
        for p in parsed.get("proposals", []):
            if p.get("confidence", 0) < 0.5:
                continue
            existing = db.scalar(
                select(Proposal).where(
                    Proposal.target == "klon_md",
                    Proposal.title == p["title"],
                    Proposal.status == "pending",
                )
            )
            if existing:
                continue
            prop = Proposal(
                target="klon_md",
                kind="style_rule",
                title=p["title"],
                description=p["reasoning"],
                evidence=json.dumps({
                    "rule": p["rule"],
                    "lesson_ids": [lessons[i].id for i in p.get("evidence_indices", []) if i < len(lessons)],
                }, ensure_ascii=False),
                proposed_change=p["rule"],
                confidence=p.get("confidence"),
            )
            db.add(prop)
            n_created += 1

        db.add(ApiCost(
            operation="propose_style", model=meta["model"],
            tokens_input=meta["tokens_in"], tokens_output=meta["tokens_out"],
            cost_usd=meta["cost_usd"],
        ))
        db.commit()
        log.info("Style proposer: %d propozycji z %d lekcji", n_created, len(lessons))
        return n_created


# =====================================================================
# Pętla C — luki w firma.yaml na podstawie maili
# =====================================================================

FACTS_GAP_SYSTEM = """Jesteś audytorem bazy wiedzy firmy.

Dostajesz listę maili od leadów/klientów oraz aktualny firma.yaml.

Twoje zadanie: znajdź LUKI w firma.yaml — informacje o których klienci pytają, ale których brakuje w pliku.

PRZYKŁADY LUK:
- 5 leadów pytało o "godziny wsparcia technicznego" — brak w firma.yaml
- 3 klientów pytało o "cenę dla agencji marketingowych" — brak takiego pakietu w firma.yaml
- 2 razy ktoś pytał o "płatność ratalną" — brak info o płatnościach w firma.yaml

ZASADY:
- Tylko POWTARZALNE wzorce (2+ pytań)
- Konkretne, działające sugestie (jaką sekcję dodać, z jakim contentem)
- NIE proponuj wartości — proponuj że Adam powinien je określić

OUTPUT: JSON z listą luk."""


FACTS_GAP_SCHEMA = {
    "name": "facts_gaps",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "gaps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "section_suggestion": {"type": "string", "description": "Gdzie w firma.yaml dodać (path albo nazwa sekcji)"},
                        "what_to_add": {"type": "string", "description": "Co Adam powinien sprecyzować"},
                        "evidence": {"type": "string", "description": "Cytat z maila/maili które pytały o to"},
                        "frequency": {"type": "integer", "description": "Ile razy się powtarzało"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["title", "section_suggestion", "what_to_add", "evidence", "frequency", "confidence"],
                },
            },
        },
        "required": ["gaps"],
    },
}


def propose_facts_gaps(lookback_days: int = 14) -> int:
    from pathlib import Path
    from ..config import BASE_DIR

    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    with SessionLocal() as db:
        mails = db.scalars(
            select(Mail)
            .where(Mail.category.in_([
                       "lead_cena", "lead_demo", "lead_pytanie",
                       "klient_pytanie", "klient_reklamacja", "klient_potwierdzenie", "klient_support",
                   ]),
                   Mail.received_at > cutoff)
            .order_by(Mail.received_at.desc())
            .limit(40)
        ).all()
        if len(mails) < 5:
            log.info("Za mało maili (%d), pomijam facts proposer", len(mails))
            return 0

        # Ładuj firma.yaml
        facts_path = BASE_DIR / "data" / "facts" / "firma.yaml"
        facts_text = facts_path.read_text(encoding="utf-8") if facts_path.exists() else "(brak)"

        # Skondensowany kontekst maili
        mail_blocks = []
        for m in mails:
            summary = m.summary or (m.body_text or "")[:200]
            mail_blocks.append(f"- [{m.category}] od {m.from_email}: {summary}")
        user_prompt = f"""<aktualny_firma_yaml>
{facts_text[:5000]}
</aktualny_firma_yaml>

<ostatnie_maile>
{chr(10).join(mail_blocks)}
</ostatnie_maile>

Znajdź luki w firma.yaml na podstawie pytań w mailach."""

        try:
            parsed, meta = chat_json(
                model=MODEL_MINI,
                system=FACTS_GAP_SYSTEM,
                user=user_prompt,
                json_schema=FACTS_GAP_SCHEMA,
                temperature=0.2,
                max_tokens=1500,
            )
        except Exception as e:
            log.exception("Facts proposer error: %s", e)
            return 0

        n = 0
        for g in parsed.get("gaps", []):
            if g.get("confidence", 0) < 0.6 or g.get("frequency", 0) < 2:
                continue
            existing = db.scalar(
                select(Proposal).where(
                    Proposal.target == "firma_yaml",
                    Proposal.title == g["title"],
                    Proposal.status == "pending",
                )
            )
            if existing:
                continue
            prop = Proposal(
                target="firma_yaml",
                kind="content_gap",
                title=g["title"],
                description=g["what_to_add"],
                evidence=json.dumps({
                    "evidence": g["evidence"],
                    "section": g["section_suggestion"],
                    "frequency": g["frequency"],
                }, ensure_ascii=False),
                proposed_change=g["section_suggestion"] + "\n\n" + g["what_to_add"],
                confidence=g.get("confidence"),
            )
            db.add(prop)
            n += 1

        db.add(ApiCost(
            operation="propose_facts", model=meta["model"],
            tokens_input=meta["tokens_in"], tokens_output=meta["tokens_out"],
            cost_usd=meta["cost_usd"],
        ))
        db.commit()
        log.info("Facts proposer: %d propozycji z %d maili", n, len(mails))
        return n


def run_weekly():
    """Pełny tydzieniowy przebieg — używane przez cron systemd albo scheduler."""
    style_n = propose_style_rules()
    facts_n = propose_facts_gaps()
    return style_n, facts_n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s, f = run_weekly()
    print(f"Style: {s}, Facts: {f}")
