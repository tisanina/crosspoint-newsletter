"""REST API router providing JSON endpoints for newsletters, issues, and system management."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from crosspoint_newsletter import config
from crosspoint_newsletter.ingest.email_client import EmailIngestClient
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Newsletter
from crosspoint_newsletter.transform.pipeline import TransformPipeline

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api", tags=["API"])


# Pydantic Schemas
class NewsletterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    sender_email: str = Field(..., min_length=3, max_length=150)
    slug: str | None = None
    retention_max_issues: int | None = Field(default=None, ge=1)
    retention_max_age_days: int | None = Field(default=None, ge=1)
    enabled: bool = True
    sender_rules: list[dict[str, Any]] = Field(default_factory=list)


class NewsletterUpdate(BaseModel):
    name: str | None = None
    sender_email: str | None = None
    retention_max_issues: int | None = None
    retention_max_age_days: int | None = None
    enabled: bool | None = None
    sender_rules: list[dict[str, Any]] | None = None


class ImapSettings(BaseModel):
    host: str = Field(..., min_length=1)
    port: int = Field(default=993, ge=1, le=65535)
    user: str = Field(..., min_length=1)
    password: str | None = None
    folder: str = Field(default="INBOX")


class ImapTestRequest(BaseModel):
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    folder: str | None = None


class InboxApproveRequest(BaseModel):
    name: str | None = None
    slug: str | None = None


class BlacklistCreateRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=150)
    name: str | None = None
    reason: str | None = None



def _get_db(request: Request) -> Database:
    return getattr(request.app.state, "db", None) or Database(config.DB_PATH)


def _get_fs(request: Request) -> FileStore:
    return getattr(request.app.state, "fs", None) or FileStore()


def _format_newsletter(nl: Newsletter, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    res = {
        "id": nl.id,
        "name": nl.name,
        "slug": nl.slug,
        "sender_email": nl.sender_email,
        "sender_rules": nl.sender_rules,
        "enabled": nl.enabled,
        "retention_max_issues": nl.retention_max_issues,
        "retention_max_age_days": nl.retention_max_age_days,
        "created_at": nl.created_at.isoformat() if nl.created_at else None,
        "updated_at": nl.updated_at.isoformat() if nl.updated_at else None,
        "total_issues": 0,
        "done_issues": 0,
        "last_received_at": None,
    }
    if stats:
        res["total_issues"] = stats.get("total_issues", 0)
        res["done_issues"] = stats.get("done_issues", 0)
        res["last_received_at"] = stats.get("last_received_at")
    return res


def _format_issue(issue, newsletter_name: str | None = None) -> dict[str, Any]:
    return {
        "id": issue.id,
        "newsletter_id": issue.newsletter_id,
        "newsletter_name": newsletter_name,
        "email_message_id": issue.email_message_id,
        "subject": issue.subject,
        "series_index": issue.series_index,
        "status": issue.status,
        "received_at": issue.received_at.isoformat() if issue.received_at else None,
        "processed_at": issue.processed_at.isoformat() if issue.processed_at else None,
        "epub_path": issue.epub_path,
        "raw_path": issue.raw_path,
        "metadata": issue.metadata,
        "calibre_synced": issue.calibre_synced,
        "error_detail": issue.error_detail,
    }


# Newsletters Endpoints
@api_router.get("/newsletters")
def list_newsletters(request: Request) -> list[dict[str, Any]]:
    """List all configured newsletter publications with statistics."""
    db = _get_db(request)
    newsletters = db.get_all_newsletters()
    db_stats = db.get_stats()
    stats_map = {item["id"]: item for item in db_stats.get("newsletters", [])}

    return [_format_newsletter(nl, stats_map.get(nl.id)) for nl in newsletters]


@api_router.post("/newsletters", status_code=status.HTTP_201_CREATED)
def create_newsletter(data: NewsletterCreate, request: Request) -> dict[str, Any]:
    """Register a new newsletter publication."""
    db = _get_db(request)

    slug = (
        data.slug.strip().lower()
        if data.slug
        else data.name.lower().replace(" ", "-").replace("'", "")
    )

    if db.get_newsletter_by_slug(slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Newsletter with slug '{slug}' already exists",
        )

    now = datetime.now(UTC)
    nl = Newsletter(
        id=str(uuid.uuid4()),
        name=data.name.strip(),
        slug=slug,
        sender_email=data.sender_email.strip().lower(),
        sender_rules=data.sender_rules,
        enabled=data.enabled,
        retention_max_issues=data.retention_max_issues,
        retention_max_age_days=data.retention_max_age_days,
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl)
    return _format_newsletter(nl)


@api_router.get("/newsletters/{id_or_slug}")
def get_newsletter(id_or_slug: str, request: Request) -> dict[str, Any]:
    """Get single newsletter details by ID or slug."""
    db = _get_db(request)
    nl = db.get_newsletter_by_id(id_or_slug) or db.get_newsletter_by_slug(id_or_slug)
    if not nl:
        raise HTTPException(status_code=404, detail="Newsletter not found")

    db_stats = db.get_stats()
    stats_map = {item["id"]: item for item in db_stats.get("newsletters", [])}
    return _format_newsletter(nl, stats_map.get(nl.id))


@api_router.put("/newsletters/{id_or_slug}")
def update_newsletter(
    id_or_slug: str,
    data: NewsletterUpdate,
    request: Request,
    apply_retention: bool = False,
) -> dict[str, Any]:
    """Update newsletter properties."""
    db = _get_db(request)
    nl = db.get_newsletter_by_id(id_or_slug) or db.get_newsletter_by_slug(id_or_slug)
    if not nl:
        raise HTTPException(status_code=404, detail="Newsletter not found")

    fields_set = getattr(data, "model_fields_set", set())
    if "name" in fields_set and data.name is not None:
        nl.name = data.name.strip()
    if "sender_email" in fields_set and data.sender_email is not None:
        nl.sender_email = data.sender_email.strip().lower()
    if "retention_max_issues" in fields_set:
        nl.retention_max_issues = data.retention_max_issues
    if "retention_max_age_days" in fields_set:
        nl.retention_max_age_days = data.retention_max_age_days
    if "enabled" in fields_set and data.enabled is not None:
        nl.enabled = data.enabled
    if "sender_rules" in fields_set and data.sender_rules is not None:
        nl.sender_rules = data.sender_rules

    nl.updated_at = datetime.now(UTC)
    db.upsert_newsletter(nl)

    res = _format_newsletter(nl)
    if apply_retention:
        from crosspoint_newsletter.storage.retention import RetentionManager

        rm = RetentionManager(db=db, file_store=_get_fs(request))
        ret_result = rm.apply_retention(newsletter_id=nl.id, dry_run=False)
        res["retention_applied"] = ret_result.to_dict()

    return res


@api_router.post("/newsletters/{id_or_slug}/retention")
def run_newsletter_retention(
    id_or_slug: str, request: Request, dry_run: bool = False
) -> dict[str, Any]:
    """Manually trigger retention cleanup for a specific newsletter."""
    db = _get_db(request)
    nl = db.get_newsletter_by_id(id_or_slug) or db.get_newsletter_by_slug(id_or_slug)
    if not nl:
        raise HTTPException(status_code=404, detail="Newsletter not found")

    from crosspoint_newsletter.storage.retention import RetentionManager

    rm = RetentionManager(db=db, file_store=_get_fs(request))
    result = rm.apply_retention(newsletter_id=nl.id, dry_run=dry_run)
    return result.to_dict()


@api_router.delete("/newsletters/{id_or_slug}", status_code=status.HTTP_200_OK)
def delete_newsletter(id_or_slug: str, request: Request) -> dict[str, Any]:
    """Delete a newsletter and cascade delete its issues and EPUB storage files."""
    db = _get_db(request)
    fs = _get_fs(request)

    nl = db.get_newsletter_by_id(id_or_slug) or db.get_newsletter_by_slug(id_or_slug)
    if not nl:
        raise HTTPException(status_code=404, detail="Newsletter not found")

    issues = db.list_issues_by_newsletter(nl.id, limit=1000)
    for iss in issues:
        if iss.epub_path:
            fs.delete_epub(iss.epub_path)
        if iss.raw_path:
            fs.delete_raw(iss.raw_path)

    deleted = db.delete_newsletter(nl.id)
    return {"deleted": deleted, "slug": nl.slug, "issues_pruned": len(issues)}


# Issues Endpoints
@api_router.get("/issues")
def list_issues(
    request: Request,
    newsletter: str | None = Query(default=None, description="Newsletter ID or slug filter"),
    status: str | None = Query(default=None, pattern="^(pending|processing|done|error)$"),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List issues with optional filters, search, and pagination."""
    db = _get_db(request)

    nl_id = None
    if newsletter:
        nl = db.get_newsletter_by_slug(newsletter) or db.get_newsletter_by_id(newsletter)
        if nl:
            nl_id = nl.id
        else:
            return []

    issues = db.list_issues(
        newsletter_id=nl_id,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )

    # Build newsletter name lookup
    all_nls = {n.id: n.name for n in db.get_all_newsletters()}
    return [_format_issue(i, all_nls.get(i.newsletter_id, "Unknown")) for i in issues]


