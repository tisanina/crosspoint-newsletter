"""Retention policy enforcement module for newsletter issues and disk storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Issue, Newsletter

if TYPE_CHECKING:
    from crosspoint_newsletter.storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class PrunedIssueInfo:
    id: str
    newsletter_slug: str
    series_index: int
    subject: str
    epub_path: str | None
    size_bytes: int
    reason: str


@dataclass
class RetentionResult:
    pruned_count: int = 0
    bytes_reclaimed: int = 0
    pruned_issues: list[PrunedIssueInfo] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "pruned_count": self.pruned_count,
            "bytes_reclaimed": self.bytes_reclaimed,
            "mb_reclaimed": round(self.bytes_reclaimed / (1024 * 1024), 2),
            "dry_run": self.dry_run,
            "issues": [
                {
                    "id": p.id,
                    "slug": p.newsletter_slug,
                    "series_index": p.series_index,
                    "subject": p.subject,
                    "size_bytes": p.size_bytes,
                    "reason": p.reason,
                }
                for p in self.pruned_issues
            ],
        }


class RetentionManager:
    """Applies count-based and age-based retention rules across database and disk."""

    def __init__(self, db: Database, file_store: FileStore | None = None) -> None:
        self.db = db
        self.file_store = file_store or FileStore()

    def apply_retention(
        self,
        newsletter_id: str | None = None,
        dry_run: bool = False,
    ) -> RetentionResult:
        """Evaluate retention criteria and remove expired/excess issues."""
        result = RetentionResult(dry_run=dry_run)

        newsletters: list[Newsletter] = []
        if newsletter_id:
            nl = self.db.get_newsletter_by_id(newsletter_id)
            if not nl:
                # Try by slug
                nl = self.db.get_newsletter_by_slug(newsletter_id)
            if nl:
                newsletters.append(nl)
        else:
            newsletters = self.db.get_all_newsletters()

        now = datetime.now(UTC)

        for nl in newsletters:
            candidates: dict[str, tuple[Issue, str]] = {}

            # 1. Count-based retention (keep newest N issues)
            if nl.retention_max_issues and nl.retention_max_issues > 0:
                excess_issues = self.db.get_issues_exceeding_count(
                    newsletter_id=nl.id,
                    keep_count=nl.retention_max_issues,
                )
                for issue in excess_issues:
                    reason = f"Exceeded max count ({nl.retention_max_issues})"
                    candidates[issue.id] = (issue, reason)

            # 2. Age-based retention (remove issues older than N days)
            if nl.retention_max_age_days and nl.retention_max_age_days > 0:
                cutoff = now - timedelta(days=nl.retention_max_age_days)
                old_issues = self.db.get_issues_older_than(
                    newsletter_id=nl.id,
                    cutoff_date=cutoff,
                )
                for issue in old_issues:
                    reason = f"Exceeded max age ({nl.retention_max_age_days} days)"
                    if issue.id not in candidates:
                        candidates[issue.id] = (issue, reason)

            # Process candidates for this newsletter
            for issue_id, (issue, reason) in candidates.items():
                size = 0
                if issue.epub_path:
                    try:
                        resolved = self.file_store.base_dir / issue.epub_path
                        if not resolved.exists():
                            from crosspoint_newsletter import config
                            resolved = config.BASE_DIR / issue.epub_path
                        if resolved.exists() and resolved.is_file():
                            size = resolved.stat().st_size
                    except Exception:
                        size = 0

                pruned_info = PrunedIssueInfo(
                    id=issue.id,
                    newsletter_slug=nl.slug,
                    series_index=issue.series_index,
                    subject=issue.subject,
                    epub_path=issue.epub_path,
                    size_bytes=size,
                    reason=reason,
                )

                if not dry_run:
                    # Remove physical file
                    if issue.epub_path:
                        self.file_store.delete_epub(issue.epub_path)
                    if issue.raw_path:
                        self.file_store.delete_raw(issue.raw_path)
                    # Remove DB record
                    self.db.delete_issue(issue.id)
                    logger.info("Retention pruned issue %s (%s): %s", issue.id, nl.slug, reason)

                result.pruned_count += 1
                result.bytes_reclaimed += size
                result.pruned_issues.append(pruned_info)

        return result
