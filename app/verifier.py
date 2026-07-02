"""Fact Verifier + walidator deterministyczny.

3 warstwy ochrony przeciw halucynacjom:
1. LLM Verifier (GPT-4o-mini) — wymienia każdy konkret i sprawdza czy ma źródło
2. Regex/dict walidator — porównuje ceny/linki/frazy z firma.yaml
3. Wynik łączony → flagi dla UI (🟢/🟡/🔴)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .llm import chat_json, MODEL_MINI
from .config import BASE_DIR


FACTS_PATH = BASE_DIR / "data" / "facts" / "firma.yaml"


@dataclass
class Flag:
    severity: str  # 'green' | 'yellow' | 'red'
    category: str  # 'price' | 'phrase' | 'link' | 'email' | 'iban' | 'phone' | 'claim' | 'system'
    message: str
    snippet: str | None = None


@dataclass
class VerificationResult:
    flags: list[Flag] = field(default_factory=list)
    can_send: bool = True   # False jeśli czerwony obecny
    needs_acknowledgement: bool = False  # True jeśli żółty obecny

    @property
    def has_red(self) -> bool:
        return any(f.severity == "red" for f in self.flags)

    @property
    def has_yellow(self) -> bool:
        return any(f.severity == "yellow" for f in self.flags)


# =====================================================================
# Helpers: ładowanie firma.yaml
# =====================================================================

_facts_cache: dict | None = None


def load_facts() -> dict:
    global _facts_cache
    if _facts_cache is None:
        if FACTS_PATH.exists():
            _facts_cache = yaml.safe_load(FACTS_PATH.read_text(encoding="utf-8")) or {}
        else:
            _facts_cache = {}
    return _facts_cache


def extract_allowed_prices(facts: dict) -> set[str]:
    """Wyciąga wszystkie ceny dozwolone (jako lowercase strings)."""
    prices = set()

    def walk(d):
        if isinstance(d, dict):
            for v in d.values():
                walk(v)
        elif isinstance(d, list):
            for v in d:
                walk(v)
        elif isinstance(d, str):
            # szukamy wzorca "XXX PLN" / "X 000 PLN" / "X-Y PLN"
            for m in re.finditer(r"\d[\d\s]*\d?\s*(?:PLN|zł|EUR|USD)", d, re.I):
                prices.add(re.sub(r"\s+", " ", m.group(0).lower()))

    walk(facts)
    return prices


def extract_blocked_phrases(facts: dict) -> list[str]:
    """Wyciąga listę zakazanych fraz."""
    blocked = facts.get("zakazane_obietnice", [])
    return [p.lower() for p in blocked if isinstance(p, str)]


def extract_allowed_domains(facts: dict) -> set[str]:
    """Domeny które wolno linkować."""
    domains = set()
    dl = facts.get("dozwolone_linki", {})
    if isinstance(dl, dict):
        for d in dl.get("domeny", []) or []:
            domains.add(d.lower())
        for url in (dl.get("konkretne_url", {}) or {}).values():
            if isinstance(url, str):
                m = re.search(r"https?://([^/]+)", url)
                if m:
                    domains.add(m.group(1).lower())
    return domains


# =====================================================================
# Warstwa 1: Regex / dict walidator
# =====================================================================

def deterministic_check(draft_body: str) -> list[Flag]:
    flags: list[Flag] = []
    facts = load_facts()
    body_lower = draft_body.lower()

    # 1. Zakazane frazy
    blocked = extract_blocked_phrases(facts)
    for phrase in blocked:
        if phrase in body_lower:
            flags.append(Flag(
                severity="red",
                category="phrase",
                message=f"Zakazana fraza: '{phrase}'",
                snippet=phrase,
            ))

    # 2. Ceny — każda kwota w drafcie sprawdzana czy istnieje w firma.yaml
    allowed_prices = extract_allowed_prices(facts)
    # tylko jeśli mamy whitelist
    if allowed_prices:
        for m in re.finditer(r"(\d[\d\s]{0,8}\d?)\s*(PLN|zł|EUR|USD)", draft_body, re.I):
            amount = re.sub(r"\s+", " ", m.group(0).lower())
            # akceptuj jeśli pasuje do listy LUB jest wynikiem mnożenia (np. 5 × 97 = 485)
            ok = any(amount == p or amount.replace(" ", "") == p.replace(" ", "")
                     for p in allowed_prices)
            # Sprawdź również obliczenia (np. "1485 PLN" = 5 * 297) — heurystyka
            if not ok:
                try:
                    num = int(re.sub(r"\D", "", m.group(1)))
                    # czy jest wielokrotnością któregoś allowedu?
                    for p in allowed_prices:
                        pnum = int(re.sub(r"\D", "", p))
                        if pnum and num % pnum == 0 and 1 <= num // pnum <= 100:
                            ok = True
                            break
                except (ValueError, ZeroDivisionError):
                    pass
            if not ok:
                flags.append(Flag(
                    severity="yellow",
                    category="price",
                    message=f"Cena '{m.group(0)}' nie pasuje do firma.yaml",
                    snippet=m.group(0),
                ))

    # 3. Linki — tylko whitelist
    allowed_domains = extract_allowed_domains(facts)
    if allowed_domains:
        for m in re.finditer(r"https?://([^\s/<>]+)", draft_body):
            dom = m.group(1).lower()
            if not any(dom == ad or dom.endswith("." + ad) for ad in allowed_domains):
                flags.append(Flag(
                    severity="red",
                    category="link",
                    message=f"Nieautoryzowana domena: {dom}",
                    snippet=m.group(0),
                ))

    # 4. IBAN (tylko whitelist)
    for m in re.finditer(r"\b(?:PL)?\s?\d{2}(?:\s?\d{4}){6}\b", draft_body):
        flags.append(Flag(
            severity="yellow",
            category="iban",
            message=f"Numer konta (IBAN) w drafcie: {m.group(0)} — sprawdź czy nasz",
            snippet=m.group(0),
        ))

    # 5. Telefon (informacyjnie)
    for m in re.finditer(r"\b(?:\+?48\s?)?(?:\d{3}[\s\-]?){3}\b", draft_body):
        # za szeroka regula — pominęmy
        pass

    return flags


# =====================================================================
# Warstwa 2: LLM Verifier (GPT-mini)
# =====================================================================

VERIFIER_SYSTEM = """Jesteś weryfikatorem faktów dla maili wychodzących.

