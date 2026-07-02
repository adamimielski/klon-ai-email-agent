"""Modele SQLAlchemy — schema bazy mail-dashboard."""
from datetime import datetime
from sqlalchemy import (
    String, Integer, Boolean, Float, Text, DateTime, ForeignKey, LargeBinary, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def _now() -> datetime:
    return datetime.utcnow()


class Account(Base):
    """Konto mailowe (Gmail API lub IMAP). Dual stack."""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connector_type: Mapped[str] = mapped_column(String(16))  # 'gmail_api' | 'imap'

    # gmail_api
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # imap
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    mails: Mapped[list["Mail"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Mail(Base):
    """Mail przychodzący — pobrany z Gmaila lub IMAP."""
    __tablename__ = "mails"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)

    # zewnętrzne ID (Gmail message_id lub IMAP UID stringified)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), index=True)

    from_email: Mapped[str | None] = mapped_column(String(255), index=True)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_emails: Mapped[str | None] = mapped_column(Text)  # JSON array
    cc_emails: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array

    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text_sanitized: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False)
    in_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # surowy MIME / payload — debugowanie
    raw_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    # wyniki analizy (uzupełniane w Etap 4)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    urgency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    injection_risk: Mapped[str | None] = mapped_column(String(32), nullable=True)
    draft_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 'auto' = powtarzalny, drafter pisze od razu
    # 'needs_decision' = nietypowy, czeka na wybór Adama
    decision_options: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON list: [{"label": "...", "context": "..."}, ...]
    chosen_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Po wyborze Adama: index opcji lub tekst custom

    # status w pipeline'ie
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    # 'new' | 'analyzed' | 'drafted' | 'sent' | 'archived' | 'ignored' | 'auto_archived'
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    account: Mapped["Account"] = relationship(back_populates="mails")
    drafts: Mapped[list["Draft"]] = relationship(back_populates="mail", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_mails_account_external", "account_id", "external_id", unique=True),
    )


class Draft(Base):
    """Draft wygenerowany przez Claude'a (KLON-Dashboard)."""
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    mail_id: Mapped[int] = mapped_column(ForeignKey("mails.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    facts_used: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    placeholders: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    uncertain_claims: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    needs_human_decision: Mapped[bool] = mapped_column(Boolean, default=False)

    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # finalna wersja po edycji człowieka
    edited_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    was_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_by_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_sent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Wyniki verifiera (warstwy anti-halucynacje)
    verifier_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    verifier_can_send: Mapped[bool] = mapped_column(Boolean, default=True)
    verifier_needs_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    verifier_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    mail: Mapped["Mail"] = relationship(back_populates="drafts")


class SentExample(Base):
    """Wysłany mail historyczny — korpus stylu (KLON)."""
    __tablename__ = "sent_examples"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    to_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    in_reply_to_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # wektor embeddingu (binary; uzupełnia się w Etap 3)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Lesson(Base):
    """Lekcja self-tuning — różnica AI vs człowiek."""
    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), index=True)
    ai_version: Mapped[str] = mapped_column(Text)
    human_version: Mapped[str] = mapped_column(Text)
    diff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    incorporated_to_klon: Mapped[bool] = mapped_column(Boolean, default=False)


class Proposal(Base):
    """Propozycja aktualizacji KLON.md lub firma.yaml — wymaga zatwierdzenia."""
    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    target: Mapped[str] = mapped_column(String(32))  # 'klon_md' | 'firma_yaml'
    kind: Mapped[str] = mapped_column(String(64))  # 'style_rule' | 'price_gap' | 'industry_gap' | etc.
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    proposed_change: Mapped[str | None] = mapped_column(Text, nullable=True)  # YAML/MD diff
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # 'pending' | 'accepted' | 'rejected'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class VerificationLog(Base):
    """Audit log — kto co zrobił z draftem."""
    __tablename__ = "verification_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("drafts.id"), nullable=True, index=True)
    mail_id: Mapped[int | None] = mapped_column(ForeignKey("mails.id"), nullable=True, index=True)
    user_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    action: Mapped[str] = mapped_column(String(32))
    # 'viewed' | 'edited' | 'sent' | 'saved_as_gmail_draft' | 'archived' | 'rejected' | 'regenerated'
    flags_at_time: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    yellow_flags_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    final_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_from_ai: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentLog(Base):
    """Log akcji agenta (Klon) — live stream do UI."""
    __tablename__ = "agent_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    actor: Mapped[str] = mapped_column(String(32), default="Klon")
    level: Mapped[str] = mapped_column(String(16), default="info")
    # 'info' | 'action' | 'warn' | 'error' | 'success'
    event: Mapped[str] = mapped_column(String(64))
    # 'fetch_start' | 'fetch_done' | 'analyze' | 'draft' | 'draft_blocked' | 'decision_needed'
    # 'archived' | 'sent' | 'self_tune' | 'edit' | 'startup' | 'idle'
    message: Mapped[str] = mapped_column(Text)
    mail_id: Mapped[int | None] = mapped_column(ForeignKey("mails.id"), nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON


class ApiCost(Base):
    """Log kosztów Anthropic/OpenAI API."""
    __tablename__ = "api_costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    operation: Mapped[str] = mapped_column(String(32))  # analyze/draft/embed/verify/detect_injection
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    mail_id: Mapped[int | None] = mapped_column(ForeignKey("mails.id"), nullable=True)
