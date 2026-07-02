"""Playbooks — wzorce odpowiedzi per kategoria maila.

Plik źródłowy: data/playbooks/playbooks.yaml
Format:
  lead_cena:
    name: "Pytanie o cenę"
    instruction: "Krótko podaj cenę z firma.yaml. Nawiąż do potrzeby z maila. Zaproś do rozmowy."
    must_include: ["cena", "co dalej"]
    must_avoid: ["mglistych obietnic", "zniżek bez uzasadnienia"]
    template: |
      Hej [imię],
      Cena to [X zł]. ...
      Adam
"""
from pathlib import Path
from typing import Any

import yaml

from .config import BASE_DIR


PLAYBOOKS_PATH = BASE_DIR / "data" / "playbooks" / "playbooks.yaml"

_cache: dict[str, Any] | None = None
_cache_mtime: float = 0


def _load() -> dict[str, Any]:
    global _cache, _cache_mtime
    if not PLAYBOOKS_PATH.exists():
        return {}
    mtime = PLAYBOOKS_PATH.stat().st_mtime
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    try:
        data = yaml.safe_load(PLAYBOOKS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    _cache = data
    _cache_mtime = mtime
    return data


def get_playbook(category: str | None) -> dict[str, Any] | None:
    if not category:
        return None
    pb = _load().get(category)
    return pb if isinstance(pb, dict) else None


def playbook_for_prompt(pb: dict | None) -> str:
    """Sformatuj playbook dla wstrzyknięcia do prompta drafera."""
    if not pb:
        return ""
    parts = [f"Nazwa playbooku: {pb.get('name', '?')}"]
    if pb.get("instruction"):
        parts.append(f"Instrukcja: {pb['instruction']}")
    if pb.get("must_include"):
        parts.append(f"MUSI zawierać: {', '.join(pb['must_include'])}")
    if pb.get("must_avoid"):
        parts.append(f"NIE używaj: {', '.join(pb['must_avoid'])}")
    if pb.get("template"):
        parts.append(f"Wzór (luźny — dostosuj do kontekstu, NIE kopiuj 1:1):\n{pb['template']}")
    return "\n".join(parts)


def list_playbooks() -> dict[str, dict]:
    return _load()
