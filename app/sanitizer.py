"""Sanitizer treści maila — usuwa ukryty tekst, normalizuje, wykrywa injection.

Warstwa 1 obrony anti-injection.
"""
from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


SUSPICIOUS_PATTERNS = [
    (r"(?i)\bignore\s+(previous|above|all|prior)\s+instructions?", "ignore_instructions"),
    (r"(?i)\byou\s+are\s+(now|actually)\s+a\b", "role_override"),
    (r"(?i)\[?system\s*[:\]]", "system_marker"),
    (r"(?i)\bdisregard\s+(previous|above|prior|all)", "disregard"),
    (r"(?i)\bforget\s+(everything|above|previous|all)", "forget_everything"),
    (r"(?i)show\s+(me\s+)?your\s+(system\s+)?prompt", "show_prompt"),
    (r"(?i)reveal\s+(your\s+)?(system\s+)?(prompt|instructions?)", "reveal_prompt"),
    (r"(?i)act\s+as\s+(a\s+)?(different|new)", "act_as"),
    (r"(?i)pretend\s+(you\s+are|to\s+be)", "pretend"),
]


class SanitizeResult(NamedTuple):
    clean_text: str
    suspicious_matches: list[str]
    had_hidden_content: bool
    original_length: int
    clean_length: int


def _strip_hidden_html(html: str) -> tuple[str, bool]:
    """Wycina ukryty tekst (CSS display:none, biały na białym, font 1px, opacity:0)."""
    if not HAS_BS4 or not html:
        return html or "", False
    soup = BeautifulSoup(html, "lxml")
    had_hidden = False
    hidden_patterns = [
        r"display\s*:\s*none",
        r"visibility\s*:\s*hidden",
        r"color\s*:\s*white\b",
        r"color\s*:\s*#fff(?:fff)?\b",
        r"font-size\s*:\s*(?:0|1)(?:px)?\b",
        r"opacity\s*:\s*0(?:\.0+)?\b",
    ]
    for el in soup.find_all(style=True):
        try:
            style = el.attrs.get("style", "") if el.attrs else ""
        except AttributeError:
            continue
        if any(re.search(p, style, re.I) for p in hidden_patterns):
            el.decompose()
            had_hidden = True
    for cls in ["hidden", "invisible", "sr-only"]:
        try:
            for el in soup.find_all(class_=cls):
                el.decompose()
                had_hidden = True
        except Exception:
            pass
    text = soup.get_text(separator=" ")
    return text, had_hidden


def sanitize_mail(body_text: str | None, body_html: str | None) -> SanitizeResult:
    """Przygotowuje mail do analizy AI.

    1. Jeśli jest HTML, parsuje go i usuwa ukryty tekst
    2. Wybiera plain text (jeśli mamy, ufamy bardziej niż HTML)
    3. Usuwa zero-width chars, normalizuje whitespace
    4. Wykrywa suspicious patterns (NIE usuwa — flaguje)
    """
    original_text = (body_text or body_html or "").strip()
    original_len = len(original_text)

    had_hidden = False
    if body_text:
        text = body_text
    elif body_html:
        text, had_hidden = _strip_hidden_html(body_html)
    else:
        text = ""

    # Zero-width characters (ZWS, ZWJ, BOM)
    text = re.sub(r"[​-‍﻿⁠]", "", text)
    # Unicode normalize (kompozycja)
    text = unicodedata.normalize("NFKC", text)
    # Normalize whitespace (max 2 podwójne nowe linie pod rząd)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = text.strip()

    # Wykryj podejrzane wzorce
    matches = []
    for pattern, label in SUSPICIOUS_PATTERNS:
        if re.search(pattern, text):
            matches.append(label)

    return SanitizeResult(
        clean_text=text,
        suspicious_matches=matches,
        had_hidden_content=had_hidden,
        original_length=original_len,
        clean_length=len(text),
    )
