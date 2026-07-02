"""Abstrakcja MailConnector — wspólny interface dla Gmail API i IMAP."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class FetchedMail:
    """Jeden mail pobrany ze skrzynki — wynik connectora."""
    external_id: str
    thread_id: str | None
    from_email: str | None
    from_name: str | None
    to_emails: list[str] = field(default_factory=list)
    cc_emails: list[str] = field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    received_at: datetime | None = None
    is_reply: bool = False
    has_attachments: bool = False
    in_reply_to: str | None = None
    raw_payload: str | None = None  # debug


@dataclass
class FetchedSent:
    """Wysłany mail historyczny — do korpusu stylu."""
    external_id: str | None
    to_email: str | None
    subject: str | None
    body_text: str
    sent_at: datetime | None
    is_reply: bool
    in_reply_to_body: str | None = None


class MailConnector(ABC):
    """Wspólny interface dla wszystkich łączników mailowych."""

    account_id: int
    email: str

    @abstractmethod
    def fetch_new(self, since_marker: str | None = None) -> Iterable[FetchedMail]:
        """Pobiera nowe maile od ostatniego markera (history_id/UID).

        Zwraca iterator FetchedMail (możliwie streaming, nie ładujemy
        wszystkiego do RAM-u).
        """

    @abstractmethod
    def new_marker(self) -> str | None:
        """Zwraca aktualny marker do zapisania w accounts.last_history_id/last_uid."""

    @abstractmethod
    def list_sent(self, limit: int = 200) -> Iterable[FetchedSent]:
        """Pobiera ostatnie wysłane maile (do nauki stylu)."""

    @abstractmethod
    def create_draft(self, to: list[str], subject: str, body: str,
                     in_reply_to_external_id: str | None = None) -> str:
        """Tworzy draft w skrzynce (folder Drafts/Wersje robocze).

        Zwraca external_id draftu w skrzynce.
        """

    @abstractmethod
    def send(self, to: list[str], subject: str, body: str,
             in_reply_to_external_id: str | None = None) -> str:
        """Wysyła mail. Zwraca external_id wysłanego maila."""

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """Test połączenia. Zwraca (ok, opis)."""
