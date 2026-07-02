"""Helper do logowania akcji agenta (Klon) — live stream do UI."""
import json
import logging
from datetime import datetime

from .db import SessionLocal
from .models import AgentLog


_log = logging.getLogger("agent")


def say(message: str, *, event: str = "info", level: str = "info",
        mail_id: int | None = None, account_id: int | None = None,
        meta: dict | None = None, db=None):
    """Zapisz wpis agenta do logu. Db opcjonalna — gdy None, otwiera własną sesję.

    Wywołuj tam gdzie agent "myśli głośno":
    - fetcher: "Sprawdzam skrzynkę X", "Pobrałem 3 nowe maile"
    - analyzer: "Analizuję mail od X — kategoria Y, pilność Z"
    - drafter: "Piszę draft do X", "Verifier zablokował: ..."
    - api: "Wysłałem mail", "Zarchiwizowano"
    """
    own = db is None
    if own:
        db = SessionLocal()
    try:
        entry = AgentLog(
            timestamp=datetime.utcnow(),
            actor="Klon",
            level=level,
            event=event,
            message=message,
            mail_id=mail_id,
            account_id=account_id,
            meta=json.dumps(meta, ensure_ascii=False) if meta else None,
        )
        db.add(entry)
        if own:
            db.commit()
        _log.info("[agent:%s] %s", event, message)
    except Exception as e:
        _log.warning("agent_log.say failed: %s", e)
        if own:
            db.rollback()
    finally:
        if own:
            db.close()