@api_router.get("/issues/{issue_id}")
def get_issue(issue_id: str, request: Request) -> dict[str, Any]:
    """Get detail of a single issue."""
    db = _get_db(request)
    issue = db.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    nl = db.get_newsletter_by_id(issue.newsletter_id)
    nl_name = nl.name if nl else "Unknown"
    return _format_issue(issue, nl_name)


@api_router.post("/issues/{issue_id}/reprocess")
def reprocess_issue(issue_id: str, request: Request) -> dict[str, Any]:
    """Re-run transformation pipeline for an issue in error or pending state."""
    db = _get_db(request)
    fs = _get_fs(request)
    issue = db.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    raw_file = fs.resolve_path(issue.raw_path) if issue.raw_path else None
    if not raw_file or not raw_file.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reprocess issue: raw email file not found on disk",
        )

    nl = db.get_newsletter_by_id(issue.newsletter_id)
    if not nl:
        raise HTTPException(status_code=404, detail="Associated newsletter not found")

    # Ingest and Transform
    matcher = NewsletterMatcher(db=db)
    client = EmailIngestClient(matcher=matcher)
    raw_content = client.process_email_bytes(raw_file.read_bytes())
    if not raw_content:
        raise HTTPException(status_code=500, detail="Failed parsing raw email bytes")

    raw_content.issue_id = issue.id
    raw_content.series_index = issue.series_index

    pipeline = TransformPipeline(db=db)
    result = pipeline.process_raw_content(raw_content)

    updated_issue = db.get_issue(issue.id)
    return {
        "success": result is not None,
        "issue": _format_issue(updated_issue, nl.name) if updated_issue else None,
    }


