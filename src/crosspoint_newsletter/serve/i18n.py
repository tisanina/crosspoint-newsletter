"""Internationalization (i18n) module for CrossPoint Newsletter WebUI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import Request

from crosspoint_newsletter import config

logger = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

SUPPORTED_LANGUAGES: dict[str, str] = {
    "it": "Italiano",
    "en": "English",
}

DEFAULT_LANGUAGE = "it"

_translations: dict[str, dict[str, str]] = {}


def _flatten_dict(d: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten nested dictionary into dot-separated keys."""
    flat: dict[str, str] = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_dict(v, full_key))
        else:
            flat[full_key] = str(v)
    return flat


def load_translations() -> None:
    """Load all JSON translation files from the locales directory."""
    global _translations
    _translations.clear()
    if not LOCALES_DIR.exists():
        return

    for file in LOCALES_DIR.glob("*.json"):
        lang_code = file.stem.lower()
        try:
            raw_data = json.loads(file.read_text(encoding="utf-8"))
            if isinstance(raw_data, dict):
                _translations[lang_code] = _flatten_dict(raw_data)
        except Exception as exc:
            logger.error(f"Failed to load translations from {file}: {exc}")


def get_active_language(request: Request | None = None) -> str:
    """Determine the active language from query, cookie, header, or config."""
    # 1. Priority: Explicit query parameter ?lang=...
    if request:
        query_lang = request.query_params.get("lang")
        if query_lang and query_lang.strip().lower() in SUPPORTED_LANGUAGES:
            return query_lang.strip().lower()

        # 2. Priority: Browser cookie
        cookie_lang = request.cookies.get("cn_lang")
        if cookie_lang and cookie_lang.strip().lower() in SUPPORTED_LANGUAGES:
            return cookie_lang.strip().lower()

    # 3. Priority: Server-level configuration (.env)
    conf_lang = getattr(config, "LANGUAGE", DEFAULT_LANGUAGE).strip().lower()
    if conf_lang in SUPPORTED_LANGUAGES:
        return conf_lang

    # 4. Priority: HTTP Accept-Language header
    if request:
        accept_header = request.headers.get("accept-language", "")
        for item in accept_header.split(","):
            code = item.split(";")[0].strip().split("-")[0].lower()
            if code in SUPPORTED_LANGUAGES:
                return code

    return DEFAULT_LANGUAGE


def translate(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate a key into target language with fallback to default language."""
    if not _translations:
        load_translations()

    target_lang = (lang or DEFAULT_LANGUAGE).strip().lower()
    catalog = _translations.get(target_lang, {})
    text = catalog.get(key)

    # Fallback to DEFAULT_LANGUAGE if key not in target language
    if text is None and target_lang != DEFAULT_LANGUAGE:
        text = _translations.get(DEFAULT_LANGUAGE, {}).get(key)

    # Fallback to key itself
    if text is None:
        text = key

    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


t = translate
