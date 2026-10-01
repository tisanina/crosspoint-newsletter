"""Centralized application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present (development only — never commit .env)
load_dotenv()


# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Cache volume (SSD / Appdata): DB SQLite, credenziali, file di configurazione
CONF_DIR = Path(os.getenv("CN_CONF_DIR", str(BASE_DIR / "conf" if (BASE_DIR / "conf").exists() else BASE_DIR / "data")))
DB_PATH = Path(os.getenv("CN_DB_PATH", str(CONF_DIR / "newsletter.db" if (CONF_DIR / "newsletter.db").exists() else CONF_DIR / "db" / "newsletter.db")))

# RAID volume (Array): EPUB, raw email (.eml), copertine (covers)
DATA_DIR = Path(os.getenv("CN_DATA_DIR", str(BASE_DIR / "data")))
EPUB_DIR = Path(os.getenv("CN_EPUB_DIR", str(DATA_DIR / "epub")))
RAW_DIR = Path(os.getenv("CN_RAW_DIR", str(DATA_DIR / "raw")))
COVERS_DIR = Path(os.getenv("CN_COVERS_DIR", str(DATA_DIR / "covers")))

# --- Storage settings ---
CN_KEEP_RAW = os.getenv("CN_KEEP_RAW", "true").lower() in ("true", "1", "yes")

# --- Timezone & Localization ---
TIMEZONE = os.getenv("CN_TIMEZONE", os.getenv("TZ", "Europe/Rome"))
LANGUAGE = os.getenv("CN_LANGUAGE", "it").strip().lower()

# --- Polling & Automation ---
POLL_INTERVAL_MINUTES = int(os.getenv("CN_POLL_INTERVAL_MINUTES", "15"))
ENABLE_AUTO_POLL = os.getenv("CN_ENABLE_AUTO_POLL", "true").lower() in ("true", "1", "yes")

# --- IMAP (email ingestion) ---
IMAP_HOST = os.getenv("CN_IMAP_HOST", "")
IMAP_PORT = int(os.getenv("CN_IMAP_PORT", "993"))
IMAP_USER = os.getenv("CN_IMAP_USER", "")
IMAP_PASSWORD = os.getenv("CN_IMAP_PASSWORD", "")
IMAP_FOLDER = os.getenv("CN_IMAP_FOLDER", "INBOX")

# --- OPDS server ---
OPDS_HOST = os.getenv("CN_OPDS_HOST", "0.0.0.0")
OPDS_PORT = int(os.getenv("CN_OPDS_PORT", "8400"))
OPDS_USERNAME = os.getenv("CN_OPDS_USERNAME", "")
OPDS_PASSWORD = os.getenv("CN_OPDS_PASSWORD", "")

# --- Newsletter config ---
NEWSLETTERS_CONFIG = os.getenv(
    "CN_NEWSLETTERS_CONFIG", str(BASE_DIR / "config" / "newsletters.yaml")
)


def update_env_file(key: str, value: str) -> None:
    """Save or update a key-value pair in .env file and runtime os.environ."""
    import dotenv

    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        example = BASE_DIR / ".env.example"
        if example.exists():
            env_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            env_file.touch()

    dotenv.set_key(str(env_file), key, value)
    os.environ[key] = value


def update_imap_config(
    host: str,
    port: int,
    user: str,
    password: str | None = None,
    folder: str = "INBOX",
) -> None:
    """Persist IMAP credentials to .env and refresh runtime configuration."""
    global IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER
    h = host.replace("\xa0", "").strip()
    p = int(port)
    u = user.replace("\xa0", "").strip()
    f = folder.replace("\xa0", "").strip() or "INBOX"

    update_env_file("CN_IMAP_HOST", h)
    IMAP_HOST = h

    update_env_file("CN_IMAP_PORT", str(p))
    IMAP_PORT = p

    update_env_file("CN_IMAP_USER", u)
    IMAP_USER = u

    if password is not None and password.strip() != "":
        pw = password.replace("\xa0", " ").strip()
        if len(pw.replace(" ", "")) == 16:
            pw = pw.replace(" ", "")
        update_env_file("CN_IMAP_PASSWORD", pw)
        IMAP_PASSWORD = pw

    update_env_file("CN_IMAP_FOLDER", f)
    IMAP_FOLDER = f


def update_timezone(tz_name: str) -> None:
    """Persist timezone to .env and refresh runtime configuration."""
    global TIMEZONE
    clean_tz = tz_name.strip()
    from zoneinfo import ZoneInfo

    ZoneInfo(clean_tz)
    update_env_file("CN_TIMEZONE", clean_tz)
    update_env_file("TZ", clean_tz)
    TIMEZONE = clean_tz


def update_language(lang_code: str) -> None:
    """Persist default UI language to .env and refresh runtime configuration."""
    global LANGUAGE
    clean_lang = lang_code.strip().lower()
    if clean_lang not in ("it", "en"):
        raise ValueError(f"Unsupported language: {lang_code}")
    update_env_file("CN_LANGUAGE", clean_lang)
    LANGUAGE = clean_lang


