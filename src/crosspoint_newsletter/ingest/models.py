"""Data contract models for the ingestion phase."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class RawContent:
    """Standard raw content format delivered by any ingestion source (IMAP, future RSS)."""
    newsletter_name: str
    newsletter_slug: str
    subject: str
    sender: str
    message_id: str
    received_at: datetime
    html_body: str | None = None
    text_body: str | None = None
    inline_images: dict[str, bytes] = field(default_factory=dict)
    raw_eml_path: Path | None = None
    issue_id: str | None = None
    series_index: int = 1
