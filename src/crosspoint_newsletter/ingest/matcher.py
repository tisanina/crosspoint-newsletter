"""Newsletter matching and issue deduplication against storage."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.models import Issue, Newsletter

logger = logging.getLogger(__name__)


def extract_email_address(raw_from: str) -> str:
    """Extract clean email address from a RFC From header string."""
    if not raw_from:
        return ""
    match = re.search(r"<([^>]+)>", raw_from)
    if match:
        return match.group(1).strip().lower()
    return raw_from.strip().lower()


class NewsletterMatcher:
    def __init__(self, db: Database, config_path: Path | str | None = None) -> None:
        self.db = db
        if config_path and Path(config_path).exists():
            self.sync_config_to_db(config_path)

    def sync_config_to_db(self, config_path: Path | str) -> list[Newsletter]:
        path = Path(config_path)
        if not path.exists():
            logger.warning("Configuration file %s does not exist", path)
            return []

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        items = data.get("newsletters", [])
        synced: list[Newsletter] = []

        for item in items:
            name = item.get("name", "").strip()
            sender = item.get("sender", "").strip()
            if not name or not sender:
                continue

            slug = item.get("slug")
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

            rules = item.get("sender_rules", [])
            enabled = bool(item.get("enabled", True))

            retention = item.get("retention") or {}
            max_issues = retention.get("max_issues")
            max_age_days = retention.get("max_age_days")

            now = datetime.now(UTC)
            nl = Newsletter(
                id=str(uuid.uuid4()),
                name=name,
                slug=slug,
                sender_email=sender,
                sender_rules=rules,
                enabled=enabled,
                retention_max_issues=int(max_issues) if max_issues is not None else None,
                retention_max_age_days=int(max_age_days) if max_age_days is not None else None,
                created_at=now,
                updated_at=now,
            )
            self.db.upsert_newsletter(nl)
            synced.append(nl)

        logger.info("Synchronized %d newsletters from %s to DB", len(synced), path)
        return synced

    def match_newsletter(
        self,
        sender_header: str,
        subject: str = "",
        headers: dict[str, str] | None = None,
    ) -> Newsletter | None:
        clean_sender = extract_email_address(sender_header)
        all_newsletters = self.db.get_all_newsletters()
        headers = headers or {}

        # 1. Exact sender_email match
        for nl in all_newsletters:
            if not nl.enabled:
                continue
            if nl.sender_email.strip().lower() == clean_sender:
                return nl

        # 2. Rule-based match (sender_rules)
        for nl in all_newsletters:
            if not nl.enabled or not nl.sender_rules:
                continue
            for rule in nl.sender_rules:
                rule_type = rule.get("type")
                rule_value = str(rule.get("value", "")).lower()
                if not rule_type or not rule_value:
                    continue

                if rule_type == "header_from_contains":
                    if rule_value in sender_header.lower():
                        return nl
                elif rule_type == "subject_contains":
                    if rule_value in subject.lower():
                        return nl
                elif rule_type == "header_contains":
                    header_key = str(rule.get("header", "")).lower()
                    header_val = headers.get(header_key, "").lower()
                    if rule_value in header_val:
                        return nl

        return None

    def match_and_create_issue(
        self,
        message_id: str,
        sender_header: str,
        subject: str,
        received_at: datetime,
        headers: dict[str, str] | None = None,
        raw_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Newsletter, Issue] | None:
        clean_mid = message_id.strip()
        if not clean_mid:
            logger.warning("Email without Message-ID skipped")
            return None

        # Deduplication check
        if self.db.issue_exists(clean_mid):
            logger.info("Issue with Message-ID '%s' already exists (deduplicated)", clean_mid)
            return None

        newsletter = self.match_newsletter(sender_header, subject, headers)
        if not newsletter:
            logger.info(
                "No active newsletter matching sender='%s', subject='%s' (skipped)",
                sender_header,
                subject,
            )
            return None

        series_idx = self.db.get_next_series_index(newsletter.id)
        issue = Issue(
            id=str(uuid.uuid4()),
            newsletter_id=newsletter.id,
            email_message_id=clean_mid,
            subject=subject or f"{newsletter.name} #{series_idx}",
            received_at=received_at,
            series_index=series_idx,
            status="pending",
            raw_path=raw_path,
            metadata=metadata or {},
        )
        created = self.db.create_issue(issue)
        logger.info(
            "Created Issue '%s' (series_index=%d) for Newsletter '%s'",
            created.subject,
            created.series_index,
            newsletter.name,
        )
        return newsletter, created