# Settings & IMAP
@api_router.post("/settings/imap")
def update_imap_settings(data: ImapSettings) -> dict[str, Any]:
    """Save IMAP settings and update configuration."""
    config.update_imap_config(
        host=data.host,
        port=data.port,
        user=data.user,
        password=data.password,
        folder=data.folder,
    )
    return {
        "success": True,
        "message": "Configurazione IMAP salvata con successo",
        "config": {
            "host": config.IMAP_HOST,
            "port": config.IMAP_PORT,
            "user": config.IMAP_USER,
            "password_set": bool(config.IMAP_PASSWORD),
            "folder": config.IMAP_FOLDER,
        },
    }


@api_router.post("/imap/test")
def test_imap_connection(data: ImapTestRequest | None = None) -> dict[str, Any]:
    """Test IMAP server connectivity with given parameters or stored config."""
    test_host = (
        data.host.strip() if data and data.host and data.host.strip() else None
    ) or config.IMAP_HOST
    test_port = (data.port if data and data.port else None) or config.IMAP_PORT
    test_user = (
        data.user.strip() if data and data.user and data.user.strip() else None
    ) or config.IMAP_USER
    test_pw = (
        data.password.strip()
        if data and data.password and data.password.strip()
        else None
    ) or config.IMAP_PASSWORD
    test_folder = (
        data.folder.strip() if data and data.folder and data.folder.strip() else None
    ) or config.IMAP_FOLDER

    matcher = NewsletterMatcher(db=Database(config.DB_PATH))
    client = EmailIngestClient(
        matcher=matcher,
        host=test_host,
        port=test_port,
        user=test_user,
        password=test_pw,
        folder=test_folder,
    )
    success, msg = client.test_connection()
    return {"success": success, "message": msg}


