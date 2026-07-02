"""API endpoints dla propozycji self-tuningu."""
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import Proposal
from .mails import require_owner


router = APIRouter(prefix="/api/proposals")


@router.get("")
def list_proposals(user=Depends(require_owner), db: Session = Depends(get_session),
                   status: str = "pending"):
    rows = db.scalars(
        select(Proposal).where(Proposal.status == status).order_by(desc(Proposal.created_at))
    ).all()
    return [
        {
            "id": p.id,
            "target": p.target,
            "kind": p.kind,
            "title": p.title,
            "description": p.description,
            "evidence": json.loads(p.evidence) if p.evidence else None,
            "proposed_change": p.proposed_change,
            "confidence": p.confidence,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]


class DecisionBody(BaseModel):
    note: str | None = None


@router.post("/{prop_id}/accept")
def accept_proposal(prop_id: int, body: DecisionBody | None = None,
                    user=Depends(require_owner), db: Session = Depends(get_session)):
    p = db.get(Proposal, prop_id)
    if not p:
        raise HTTPException(404)
    if p.status != "pending":
        raise HTTPException(400, f"Już {p.status}")

    p.status = "accepted"
    p.decided_at = datetime.utcnow()
    p.decided_by = user

    # Dla style_rule — zaaplikuj do KLON.md (append do sekcji)
    if p.target == "klon_md" and p.kind == "style_rule":
        from ..config import BASE_DIR
        klon_path = BASE_DIR / "data" / "rules" / "KLON.md"
        klon_path.parent.mkdir(parents=True, exist_ok=True)
        if not klon_path.exists():
            klon_path.write_text(
                "# KLON.md — Reguły stylu pisania Adama\n\n## Reguły (self-tuning)\n\n",
                encoding="utf-8",
            )
        existing = klon_path.read_text(encoding="utf-8")
        marker = "## Reguły (self-tuning)\n\n"
        new_rule = f"### {p.title}\n{p.proposed_change}\n\n_Zaakceptowano {datetime.utcnow().date()} przez {user}_\n\n"
        if marker in existing:
            existing = existing.replace(marker, marker + new_rule, 1)
        else:
            existing += "\n\n" + marker + new_rule
        klon_path.write_text(existing, encoding="utf-8")

    # Dla firma_yaml — NIE auto-apply, tylko zapisujemy że Adam zaakceptował,
    # on sam edytuje DATA/facts/firma.yaml w vault.

    db.commit()
    return {"ok": True, "id": p.id, "status": p.status}


@router.post("/{prop_id}/reject")
def reject_proposal(prop_id: int, body: DecisionBody | None = None,
                    user=Depends(require_owner), db: Session = Depends(get_session)):
    p = db.get(Proposal, prop_id)
    if not p:
        raise HTTPException(404)
    p.status = "rejected"
    p.decided_at = datetime.utcnow()
    p.decided_by = user
    db.commit()
    return {"ok": True}


@router.post("/run-now")
def run_now(user=Depends(require_owner)):
    """Wymuś przebieg proposera (debug/test)."""
    from ..workers.proposer import run_weekly
    s, f = run_weekly()
    return {"style_proposals": s, "facts_proposals": f}
