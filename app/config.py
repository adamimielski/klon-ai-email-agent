"""Konfiguracja aplikacji — czytane z env i .env."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Główna baza danych
    db_path: Path = DATA_DIR / "mails.db"

    # Klucz Fernet do szyfrowania haseł IMAP / refresh tokenów
    fernet_key: str = ""

    # Klucz Anthropic API (Claude) — opcjonalny
    anthropic_api_key: str = ""

    # Klucz OpenAI (LLM + embeddingi)
    openai_api_key: str = ""

    # Hard limit kosztów API (PLN/mc)
    api_cost_hard_limit_pln: float = 500.0

    # Identyfikacja użytkownika z Cloudflare Access
    cf_access_user_header: str = "Cf-Access-Authenticated-User-Email"
    cf_team_domain: str = "asystencibiznesowi.cloudflareaccess.com"

    # Whitelist właścicieli (override dla cf_access_user_header)
    owners: list[str] = ["imielski.adam@gmail.com", "kontakt@asystencibiznesowi.pl"]


settings = Settings()


def db_url() -> str:
    """SQLAlchemy URL."""
    return f"sqlite:///{settings.db_path}"
