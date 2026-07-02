"""Szyfrowanie sekretów (hasła IMAP, refresh tokeny Gmail) przez Fernet.

Klucz Fernet jest w env FERNET_KEY (base64). Bez klucza aplikacja
odmówi szyfrowania i pisze do logów ostrzeżenie.
"""
from cryptography.fernet import Fernet, InvalidToken
from .config import settings


def _fernet() -> Fernet:
    if not settings.fernet_key:
        raise RuntimeError(
            "FERNET_KEY nie ustawiony w .env — wygeneruj: "
            "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    return Fernet(settings.fernet_key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt(cipher: str) -> str:
    try:
        return _fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError(f"Nieprawidłowy ciphertext (zły FERNET_KEY?): {e}")


def generate_key() -> str:
    """Pomocnik — wygeneruj nowy klucz Fernet."""
    return Fernet.generate_key().decode("utf-8")
