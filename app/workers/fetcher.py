"""Fetcher worker — pobiera nowe maile z każdego aktywnego konta co X min."""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Tuple

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal, init_db
from ..models import Account, Mail
from ..connectors import connector_for
from ..config import settings
from .. import agent_log


log = logging.getLogger("fetcher")


def fetch_account_once(db, account: Account) -> Tuple[int, str | None]:
    """Pobiera nowe maile dla jednego konta. Zwraca (liczba_nowych, err)."""
    # Demo accounts (bez credentials) — pomijaj cicho
    if account.connector_type == "imap" and not account.imap_password_encrypted:
        return 0, None
    if account.connector_type == "gmail_api" and not account.refresh_token_encrypted:
        return 0, None
    try:
        conn = connector_for(account)
    except Exception as e:
        return 0, f"connector_for: {e}"

    marker = account.last_history_id if account.connector_type == "gmail_api" \
             else (str(account.last_uid) if account.last_uid else None)

    new_count = 0
    err = None
    try:
        for fetched in conn.fetch_new(since_marker=marker):
            mail = Mail(
                account_id=account.id,
                external_id=fetched.external_id,
                thread_id=fetched.thread_id,
                from_email=fetched.from_email,
                from_name=fetched.from_name,
                to_emails=json.dumps(fetched.to_emails or []),
                cc_emails=json.dumps(fetched.cc_emails or []),
                subject=fetched.subject,
                body_text=fetched.body_text,
                body_html=fetched.body_html,
                received_at=fetched.received_at,
                is_reply=fetched.is_reply,
                has_attachments=fetched.has_attachments,
                in_reply_to=fetched.in_reply_to,
                status="new",
            )
            db.add(mail)
            try:
                db.flush()
                new_count += 1
                agent_log.say(
                    f"Nowy mail od {fetched.from_name or fetched.from_email or '?'}: „{(fetched.subject or '(bez tematu)')[:60]}”",
                    event="mail_received", level="info",
                    mail_id=mail.id, account_id=account.id, db=db,
                )
            except IntegrityError:
                # dedup po (account_id, external_id) unique index
                db.rollback()
                continue
    except Exception as e:
        err = f"fetch_new: {e}"
        log.exception("Błąd fetch_new dla %s", account.email)
        agent_log.say(
            f"Błąd pobierania ze skrzynki {account.email}: {e}",
            event="fetch_error", level="error", account_id=account.id,
        )
        db.rollback()

    # Zaktualizuj marker (nawet jeśli częściowy sukces)
    new_marker = conn.new_marker()
    if new_marker:
        if account.connector_type == "gmail_api":
            account.last_history_id = new_marker
        else:
            try:
                account.last_uid = int(new_marker)
            except (TypeError, ValueError):
                pass

    # Jeśli to Gmail, zapisz odświeżony token (gdyby się odświeżył)
    if account.connector_type == "gmail_api" and hasattr(conn, "credentials_json"):
        from ..security import encrypt
        try:
            account.refresh_token_encrypted = encrypt(json.dumps(conn.credentials_json()))
        except Exception:
            log.warning("Nie udało się zapisać odświeżonego tokenu dla %s", account.email)

    account.last_fetch_at = datetime.utcnow()
    account.last_fetch_error = err
    db.commit()
    return new_count, err


def fetch_all_accounts():
    """Pobiera maile dla wszystkich aktywnych kont.

    Wyłączane przez env MAILDASH_FETCH_DISABLED=1 (DEMO mode — brak
    pobierania z prawdziwej skrzynki, używamy tylko maili wstawionych
    przez app.cli_seed).
    """
    if os.environ.get("MAILDASH_FETCH_DISABLED", "").lower() in ("1", "true", "yes"):
        log.info("MAILDASH_FETCH_DISABLED=1, pomijam fetch (DEMO mode)")
        return
    with SessionLocal() as db:
        accounts = db.scalars(select(Account).where(Account.active.is_(True))).all()
        total_new = 0
        for acc in accounts:
            count, err = fetch_account_once(db, acc)
            total_new += count or 0
            if err:
                log.warning("%s: %s", acc.email, err)
            else:
                log.info("%s: +%d maili", acc.email, count)
        if total_new > 0:
            agent_log.say(
                f"Cykl fetch zakończony: {total_new} nowych maili w {len(accounts)} skrzynkach",
                event="fetch_done", level="info", db=db,
            )
            db.commit()


def analyze_new_mails():
    """Analizuje wszystkie maile ze status='new' (do 20 na raz)."""
    try:
        from .analyzer import analyze_pending
        n = analyze_pending(limit=20)
        if n:
            log.info("Analyzer: przeanalizowano %d maili", n)
    except Exception as e:
        log.exception("Analyzer error: %s", e)


def draft_auto_mails():
    """Generuje drafty dla maili z draft_strategy='auto'."""
    try:
        from .drafter import draft_pending
        n = draft_pending(limit=10)
        if n:
            log.info("Drafter: wygenerowano %d draftów", n)
    except Exception as e:
        log.exception("Drafter error: %s", e)


def weekly_self_tuning():
    """Tygodniowy przebieg self-tuningu — propozycje stylu + luk firma.yaml."""
    try:
        from .proposer import run_weekly
        s, f = run_weekly()
        if s or f:
            log.info("Proposer: style=%d, facts=%d", s, f)
    except Exception as e:
        log.exception("Proposer error: %s", e)


def run_scheduler():
    """Scheduler: fetch 2min, analyze 1min, draft 2min, self-tuning co tydzień."""
    init_db()
    agent_log.say("Klon uruchomiony — sprawdzam skrzynki co 2 min, analizuję co 1 min",
                  event="startup", level="success")
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(fetch_all_accounts, "interval", minutes=2,
                  next_run_time=datetime.utcnow(), id="fetch_all", max_instances=1)
    sched.add_job(analyze_new_mails, "interval", minutes=1,
                  next_run_time=datetime.utcnow(), id="analyze", max_instances=1)
    sched.add_job(draft_auto_mails, "interval", minutes=2,
                  next_run_time=datetime.utcnow(), id="draft", max_instances=1)
    # Self-tuning: co 7 dni o 3:00 UTC (nie spamuje API)
    sched.add_job(weekly_self_tuning, "interval", days=7,
                  next_run_time=datetime.utcnow() + timedelta(hours=2),
                  id="self_tuning", max_instances=1)
    log.info("Worker uruchomiony (fetch 2min, analyze 1min, draft 2min, self-tuning 7d)")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Worker zatrzymany")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    run_scheduler()
