"""Factory dla connectorów na podstawie wpisu Account z DB."""
import json

from ..models import Account
from ..security import decrypt
from .base import MailConnector
from .gmail import GmailConnector, build_credentials_from_token
from .imap import ImapConnector


def connector_for(account: Account) -> MailConnector:
    """Zwraca odpowiedni connector dla danego konta."""
    if account.connector_type == "gmail_api":
        if not account.refresh_token_encrypted:
            raise RuntimeError(f"Brak refresh_token dla {account.email}")
        token_json = json.loads(decrypt(account.refresh_token_encrypted))
        creds = build_credentials_from_token(token_json)
        return GmailConnector(
            account_id=account.id,
            email_addr=account.email,
            credentials=creds,
        )
    elif account.connector_type == "imap":
        if not account.imap_password_encrypted:
            raise RuntimeError(f"Brak hasła IMAP dla {account.email}")
        password = decrypt(account.imap_password_encrypted)
        return ImapConnector(
            account_id=account.id,
            email_addr=account.email,
            imap_host=account.imap_host,
            imap_port=account.imap_port,
            smtp_host=account.smtp_host,
            smtp_port=account.smtp_port,
            password=password,
        )
    else:
        raise RuntimeError(f"Nieznany connector_type: {account.connector_type}")


__all__ = ["MailConnector", "connector_for", "GmailConnector", "ImapConnector"]
