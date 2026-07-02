"""IMAP/SMTP connector — dla skrzynek na hostingu (LH, home.pl, OVH).

Protokół IMAP (port 993, SSL) do czytania + SMTP (port 587, STARTTLS) do wysyłania.
"""
import email
import email.utils
import email.message
import smtplib
import ssl
from datetime import datetime, timezone
from email.header import decode_header, make_header
from typing import Iterable

from imapclient import IMAPClient

from .base import MailConnector, FetchedMail, FetchedSent


def _decode_header(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def _parse_addr(raw: str | None) -> tuple[str | None, str | None]:
    """Z "John <a@b.com>" -> ("John", "a@b.com")."""
    if not raw:
        return None, None
    name, addr = email.utils.parseaddr(raw)
    name = _decode_header(name) or None
    return (name or None, addr or None)


def _get_body(msg: email.message.Message) -> tuple[str | None, str | None]:
    """Wyciąga text/plain + text/html z wiadomości MIME."""
    plain = None
    html = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                html = text
            else:
                plain = text
    return plain, html


class ImapConnector(MailConnector):
    def __init__(self, *, account_id: int, email_addr: str,
                 imap_host: str, imap_port: int,
                 smtp_host: str, smtp_port: int,
                 password: str):
        self.account_id = account_id
        self.email = email_addr
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.password = password
        self._last_seen_uid: int | None = None

    def _imap(self) -> IMAPClient:
        client = IMAPClient(self.imap_host, port=self.imap_port, ssl=True, timeout=30)
        client.login(self.email, self.password)
        return client

    def test_connection(self) -> tuple[bool, str]:
        try:
            with self._imap() as c:
                folders = c.list_folders()
                return True, f"IMAP OK, {len(folders)} folderów"
        except Exception as e:
            return False, f"IMAP błąd: {e}"

    def fetch_new(self, since_marker: str | None = None) -> Iterable[FetchedMail]:
        """Pobiera maile z INBOX o UID > since_marker."""
        try:
            since_uid = int(since_marker) if since_marker else 0
        except (TypeError, ValueError):
            since_uid = 0

        with self._imap() as c:
            c.select_folder("INBOX", readonly=True)
            # UID FETCH od (since_uid+1):*
            criteria = ["UID", f"{since_uid + 1}:*"] if since_uid else ["ALL"]
            uids = c.search(criteria)
            if not uids:
                return

            for uid in uids:
                if uid <= since_uid:
                    continue
                self._last_seen_uid = max(self._last_seen_uid or 0, uid)
                fetched = c.fetch([uid], ["RFC822", "INTERNALDATE"])
                payload = fetched.get(uid)
                if not payload:
                    continue
                raw = payload.get(b"RFC822") or payload.get("RFC822")
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                from_name, from_email = _parse_addr(msg.get("From"))
                to_raw = msg.get_all("To") or []
                cc_raw = msg.get_all("Cc") or []
                to_list = [a for _, a in [_parse_addr(t) for t in to_raw] if a]
                cc_list = [a for _, a in [_parse_addr(t) for t in cc_raw] if a]

                plain, html = _get_body(msg)

                date_hdr = msg.get("Date")
                received_at = None
                if date_hdr:
                    try:
                        received_at = email.utils.parsedate_to_datetime(date_hdr)
                        if received_at and received_at.tzinfo:
                            received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
                    except Exception:
                        received_at = None

                in_reply_to = msg.get("In-Reply-To")
                has_attach = any(
                    "attachment" in str(p.get("Content-Disposition", ""))
                    for p in msg.walk()
                ) if msg.is_multipart() else False

                yield FetchedMail(
                    external_id=str(uid),
                    thread_id=in_reply_to or msg.get("References"),
                    from_email=from_email,
                    from_name=from_name,
                    to_emails=to_list,
                    cc_emails=cc_list,
                    subject=_decode_header(msg.get("Subject")),
                    body_text=plain,
                    body_html=html,
                    received_at=received_at,
                    is_reply=bool(in_reply_to),
                    has_attachments=has_attach,
                    in_reply_to=in_reply_to,
                    raw_payload=None,  # zbyt duże by trzymać
                )

    def new_marker(self) -> str | None:
        return str(self._last_seen_uid) if self._last_seen_uid else None

    def list_sent(self, limit: int = 200) -> Iterable[FetchedSent]:
        """Pobiera ostatnie maile z folderu 'Sent'.

        Próbuje znaleźć folder po popularnych nazwach (Sent, Sent Items, Wyslane).
        """
        with self._imap() as c:
            folders = [f[2] for f in c.list_folders()]
            sent_folder = None
            candidates = ["Sent", "Sent Items", "INBOX.Sent", "Wysłane",
                          "Wyslane", "Elementy wysłane", "[Gmail]/Sent Mail"]
            for cand in candidates:
                if cand in folders:
                    sent_folder = cand
                    break
            if not sent_folder:
                # heurystyka: cokolwiek z "sent" w nazwie
                for f in folders:
                    if "sent" in f.lower() or "wysłan" in f.lower() or "wyslan" in f.lower():
                        sent_folder = f
                        break
            if not sent_folder:
                return

            c.select_folder(sent_folder, readonly=True)
            all_uids = c.search(["ALL"])
            # ostatnie `limit`
            uids = sorted(all_uids)[-limit:] if all_uids else []

            for uid in reversed(uids):
                fetched = c.fetch([uid], ["RFC822"])
                payload = fetched.get(uid)
                if not payload:
                    continue
                raw = payload.get(b"RFC822") or payload.get("RFC822")
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                to_name, to_email = _parse_addr((msg.get_all("To") or [None])[0])
                plain, _ = _get_body(msg)
                if not plain or len(plain.strip()) < 30:
                    continue  # pomiń 1-liniowe odpowiedzi
                date_hdr = msg.get("Date")
                sent_at = None
                if date_hdr:
                    try:
                        sent_at = email.utils.parsedate_to_datetime(date_hdr)
                        if sent_at and sent_at.tzinfo:
                            sent_at = sent_at.astimezone(timezone.utc).replace(tzinfo=None)
                    except Exception:
                        pass

                yield FetchedSent(
                    external_id=str(uid),
                    to_email=to_email,
                    subject=_decode_header(msg.get("Subject")),
                    body_text=plain,
                    sent_at=sent_at,
                    is_reply=bool(msg.get("In-Reply-To")),
                    in_reply_to_body=None,
                )

    def create_draft(self, to, subject, body, in_reply_to_external_id=None) -> str:
        msg = email.message.EmailMessage()
        msg["From"] = self.email
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        with self._imap() as c:
            drafts_folder = "Drafts"
            folders = [f[2] for f in c.list_folders()]
            for cand in ["Drafts", "INBOX.Drafts", "Wersje robocze", "Szkice", "[Gmail]/Drafts"]:
                if cand in folders:
                    drafts_folder = cand
                    break
            c.append(drafts_folder, msg.as_bytes(), flags=[r"\Draft"])
        return f"imap-draft-{datetime.utcnow().isoformat()}"

    def send(self, to, subject, body, in_reply_to_external_id=None) -> str:
        msg = email.message.EmailMessage()
        msg["From"] = self.email
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Message-ID"] = email.utils.make_msgid()
        msg.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as s:
            s.starttls(context=context)
            s.login(self.email, self.password)
            s.send_message(msg)
        return msg["Message-ID"]
