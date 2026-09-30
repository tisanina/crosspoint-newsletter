"""FastAPI application providing standalone OPDS 1.2 catalog and download service."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from crosspoint_newsletter import config
from crosspoint_newsletter.scheduler import PollingScheduler
from crosspoint_newsletter.serve.api import api_router
from crosspoint_newsletter.serve.opds_builder import (
    EPUB_MIME,
    IMAGE_PNG_MIME,
    OPDS_CATALOG_MIME,
    OpdsFeedBuilder,
)
from crosspoint_newsletter.serve.webui import ui_router
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.transform.cover_generator import CoverGenerator

logger = logging.getLogger(__name__)

security = HTTPBasic(auto_error=False)


def create_app(
    db: Database | None = None,
    file_store: FileStore | None = None,
    auth_username: str | None = None,
    auth_password: str | None = None,
    enable_scheduler: bool | None = None,
) -> FastAPI:
    """Application factory for the CrossPoint OPDS server."""
    # State dependencies
    app_db = db or Database(config.DB_PATH)
    app_fs = file_store or FileStore()
    app_builder = OpdsFeedBuilder(base_title="CrossPoint Newsletters")
    cover_gen = CoverGenerator()

    should_enable_scheduler = (
        enable_scheduler if enable_scheduler is not None else config.ENABLE_AUTO_POLL
    )

    scheduler = PollingScheduler(
        db=app_db,
        file_store=app_fs,
        interval_minutes=config.POLL_INTERVAL_MINUTES,
        enabled=should_enable_scheduler,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.scheduler = scheduler
        if should_enable_scheduler:
            scheduler.start()
        yield
        if hasattr(app.state, "scheduler") and app.state.scheduler:
            await app.state.scheduler.stop()

    app = FastAPI(
        title="CrossPoint Newsletter OPDS Server",
        description="OPDS 1.2 catalog and acquisition service for CrossPoint e-ink reader",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.db = app_db
    app.state.fs = app_fs
    app.state.scheduler = scheduler
    app.state.start_time = datetime.now(UTC)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Determine auth configuration
    expected_user = auth_username if auth_username is not None else config.OPDS_USERNAME
    expected_pass = auth_password if auth_password is not None else config.OPDS_PASSWORD

    def verify_auth(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    ) -> bool:
        """Enforce HTTP Basic Auth if credentials are configured."""
        if not expected_user or not expected_pass:
            # Auth is disabled / unconfigured
            return True

        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": 'Basic realm="CrossPoint OPDS"'},
            )

        is_user_correct = secrets.compare_digest(
            credentials.username.encode("utf-8"), expected_user.encode("utf-8")
        )
        is_pass_correct = secrets.compare_digest(
            credentials.password.encode("utf-8"), expected_pass.encode("utf-8")
        )

        if not (is_user_correct and is_pass_correct):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": 'Basic realm="CrossPoint OPDS"'},
            )
        return True

    @app.get("/", include_in_schema=False)
    def root_redirect(request: Request) -> RedirectResponse:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(url="/ui/")
        return RedirectResponse(url="/opds")

    @app.get("/api/status", tags=["System"])
    def get_status() -> dict:
        """Unauthenticated healthcheck and system overview."""
        from crosspoint_newsletter import __version__

        db_stats = app_db.get_stats()
        disk_stats = app_fs.get_disk_usage()

        scheduler_status = (
            getattr(app.state, "scheduler", None).get_status()
            if hasattr(app.state, "scheduler") and app.state.scheduler
            else {"enabled": False, "running": False, "is_polling": False}
        )

        uptime_seconds = 0
        if hasattr(app.state, "start_time") and app.state.start_time:
            uptime_seconds = int((datetime.now(UTC) - app.state.start_time).total_seconds())

        return {
            "status": "healthy",
            "service": "crosspoint-newsletter",
            "version": __version__,
            "uptime_seconds": uptime_seconds,
            "newsletters_count": db_stats["newsletters_count"],
            "total_issues": db_stats["total_issues"],
            "done_issues": db_stats["done_issues"],
            "error_issues": db_stats["error_issues"],
            "storage_files": disk_stats["total_files"],
            "storage_bytes": disk_stats["total_bytes"],
            "storage_mb": round(disk_stats["total_bytes"] / (1024 * 1024), 2),
            "volumes": {
                "conf": str(config.CONF_DIR),
                "data": str(config.DATA_DIR),
                "db_path": str(config.DB_PATH),
                "epub_dir": str(app_fs.epub_dir),
                "raw_dir": str(app_fs.raw_dir),
                "covers_dir": str(app_fs.covers_dir),
            },
            "polling": scheduler_status,
            "imap": {
                "configured": bool(config.IMAP_HOST and config.IMAP_USER and config.IMAP_PASSWORD),
                "host": config.IMAP_HOST,
                "port": config.IMAP_PORT,
                "folder": config.IMAP_FOLDER,
            },
        }

    @app.get("/opds", response_class=Response, tags=["OPDS"])
    def get_root_catalog(authenticated: bool = Depends(verify_auth)) -> Response:
        """Root OPDS 1.2 catalog listing newsletter series and recent issues."""
        db_stats = app_db.get_stats()
        recent_issues = app_db.get_recent_done_issues(limit=1)
        recent_count = len(recent_issues)

        xml_content = app_builder.build_root_catalog(
            newsletters=db_stats["newsletters"],
            recent_count=recent_count,
        )
        return Response(content=xml_content, media_type=OPDS_CATALOG_MIME)

    @app.get("/opds/recent", response_class=Response, tags=["OPDS"])
    def get_recent_feed(
        limit: int = Query(default=30, ge=1, le=100),
        authenticated: bool = Depends(verify_auth),
    ) -> Response:
        """Acquisition feed for the most recent issues across all newsletters."""
        recent_issues = app_db.get_recent_done_issues(limit=limit)

        # Cache newsletter names
        nl_cache: dict[str, str] = {}
        issues_with_nl: list[tuple] = []
        for issue in recent_issues:
            if issue.newsletter_id not in nl_cache:
                nl = app_db.get_newsletter_by_id(issue.newsletter_id)
                nl_cache[issue.newsletter_id] = nl.name if nl else "Newsletter"
            issues_with_nl.append((issue, nl_cache[issue.newsletter_id]))

        xml_content = app_builder.build_recent_feed(
            issues=issues_with_nl,
            feed_url="/opds/recent",
        )
        return Response(content=xml_content, media_type=OPDS_CATALOG_MIME)

    @app.get("/opds/newsletter/{slug}", response_class=Response, tags=["OPDS"])
    def get_series_feed(
        slug: str,
        limit: int = Query(default=50, ge=1, le=200),
        authenticated: bool = Depends(verify_auth),
    ) -> Response:
        """Acquisition feed for a specific newsletter series."""
        nl = app_db.get_newsletter_by_slug(slug)
        if not nl:
            raise HTTPException(status_code=404, detail=f"Newsletter '{slug}' not found")

        issues = app_db.get_issues_for_feed(newsletter_id=nl.id, limit=limit, descending=True)
        xml_content = app_builder.build_series_feed(
            newsletter=nl,
            issues=issues,
            feed_url=f"/opds/newsletter/{slug}",
        )
        return Response(content=xml_content, media_type=OPDS_CATALOG_MIME)

    @app.get("/opds/download/{issue_id}", tags=["OPDS"])
    def download_issue(
        issue_id: str,
        authenticated: bool = Depends(verify_auth),
    ) -> FileResponse:
        """Download EPUB file for a specific issue."""
        issue = app_db.get_issue(issue_id)
        if not issue or issue.status != "done":
            raise HTTPException(status_code=404, detail="Issue not found or not ready for download")

        file_path = app_fs.resolve_path(issue.epub_path)
        if not file_path or not file_path.is_file():
            logger.error("EPUB file missing on disk for issue %s: %s", issue_id, issue.epub_path)
            raise HTTPException(status_code=404, detail="EPUB file not found on disk")

        return FileResponse(
            path=file_path,
            media_type=EPUB_MIME,
            filename=file_path.name,
        )

    @app.get("/opds/cover/{issue_id}", tags=["OPDS"])
    def get_cover(issue_id: str) -> Response:
        """Serve cover PNG image for an issue (or dynamically generate on the fly)."""

        issue = app_db.get_issue(issue_id)
        if not issue:
            raise HTTPException(status_code=404, detail="Issue not found")

        nl = app_db.get_newsletter_by_id(issue.newsletter_id)
        nl_name = nl.name if nl else "Newsletter"
        nl_slug = nl.slug if nl else "newsletter"

        # 1. Check if cover exists on disk
        cover_filename = f"{nl_slug}-{issue.series_index:03d}.png"
        cand_path = app_fs.covers_dir / nl_slug / cover_filename
        if cand_path.is_file():
            return FileResponse(path=cand_path, media_type=IMAGE_PNG_MIME)

        # 2. Check issue metadata for custom cover_path
        if issue.metadata and "cover_path" in issue.metadata:
            meta_path = app_fs.resolve_path(issue.metadata["cover_path"])
            if meta_path and meta_path.is_file():
                return FileResponse(path=meta_path, media_type=IMAGE_PNG_MIME)

        # 3. Generate dynamically on the fly
        cover_bytes = cover_gen.generate_cover_image(
            newsletter_name=nl_name,
            title=issue.subject,
            series_index=issue.series_index,
            date=issue.received_at,
        )
        return Response(content=cover_bytes, media_type=IMAGE_PNG_MIME)

    @app.get("/opds/search", response_class=Response, tags=["OPDS"])
    def search_feed(
        q: str = Query(..., min_length=1),
        authenticated: bool = Depends(verify_auth),
    ) -> Response:
        """Search issues by keyword and return acquisition feed."""
        matching_issues = app_db.search_issues(query=q, limit=30)
        # Filter done only
        done_matches = [i for i in matching_issues if i.status == "done"]

        nl_cache: dict[str, str] = {}
        issues_with_nl: list[tuple] = []
        for issue in done_matches:
            if issue.newsletter_id not in nl_cache:
                nl = app_db.get_newsletter_by_id(issue.newsletter_id)
                nl_cache[issue.newsletter_id] = nl.name if nl else "Newsletter"
            issues_with_nl.append((issue, nl_cache[issue.newsletter_id]))

        xml_content = app_builder.build_recent_feed(
            issues=issues_with_nl,
            feed_url=f"/opds/search?q={q}",
        )
        return Response(content=xml_content, media_type=OPDS_CATALOG_MIME)

    app.include_router(api_router)
    app.include_router(ui_router)

    return app


# Default ASGI application instance for uvicorn
app = create_app()
