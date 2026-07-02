"""Gmail API connector — dla skrzynek Google (Workspace + consumer)."""
from __future__ import annotations

import base64
import email
from datetime import datetime, timezone
from email.header import decode_header, make_header
from typing import Iterable

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from .base import MailConnector, FetchedMail, FetchedSent


def _decode_h(raw: str | None) -> str | None:
    """Dekoduje nagłówek MIME =?UTF-8?Q?...?= na czytelny tekst."""
    if not raw:
        return None
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


def build_credentials_from_token(token_json: dict) -> Credentials:
    """Budowa Credentials z dict (tak jak zapisane po OAuth flow)."""
    return Credentials(
        token=token_json.get("token"),
        refresh_token=token_json.get("refresh_token"),
        token_uri=token_json.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_json["client_id"],
        client_secret=token_json["client_secret"],
        scopes=token_json.get("scopes", SCOPES),
    )


class GmailConnector(MailConnector):
    def __init__(self, *, account_id: int, email_addr: str, credentials: Credentials):
        self.account_id = account_id
        self.email = email_addr
        self._creds = credentials
        self._service = None
        self._last_history_id: str | None = None

    def _svc(self):
        if not self._creds.valid:
            if self._creds.expired and self._creds.refresh_token:
                self._creds.refresh(Request())
        if self._service is None:
            self._service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        return self._service

    def credentials_json(self) -> dict:
        """Zwraca aktualne credentials do zapisu (po odświeżeniu)."""
        return {
            "token": self._creds.token,
            "refresh_token": self._creds.refresh_token,
            "token_uri": self._creds.token_uri,
            "client_id": self._creds.client_id,
            "client_secret": self._creds.client_secret,
            "scopes": self._creds.scopes,
        }

    def test_connection(self) -> tuple[bool, str]:
        try:
            profile = self._svc().users().getProfile(userId="me").execute()
            return True, f"Gmail OK, {profile.get('messagesTotal', 0)} wiadomości w skrzynce"
        except Exception as e:
            return False, f"Gmail błąd: {e}"

    def _get_message(self, msg_id: str) -> FetchedMail | None:
        msg = self._svc().users().messages().get(
            userId="me", id=msg_id, format="raw"
        ).execute()
        raw_bytes = base64.urlsafe_b64decode(msg["raw"].encode("ascii"))
        parsed = email.message_from_bytes(raw_bytes)

        from_name, from_email = self._parse_addr(parsed.get("From"))
        from_name = _decode_h(from_name)
        to_list = [a for _, a in [self._parse_addr(t) for t in (parsed.get_all("To") or [])] if a]
        cc_list = [a for _, a in [self._parse_addr(t) for t in (parsed.get_all("Cc") or [])] if a]

        plain, html = self._get_body(parsed)

        date_hdr = parsed.get("Date")
        received_at = None
        if date_hdr:
            try:
                received_at = email.utils.parsedate_to_datetime(date_hdr)
                if received_at and received_at.tzinfo:
                    received_at = received_at.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                pass

        in_reply_to = parsed.get("In-Reply-To")
        has_attach = any(
            "attachment" in str(p.get("Content-Disposition", ""))
            for p in parsed.walk()
        ) if parsed.is_multipart() else False

        return FetchedMail(
            external_id=msg["id"],
            thread_id=msg.get("threadId"),
            from_email=from_email,
            from_name=from_name,
            to_emails=to_list,
            cc_emails=cc_list,
            subject=_decode_h(parsed.get("Subject")),
            body_text=plain,
            body_html=html,
            received_at=received_at,
            is_reply=bool(in_reply_to),
            has_attachments=has_attach,
            in_reply_to=in_reply_to,
        )

    @staticmethod
    def _parse_addr(raw):
        if not raw:
            return None, None
        name, addr = email.utils.parseaddr(raw)
        return name or None, addr or None

    @staticmethod
    def _get_body(msg: email.message.Message) -> tuple[str | None, str | None]:
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

    def fetch_new(self, since_marker: str | None = None) -> Iterable[FetchedMail]:
        """Pobiera nowe maile używając history API jeśli marker jest, inaczej list."""
        svc = self._svc()

        if since_marker:
            try:
                history = svc.users().history().list(
                    userId="me",
                    startHistoryId=since_marker,
                    historyTypes=["messageAdded"],
                ).execute()
                msg_ids = set()
                for h in history.get("history", []):
                    for added in h.get("messagesAdded", []):
                        msg_ids.add(added["message"]["id"])
                profile = svc.users().getProfile(userId="me").execute()
                self._last_history_id = profile.get("historyId")
                for mid in msg_ids:
                    fetched = self._get_message(mid)
                    if fetched:
                        yield fetched
                return
            except Exception:
                # historyId za stary → fallback do list
                pass

        # Pełna lista (pierwszy fetch albo history expired)
        list_resp = svc.users().messages().list(
            userId="me", labelIds=["INBOX"], maxResults=50
        ).execute()
        profile = svc.users().getProfile(userId="me").execute()
        self._last_history_id = profile.get("historyId")
        for m in list_resp.get("messages", []):
            fetched = self._get_message(m["id"])
            if fetched:
                yield fetched

    def new_marker(self) -> str | None:
        return self._last_history_id

    def list_sent(self, limit: int = 200) -> Iterable[FetchedSent]:
        svc = self._svc()
        token = None
        fetched_count = 0
        while fetched_count < limit:
            resp = svc.users().messages().list(
                userId="me",
                labelIds=["SENT"],
                maxResults=min(100, limit - fetched_count),
                pageToken=token,
            ).execute()
            for m in resp.get("messages", []):
                msg = svc.users().messages().get(
                    userId="me", id=m["id"], format="raw"
                ).execute()
                raw_bytes = base64.urlsafe_b64decode(msg["raw"].encode("ascii"))
                parsed = email.message_from_bytes(raw_bytes)
                to_name, to_email = self._parse_addr((parsed.get_all("To") or [None])[0])
                plain, _ = self._get_body(parsed)
                if not plain or len(plain.strip()) < 30:
                    fetched_count += 1
                    continue
                date_hdr = parsed.get("Date")
                sent_at = None
                if date_hdr:
                    try:
                        sent_at = email.utils.parsedate_to_datetime(date_hdr)
                        if sent_at and sent_at.tzinfo:
                            sent_at = sent_at.astimezone(timezone.utc).replace(tzinfo=None)
                    except Exception:
                        pass
                yield FetchedSent(
                    external_id=msg["id"],
                    to_email=to_email,
                    subject=_decode_h(parsed.get("Subject")),
                    body_text=plain,
                    sent_at=sent_at,
                    is_reply=bool(parsed.get("In-Reply-To")),
                    in_reply_to_body=None,
                )
                fetched_count += 1
                if fetched_count >= limit:
                    return
            token = resp.get("nextPageToken")
            if not token:
                return

    def create_draft(self, to, subject, body, in_reply_to_external_id=None) -> str:
        msg = email.message.EmailMessage()
        msg["From"] = self.email
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        body_req = {"message": {"raw": raw}}
        if in_reply_to_external_id:
            # tu można dorzucić threadId jak mamy
            pass
        draft = self._svc().users().drafts().create(
            userId="me", body=body_req
        ).execute()
        return draft["id"]

    def send(self, to, subject, body, in_reply_to_external_id=None) -> str:
        msg = email.message.EmailMessage()
        msg["From"] = self.email
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        sent = self._svc().users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return sent["id"]