Twoja praca: znajdź WYŁĄCZNIE KONKRETNE, FALSYFIKOWALNE fakty w drafcie i sprawdź czy mają źródło.

CO FLAGOWAĆ (konkretne, sprawdzalne):
✅ Liczby/ceny: "297 PLN", "5 dni", "od 2024 roku", "30% rabatu"
✅ Daty/terminy: "do piątku", "15 maja", "w przyszłym tygodniu"
✅ Nazwy własne produktów Adama: "Asystenci Biznesowi", "Kurs Reklam AI", "MARK Mini" (tylko jeśli wymienione W DRAFCIE jako konkretny produkt)
✅ Imiona / nazwiska klientów: "Marek", "Pan Kowalski"
✅ Konkretne URL-e i emaile: "calendly.com/adam", "support@..."
✅ Konkretne obietnice: "gwarantuję X", "zwrócę pieniądze do 7 dni"
✅ Referencje do przeszłości: "jak mówiliśmy wczoraj", "po naszej rozmowie z piątku"
✅ Konkretne funkcje techniczne: "integracja z HubSpot", "API GraphQL"

CZEGO NIE FLAGOWAĆ (abstrakcje / ogólne słowa):
❌ Ogólne pojęcia: "AI", "automatyzacje", "narzędzia", "system", "rozwiązanie"
❌ Pojęcia opisowe: "potrzeby klienta", "zakres projektu", "wycena indywidualna", "współpraca", "oczekiwania", "problemy"
❌ Standardowe zwroty grzecznościowe: "dzięki", "powodzenia", "pozdrawiam"
❌ Pytania otwarte: "jakie procesy", "co byś chciał zautomatyzować", "kiedy ci pasuje"
❌ Słowa-kategorie: "produkt", "usługa", "klient", "firma", "branża"
❌ Subiektywne oceny: "ciekawe", "fajne", "interesujące"

ZASADA: jeśli zdanie z draftu miałoby sens dla DOWOLNEJ firmy bez weryfikacji — to NIE jest claim do flagowania.
Przykład: "Chciałbym dowiedzieć się więcej o Twoich potrzebach" → NIE flaguj.
Przykład: "Cena to 297 PLN" → flaguj (to konkret).

ŹRÓDŁA DOZWOLONE:
1. <facts> — firma.yaml (oficjalne fakty firmy)
2. <user_email> — treść maila klienta
3. <thread_history> — historia wątku
4. Wynik obliczenia (5 × 297 PLN = 1485 PLN — jeśli składniki w faktach)