# --- Inbox / Unassigned Emails Discovery ---
@api_router.get("/inbox")
def list_inbox_emails(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    """List unassigned emails in the discovery inbox queue."""
    db = _get_db(request)
    items = db.list_inbox_emails(limit=limit)
    return [
        {
            "id": i.id,
            "email_message_id": i.email_message_id,
            "sender_header": i.sender_header,
            "sender_email": i.sender_email,
            "sender_name": i.sender_name,
            "subject": i.subject,
            "received_at": i.received_at.isoformat(),
            "created_at": i.created_at.isoformat(),
        }
        for i in items
    ]


@api_router.post("/inbox/{inbox_id}/approve")
def approve_inbox_email(
    inbox_id: str,
    request: Request,
    data: InboxApproveRequest | None = None,
) -> dict[str, Any]:
    """Approve an unassigned email: register newsletter and convert to EPUB."""
    db = _get_db(request)
    item = db.get_inbox_email(inbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="Email non trovata in inbox")

    nl_name = (
        (data.name.strip() if data and data.name and data.name.strip() else None)
        or item.sender_name
        or "Newsletter"
    )
    nl_slug = (
        (data.slug.strip() if data and data.slug and data.slug.strip() else None)
        or nl_name.lower().replace(" ", "-").replace("'", "")
    )

    # 1. Upsert newsletter
    existing_nl = db.get_newsletter_by_slug(nl_slug)
    if not existing_nl:
        now = datetime.now(UTC)
        nl = Newsletter(
            id=str(uuid.uuid4()),
            name=nl_name,
            slug=nl_slug,
            sender_email=item.sender_email,
            created_at=now,
            updated_at=now,
        )
        db.upsert_newsletter(nl)
    else:
        nl = existing_nl

    # 2. Process raw email into EPUB
    created_epub = None
    if item.raw_eml_path and Path(item.raw_eml_path).exists():
        raw_bytes = Path(item.raw_eml_path).read_bytes()
        matcher = NewsletterMatcher(db=db)
        client = EmailIngestClient(matcher=matcher)
        pipeline = TransformPipeline(db=db)

        raw_content = client.process_email_bytes(raw_bytes)
        if raw_content:
            created_epub = pipeline.process_raw_content(raw_content)

    # 3. Remove from inbox queue
    db.delete_inbox_email(inbox_id)

    return {
        "success": True,
        "message": f"Newsletter '{nl.name}' approvata con successo!" + (
            " ed EPUB generato" if created_epub else ""
        ),
        "newsletter": _format_newsletter(nl, {"total_issues": 1, "done_issues": 1} if created_epub else None),
        "epub_created": str(created_epub) if created_epub else None,
    }


@api_router.post("/inbox/{inbox_id}/block")
def block_inbox_email(inbox_id: str, request: Request) -> dict[str, Any]:
    """Dismiss an email and permanently blacklist its sender."""
    db = _get_db(request)
    item = db.get_inbox_email(inbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="Email non trovata in inbox")

    db.add_to_blacklist(
        sender_email=item.sender_email,
        sender_name=item.sender_name,
        reason="Rifiutato da Inbox",
    )
    if item.raw_eml_path:
        Path(item.raw_eml_path).unlink(missing_ok=True)
    db.delete_inbox_email(inbox_id)
    return {
        "success": True,
        "message": f"Mittente '{item.sender_email}' bloccato ed email rimossa dalla coda",
        "sender_email": item.sender_email,
    }


@api_router.delete("/inbox/{inbox_id}")
def dismiss_inbox_email(
    inbox_id: str, request: Request, blacklist: bool = Query(default=False)
) -> dict[str, Any]:
    """Dismiss/delete an email from the inbox queue, optionally blacklisting the sender."""
    db = _get_db(request)
    item = db.get_inbox_email(inbox_id)
    if not item:
        raise HTTPException(status_code=404, detail="Email non trovata in inbox")

    if blacklist:
        db.add_to_blacklist(
            sender_email=item.sender_email,
            sender_name=item.sender_name,
            reason="Rifiutato da Inbox",
        )

    if item.raw_eml_path:
        Path(item.raw_eml_path).unlink(missing_ok=True)
    db.delete_inbox_email(inbox_id)
    if blacklist:
        msg = f"Mittente '{item.sender_email}' bloccato ed email rimossa"
    else:
        msg = "Email rimossa dalla coda"
    return {"success": True, "message": msg, "blacklisted": blacklist}



# --- Blacklist Endpoints ---
@api_router.get("/blacklist")
def list_blacklist(request: Request, limit: int = 100) -> list[dict[str, Any]]:
    """List all blacklisted sender email addresses."""
    db = _get_db(request)
    entries = db.list_blacklist(limit=limit)
    return [
        {
            "id": e.id,
            "sender_email": e.sender_email,
            "sender_name": e.sender_name,
            "reason": e.reason,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]


@api_router.post("/blacklist", status_code=status.HTTP_201_CREATED)
def add_blacklist_entry(data: BlacklistCreateRequest, request: Request) -> dict[str, Any]:
    """Add a sender email to the blacklist."""
    db = _get_db(request)
    entry = db.add_to_blacklist(
        sender_email=data.email,
        sender_name=data.name,
        reason=data.reason or "Manuale",
    )
    return {
        "success": True,
        "message": f"Mittente '{entry.sender_email}' aggiunto alla blacklist",
        "entry": {
            "id": entry.id,
            "sender_email": entry.sender_email,
            "sender_name": entry.sender_name,
            "reason": entry.reason,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        },
    }


@api_router.delete("/blacklist/{id_or_email}")
def remove_blacklist_entry(id_or_email: str, request: Request) -> dict[str, Any]:
    """Remove a sender from the blacklist."""
    db = _get_db(request)
    deleted = db.remove_from_blacklist(id_or_email)
    if not deleted:
        raise HTTPException(status_code=404, detail="Indirizzo non trovato in blacklist")
    return {"success": True, "message": "Indirizzo rimosso dalla blacklist"}



@api_router.get("/poll/status")
def get_poll_status(request: Request) -> dict[str, Any]:
    """Get current status of background auto-polling task."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler:
        return {"enabled": False, "running": False, "is_polling": False}
    return scheduler.get_status()


@api_router.post("/poll/run")
async def trigger_poll_run(request: Request) -> dict[str, Any]:
    """Manually trigger an immediate IMAP fetch & EPUB conversion cycle."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler:
        import asyncio

        from crosspoint_newsletter.scheduler import run_pipeline_cycle

        db = _get_db(request)
        fs = _get_fs(request)
        res = await asyncio.to_thread(run_pipeline_cycle, db, fs)
        return {"status": "success", "result": res}
    return await scheduler.trigger_now()
