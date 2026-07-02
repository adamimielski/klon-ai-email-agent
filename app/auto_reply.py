"""Auto-reply: dla wybranych kategorii Klon wysyła SAM bez zatwierdzania.

Włączane przez `auto_reply.kategorie` w firma.yaml.
Bezpieczniki: verifier zielony + brak placeholderów + confidence min.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from .config import BASE_DIR
from .models import Mail, Draft, Account, VerificationLog
from .connectors import connector_for
from . import agent_log


_log = logging.getLogger("auto_reply")
_FACTS_PATH = BASE_DIR / "data" / "facts" / "firma.yaml"


def _load_auto_reply_config() -> dict:
    """Czyta sekcję auto_reply z firma.yaml. Zwraca defaults gdy brak."""
    if not _FACTS_PATH.exists():
        return {"enabled": False, "dry_run": True, "kategorie": []}
    try:
        data = yaml.safe_load(_FACTS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"enabled": False, "dry_run": True, "kategorie": []}
    cfg = data.get("auto_reply", {}) or {}
    cfg.setdefault("enabled", False)
    cfg.setdefault("dry_run", True)        # 🔴 DOMYŚLNIE TRUE — nic nie wysyła
    cfg.setdefault("kategorie", [])
    cfg.setdefault("warunki_minimalne", {})
    cfg["warunki_minimalne"].setdefault("confidence_min", 0.8)
    cfg["warunki_minimalne"].setdefault("verifier_must_be_green", True)
    cfg["warunki_minimalne"].setdefault("no_placeholders", True)
    return cfg


def should_auto_reply(mail: Mail, draft: Draft) -> tuple[bool, str]:
    """Sprawdza czy spełnione są wszystkie warunki auto-reply. Zwraca (decyzja, powód)."""
    cfg = _load_auto_reply_config()

    if not cfg["enabled"]:
        return False, "auto_reply wyłączony w firma.yaml"

    if mail.category not in cfg["kategorie"]:
        return False, f"kategoria '{mail.category}' nie jest na whitelist auto_reply"

    cond = cfg["warunki_minimalne"]

    if cond.get("verifier_must_be_green", True):
        if draft.verifier_can_send is False:
            return False, "verifier zablokował (czerwone flagi)"
        if draft.verifier_needs_ack is True:
            return False, "verifier ma żółte flagi (wymaga sprawdzenia)"

    if cond.get("no_placeholders", True):
        try:
            placeholders = json.loads(draft.placeholders or "[]")
            if placeholders:
                return False, f"draft ma {len(placeholders)} placeholderów"
        except Exception:
            pass

    if cond.get("confidence_min") is not None:
        if (draft.confidence or 0) < cond["confidence_min"]:
            return False, f"confidence {(draft.confidence or 0):.2f} < {cond['confidence_min']}"

    if mail.injection_risk and mail.injection_risk != "safe":
        return False, f"injection_risk={mail.injection_risk}"

    return True, "OK"


def attempt_auto_reply(db, mail: Mail, draft: Draft) -> bool:
    """Jeśli warunki OK — wysyła draft od razu w imieniu Adama. Zwraca True jeśli wysłano.

    Honoruje flag dry_run z firma.yaml: gdy true, NIE woła conn.send(),
    tylko symuluje wysyłkę (UI pokazuje "wysłane przez Klona", ale realnie nic
    nie wychodzi). Bezpieczne dla DEMO/portfolio.
    """
    cfg = _load_auto_reply_config()
    ok, reason = should_auto_reply(mail, draft)
    if not ok:
        _log.info("Mail %d: auto_reply skip (%s)", mail.id, reason)
        return False

    acc = db.get(Account, mail.account_id)
    if not acc:
        return False

    body_text = draft.edited_body or draft.body
    subject = ("Re: " + (mail.subject or "")) if not (mail.subject or "").startswith("Re:") else (mail.subject or "")

    is_dry_run = bool(cfg.get("dry_run", True))
    ext_id = None

    if is_dry_run:
        # SYMULACJA — nic nie wychodzi w sieć. Tylko UI flow.
        ext_id = f"DRYRUN-{int(datetime.utcnow().timestamp())}"
        _log.info("Mail %d: auto_reply DRY-RUN (no real send)", mail.id)
    else:
        try:
            conn = connector_for(acc)
            ext_id = conn.send(to=[mail.from_email], subject=subject, body=body_text)
        except Exception as e:
            _log.exception("Mail %d: auto_reply send failed: %s", mail.id, e)
            agent_log.say(
                f"Auto-reply do {mail.from_email or '?'} NIE POSZŁO: {e}",
                event="auto_reply_failed", level="error", mail_id=mail.id, db=db,
            )
            return False

    draft.sent_at = datetime.utcnow()
    draft.sent_by_user = "klon-auto-DRY" if is_dry_run else "klon-auto"
    draft.external_sent_id = ext_id
    mail.status = "sent"

    db.add(VerificationLog(
        mail_id=mail.id, draft_id=draft.id,
        user_email="klon-auto-DRY" if is_dry_run else "klon-auto",
        action="auto_replied", final_body=body_text,
        note="DRY-RUN (nie wysłane realnie)" if is_dry_run else None,
    ))

    agent_log.say(
        f"🚀 Wysłałem SAM odpowiedź do {mail.from_name or mail.from_email or '?'} "
        f"(kategoria {mail.category}, confidence {(draft.confidence or 0)*100:.0f}%)",
        event="auto_replied", level="success", mail_id=mail.id, db=db,
    )
    return True