OUTPUT: lista wyłącznie KONKRETNYCH claims (nie abstrakcji). Pusta lista jeśli draft to ogólnikowa odpowiedź bez konkretów — to OK."""


VERIFIER_SCHEMA = {
    "name": "fact_verification",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim": {"type": "string", "description": "Konkretny fakt z draftu"},
                        "type": {
                            "type": "string",
                            "enum": ["price", "date", "duration", "feature", "name", "reference", "calculation", "promise", "other"],
                        },
                        "source_found": {"type": "boolean"},
                        "source_quote": {"type": "string", "description": "Cytat ze źródła lub puste"},
                        "risk": {"type": "string", "enum": ["safe", "uncertain", "hallucination"]},
                    },
                    "required": ["claim", "type", "source_found", "source_quote", "risk"],
                },
            },
            "overall_assessment": {"type": "string", "enum": ["safe", "needs_review", "block"]},
        },
        "required": ["claims", "overall_assessment"],
    },
}


# Lista abstrakcyjnych pojęć — verifier ich nie powinien flagować jako "claims".
# Jeśli mimo promptu LLM je zaflaguje — wycinamy w post-processingu.
_ABSTRACT_NON_CLAIMS = {
    "ai", "narzędzia ai", "automatyzacje", "automatyzacja", "narzędzia",
    "system", "rozwiązanie", "rozwiązania", "produkt", "usługa", "usługi",
    "klient", "klienci", "firma", "firmy", "branża", "branże",
    "potrzeby", "potrzeby klienta", "zakres", "zakres projektu",
    "wycena", "wycena indywidualna", "współpraca", "partnership",
    "oczekiwania", "problemy", "problem", "kontakt", "rozmowa",
    "demo", "prezentacja", "spotkanie", "konsultacja",
    "informacje", "szczegóły", "detale",
    "proces", "procesy", "praca", "biznes",
    # Standardowe długości spotkań (ujęte w firma.yaml jako standard)
    "15 min", "15 minut", "30 min", "30 minut", "45 min", "45 minut",
    "godzina", "1 godzina", "godzinka", "ok. 30 min", "ok. 30 minut",
    "ok 30 min", "ok 30 minut", "około 30 minut",
    # Sformułowania ogólnoczasowe (bez konkretnej daty/godziny)
    "1-2 zdania", "2-3 propozycje terminów", "2-3 propozycje", "2-3 terminy",
    "kilka dni", "kilka tygodni", "wkrótce",
}


def _is_abstract_claim(claim: str) -> bool:
    """True jeśli claim to abstrakcyjne pojęcie (nie konkretny fakt)."""
    if not claim:
        return True
    c = claim.lower().strip().rstrip(".,!?;:")
    if c in _ABSTRACT_NON_CLAIMS:
        return True
    # Krótkie 1-2 słowa bez liczby/URL/daty raczej są abstrakcyjne
    words = c.split()
    if len(words) <= 2 and not re.search(r"\d|http|@|\.pl|\.com", c):
        # ale dopuszczamy nazwy własne (z dużą literą w oryginale)
        if claim.strip()[0].islower():
            return True
    return False


def llm_verify(draft_body: str, mail_body: str, facts_yaml: str,
                thread_history: str = "") -> tuple[dict, dict]:
    """Wywołuje LLM verifier. Zwraca (parsed, meta)."""
    user_prompt = f"""<facts>
{facts_yaml[:6000]}
</facts>

<user_email>
{mail_body[:3000]}
</user_email>

<thread_history>
{thread_history[:2000]}
</thread_history>

<draft_to_verify>
{draft_body}
</draft_to_verify>

Wymień każdy konkret w drafcie i oceń czy ma źródło."""
    parsed, meta = chat_json(
        model=MODEL_MINI,
        system=VERIFIER_SYSTEM,
        user=user_prompt,
        json_schema=VERIFIER_SCHEMA,
        temperature=0.0,
        max_tokens=1500,
    )
    return parsed, meta


# =====================================================================
# Polaczone API
# =====================================================================

def verify_draft(draft_body: str, mail_body: str = "",
                 thread_history: str = "") -> tuple[VerificationResult, dict | None]:
    """Pełna weryfikacja draftu — regex + LLM.

    Zwraca (VerificationResult, meta_z_kosztami_LLM | None).
    """
    result = VerificationResult()

    # Warstwa 1: regex
    deterministic_flags = deterministic_check(draft_body)
    result.flags.extend(deterministic_flags)

    # Warstwa 2: LLM (tylko jeśli draft ma sensowną długość)
    meta = None
    if len(draft_body) > 80:
        facts_yaml = ""
        if FACTS_PATH.exists():
            facts_yaml = FACTS_PATH.read_text(encoding="utf-8")
        try:
            parsed, meta = llm_verify(draft_body, mail_body, facts_yaml, thread_history)
            for c in parsed.get("claims", []):
                claim_text = c.get("claim", "")
                # Filtr post-processing — wycinamy abstrakcyjne pojęcia mimo promptu
                if _is_abstract_claim(claim_text):
                    continue
                risk = c.get("risk", "safe")
                if risk == "hallucination":
                    result.flags.append(Flag(
                        severity="red",
                        category="claim",
                        message=f"AI nie znalazł źródła: {claim_text}",
                        snippet=claim_text,
                    ))
                elif risk == "uncertain":
                    result.flags.append(Flag(
                        severity="yellow",
                        category="claim",
                        message=f"Niepewne: {claim_text}",
                        snippet=claim_text,
                    ))
            if parsed.get("overall_assessment") == "block":
                result.can_send = False
        except Exception as e:
            result.flags.append(Flag(
                severity="yellow",
                category="system",
                message=f"Verifier LLM error: {e}",
            ))

    # Podsumowanie
    if result.has_red:
        result.can_send = False
    if result.has_yellow:
        result.needs_acknowledgement = True

    return result, meta


def flags_to_json(flags: list[Flag]) -> list[dict]:
    return [
        {
            "severity": f.severity,
            "category": f.category,
            "message": f.message,
            "snippet": f.snippet,
        }
        for f in flags
    ]
