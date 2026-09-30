"""Transformation pipeline orchestrator converting RawContent to EPUB and updating DB state."""

from __future__ import annotations

import logging
from pathlib import Path

from crosspoint_newsletter import config
from crosspoint_newsletter.ingest.models import RawContent
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.transform.epub_builder import EpubBuilder
from crosspoint_newsletter.transform.quality_checker import QualityChecker

logger = logging.getLogger(__name__)


class TransformPipeline:
    def __init__(
        self,
        db: Database,
        epub_dir: Path | None = None,
        builder: EpubBuilder | None = None,
        quality_checker: QualityChecker | None = None,
    ) -> None:
        self.db = db
        self.epub_dir = epub_dir or config.EPUB_DIR
        self.builder = builder or EpubBuilder()
        self.quality_checker = quality_checker or QualityChecker()

    def process_raw_content(self, raw: RawContent) -> Path | None:
        """Transform raw newsletter content into EPUB, run quality check and update issue status."""
        dest_filename = f"{raw.newsletter_slug}-{raw.series_index:03d}.epub"
        dest_path = self.epub_dir / raw.newsletter_slug / dest_filename

        try:
            # Set issue status to processing if issue_id is present
            if raw.issue_id:
                self.db.update_issue_status(raw.issue_id, status="processing")

            created_epub = self.builder.build_epub(
                newsletter_name=raw.newsletter_name,
                newsletter_slug=raw.newsletter_slug,
                series_index=raw.series_index,
                subject=raw.subject,
                message_id=raw.message_id,
                html_body=raw.html_body,
                text_body=raw.text_body,
                output_path=dest_path,
                inline_images=raw.inline_images,
                date=raw.received_at,
            )

            # Quality Gate: check EPUB validity, minimum text length, and integrity
            report = self.quality_checker.check_epub(created_epub)
            if not report.is_valid:
                err_msg = f"Quality check failed: {'; '.join(report.errors)}"
                logger.error("EPUB quality check rejected %s: %s", dest_path, err_msg)
                if raw.issue_id:
                    self.db.update_issue_status(
                        issue_id=raw.issue_id,
                        status="error",
                        error_detail=err_msg,
                        metadata={"quality_report": report.to_dict()},
                    )
                return None

            if report.warnings:
                logger.warning("EPUB quality warnings for %s: %s", dest_path, report.warnings)

            # Update issue status to done
            if raw.issue_id:
                if dest_path.is_relative_to(config.BASE_DIR):
                    rel_path = str(dest_path.relative_to(config.BASE_DIR))
                else:
                    rel_path = str(dest_path)

                # Fetch existing metadata to merge
                issue = self.db.get_issue(raw.issue_id)
                meta = dict(issue.metadata) if issue else {}
                meta["quality_report"] = report.to_dict()

                self.db.update_issue_status(
                    issue_id=raw.issue_id,
                    status="done",
                    epub_path=rel_path,
                    metadata=meta,
                )

            logger.info("Successfully converted issue to EPUB (quality score: %.2f): %s", report.score, dest_path)
            return created_epub
        except Exception as exc:
            logger.error("Failed to transform issue '%s': %s", raw.subject, exc, exc_info=True)
            if raw.issue_id:
                self.db.update_issue_status(
                    issue_id=raw.issue_id,
                    status="error",
                    error_detail=str(exc),
                )
            return None
