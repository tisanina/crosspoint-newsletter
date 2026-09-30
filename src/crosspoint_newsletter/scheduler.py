"""Background scheduler for periodic automated IMAP polling and EPUB pipeline execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from crosspoint_newsletter import config
from crosspoint_newsletter.ingest.email_client import EmailIngestClient
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.retention import RetentionManager
from crosspoint_newsletter.transform.pipeline import TransformPipeline

logger = logging.getLogger(__name__)


def run_pipeline_cycle(
    db: Database,
    file_store: FileStore | None = None,
    limit: int | None = None,
    mark_seen: bool = True,
    apply_retention: bool = True,
) -> dict[str, Any]:
    """Execute a single synchronous cycle of email ingestion, EPUB conversion, and retention cleanup."""
    fs = file_store or FileStore()
    matcher = NewsletterMatcher(db=db)
    client = EmailIngestClient(matcher=matcher, raw_dir=fs.raw_dir)
    pipeline = TransformPipeline(db=db, epub_dir=fs.epub_dir)

    # 1. Check if IMAP is configured
    if not config.IMAP_HOST or not config.IMAP_USER or not config.IMAP_PASSWORD:
        logger.warning("Polling cycle skipped: IMAP credentials not configured in settings/environment.")
        return {
            "success": False,
            "error": "Credenziali IMAP non configurate",
            "unseen_count": 0,
            "epub_generated": 0,
            "errors": 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # 2. Fetch unseen emails
    logger.info("Starting polling fetch from %s:%s (folder: %s)", config.IMAP_HOST, config.IMAP_PORT, config.IMAP_FOLDER)
    items_to_process = client.fetch_unseen(mark_seen=mark_seen, limit=limit)
    unseen_count = len(items_to_process)
    success_count = 0
    error_count = 0

    # 3. Transform each raw email into EPUB
    for raw in items_to_process:
        try:
            epub_path = pipeline.process_raw_content(raw)
            if epub_path and epub_path.exists():
                success_count += 1
            else:
                error_count += 1
        except Exception as exc:
            logger.error("Error transforming issue '%s': %s", raw.subject, exc)
            error_count += 1

    # 4. Apply retention cleanup
    pruned_count = 0
    if apply_retention:
        try:
            rm = RetentionManager(db=db, file_store=fs)
            ret_res = rm.apply_retention(dry_run=False)
            pruned_count = ret_res.pruned_count
        except Exception as exc:
            logger.warning("Retention execution error during polling cycle: %s", exc)

    logger.info(
        "Polling cycle finished: %d email(s) found, %d EPUB(s) generated, %d error(s), %d issue(s) pruned.",
        unseen_count,
        success_count,
        error_count,
        pruned_count,
    )

    return {
        "success": True,
        "unseen_count": unseen_count,
        "epub_generated": success_count,
        "errors": error_count,
        "retention_pruned": pruned_count,
        "timestamp": datetime.now(UTC).isoformat(),
    }


class PollingScheduler:
    """Async background task scheduler for periodic IMAP polling."""

    def __init__(
        self,
        db: Database,
        file_store: FileStore | None = None,
        interval_minutes: int | None = None,
        enabled: bool = True,
    ) -> None:
        self.db = db
        self.file_store = file_store or FileStore()
        interval = interval_minutes if interval_minutes is not None else config.POLL_INTERVAL_MINUTES
        self.interval_minutes = max(1, interval)
        self.enabled = enabled

        self.is_polling = False
        self.last_poll_at: datetime | None = None
        self.next_poll_at: datetime | None = None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.total_polls_run = 0

        self._running = False
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Start the background polling loop."""
        if self._running or not self.enabled:
            return
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._worker_loop())
        logger.info("PollingScheduler started (interval: %d minutes)", self.interval_minutes)

    async def stop(self) -> None:
        """Gracefully stop the background polling task."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("PollingScheduler stopped.")

    async def _worker_loop(self) -> None:
        """Internal asynchronous loop running poll cycles periodically."""
        while self._running:
            self.next_poll_at = datetime.now(UTC) + timedelta(minutes=self.interval_minutes)
            try:
                # Wait for interval or until stopped
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_minutes * 60,
                )
                # If stop_event was set, exit loop
                break
            except TimeoutError:
                # Interval elapsed -> trigger poll
                pass

            if not self._running:
                break

            await self.trigger_now()

    async def trigger_now(self) -> dict[str, Any]:
        """Manually or automatically trigger an immediate polling cycle."""
        if self.is_polling:
            return {
                "status": "already_running",
                "message": "Un ciclo di controllo posta è già in corso.",
                "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
            }

        self.is_polling = True
        self.last_error = None
        start_time = datetime.now(UTC)
        logger.info("PollingScheduler: executing poll cycle...")

        try:
            # Run blocking I/O pipeline in thread pool
            result = await asyncio.to_thread(
                run_pipeline_cycle,
                self.db,
                self.file_store,
            )
            self.last_result = result
            self.last_poll_at = start_time
            self.total_polls_run += 1
            self.next_poll_at = datetime.now(UTC) + timedelta(minutes=self.interval_minutes)
            return {
                "status": "success",
                "result": result,
                "polled_at": self.last_poll_at.isoformat(),
                "next_poll_at": self.next_poll_at.isoformat() if self.next_poll_at else None,
            }
        except Exception as exc:
            self.last_error = str(exc)
            logger.error("PollingScheduler cycle failed with exception: %s", exc)
            return {
                "status": "error",
                "error": str(exc),
                "polled_at": start_time.isoformat(),
            }
        finally:
            self.is_polling = False

    def get_status(self) -> dict[str, Any]:
        """Return runtime status of the scheduler."""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "is_polling": self.is_polling,
            "interval_minutes": self.interval_minutes,
            "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
            "next_poll_at": self.next_poll_at.isoformat() if self.next_poll_at else None,
            "total_polls_run": self.total_polls_run,
            "last_result": self.last_result,
            "last_error": self.last_error,
        }
