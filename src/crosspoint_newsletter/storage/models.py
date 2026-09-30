"""Data models for SQLite storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Newsletter:
    id: str
    name: str
    slug: str
    sender_email: str
    sender_rules: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    retention_max_issues: int | None = None
    retention_max_age_days: int | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass
class Issue:
    id: str
    newsletter_id: str
    email_message_id: str
    subject: str
    received_at: datetime
    series_index: int
    processed_at: datetime | None = None
    status: str = "pending"  # pending, processing, done, error
    epub_path: str | None = None
    raw_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    calibre_synced: bool = False
    error_detail: str | None = None


@dataclass
class UnmatchedEmail:
    id: str
    email_message_id: str
    sender_header: str
    sender_email: str
    sender_name: str
    subject: str
    received_at: datetime
    raw_eml_path: str | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class BlacklistEntry:
    id: str
    sender_email: str
    sender_name: str | None = None
    reason: str | None = None
    created_at: datetime = field(default_factory=_utcnow)

