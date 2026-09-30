"""Server-side rendered WebUI for CrossPoint Newsletter management."""

from __future__ import annotations

import logging
import socket
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from crosspoint_newsletter import config
from crosspoint_newsletter.ingest.email_client import EmailIngestClient
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
from crosspoint_newsletter.serve.opds_builder import EPUB_MIME
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Newsletter
from crosspoint_newsletter.transform.pipeline import TransformPipeline

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def format_datetime_local(
    value: datetime | str | None,
    fmt: str = "%Y-%m-%d %H:%M:%S",
) -> str:
    """Convert UTC or naive datetime / ISO string to configured local timezone."""
    if not value:
        return "-"
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except Exception:
            return value
    elif isinstance(value, datetime):
        dt = value
    else:
        return "-"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    try:
        target_tz = ZoneInfo(config.TIMEZONE)
    except Exception:
        target_tz = ZoneInfo("Europe/Rome")

    return dt.astimezone(target_tz).strftime(fmt)


templates.env.filters["local_dt"] = format_datetime_local
templates.env.globals["local_dt"] = format_datetime_local

ui_router = APIRouter(prefix="/ui", tags=["WebUI"])


def _get_db(request: Request) -> Database:
    return getattr(request.app.state, "db", None) or Database(config.DB_PATH)


def _get_fs(request: Request) -> FileStore:
    return getattr(request.app.state, "fs", None) or FileStore()


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return str(ip)
    except Exception:
        return "127.0.0.1"


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.2f} MB"


@ui_router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Dashboard overview page."""
    db = _get_db(request)
    fs = _get_fs(request)

    db_stats = db.get_stats()
    disk_stats = fs.get_disk_usage()
    recent_issues = db.get_recent_done_issues(limit=8)

    scheduler = getattr(request.app.state, "scheduler", None)
    polling_info = (
        scheduler.get_status()
        if scheduler
        else {"enabled": False, "running": False, "is_polling": False}
    )

    all_nls = {n.id: n.name for n in db.get_all_newsletters()}
    recent_data = []
    for iss in recent_issues:
        recent_data.append({
            "id": iss.id,
            "newsletter_name": all_nls.get(iss.newsletter_id, "Newsletter"),
            "subject": iss.subject,
            "series_index": iss.series_index,
            "received_at": iss.received_at.isoformat() if iss.received_at else None,
            "status": iss.status,
        })

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "inbox_count": db.count_inbox_emails(),
            "stats": db_stats,
            "disk_stats_str": _format_bytes(disk_stats["total_bytes"]),
            "recent_issues": recent_data,
            "host_ip": _get_local_ip(),
            "port": config.OPDS_PORT,
            "polling": polling_info,
            "message": message,
            "error": error,
        },
    )


@ui_router.post("/poll/trigger")
async def trigger_poll_ui(request: Request) -> RedirectResponse:
    """Manually trigger email polling and EPUB generation from WebUI."""
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler:
        res = await scheduler.trigger_now()
    else:
        import asyncio

        from crosspoint_newsletter.scheduler import run_pipeline_cycle

        db = _get_db(request)
        fs = _get_fs(request)
        cycle_res = await asyncio.to_thread(run_pipeline_cycle, db, fs)
        res = {"status": "success", "result": cycle_res}

    if res.get("status") == "already_running":
        return RedirectResponse(
            url="/ui/?error=Un+ciclo+di+controllo+posta+è+già+in+corso",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    elif res.get("status") == "error":
        err_msg = res.get("error", "Errore durante il controllo posta")
        return RedirectResponse(
            url=f"/ui/?error={urllib.parse.quote_plus(err_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    cycle_info = res.get("result", {})
    unseen = cycle_info.get("unseen_count", 0)
    generated = cycle_info.get("epub_generated", 0)
    errors = cycle_info.get("errors", 0)
    msg = f"Controllo posta completato! {unseen} email trovate, {generated} nuovi EPUB generati"
    if errors > 0:
        msg += f" ({errors} errori)"
    return RedirectResponse(
        url=f"/ui/?message={urllib.parse.quote_plus(msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@ui_router.get("/newsletters", response_class=HTMLResponse)
def newsletters_list(
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Manage newsletter publications."""
    db = _get_db(request)
    newsletters = db.get_all_newsletters()
    db_stats = db.get_stats()
    stats_map = {item["id"]: item for item in db_stats.get("newsletters", [])}

    nl_data = []
    for nl in newsletters:
        st = stats_map.get(nl.id, {})
        nl_data.append({
            "id": nl.id,
            "name": nl.name,
            "slug": nl.slug,
            "sender_email": nl.sender_email,
            "enabled": nl.enabled,
            "total_issues": st.get("total_issues", 0),
            "done_issues": st.get("done_issues", 0),
            "retention_max_issues": nl.retention_max_issues,
            "retention_max_age_days": nl.retention_max_age_days,
        })

    return templates.TemplateResponse(
        request=request,
        name="newsletters.html",
        context={
            "active_page": "newsletters",
            "newsletters": nl_data,
            "message": message,
            "error": error,
        },
    )


@ui_router.post("/newsletters/add")
def add_newsletter(
    request: Request,
    name: Annotated[str, Form()],
    sender_email: Annotated[str, Form()],
    slug: Annotated[str | None, Form()] = None,
    max_issues: Annotated[int | None, Form()] = None,
    max_age_days: Annotated[int | None, Form()] = None,
) -> RedirectResponse:
    """Handle adding a new newsletter via HTML form."""
    db = _get_db(request)

    clean_slug = (
        slug.strip().lower()
        if slug and slug.strip()
        else name.lower().replace(" ", "-").replace("'", "")
    )

    if db.get_newsletter_by_slug(clean_slug):
        return RedirectResponse(
            url=f"/ui/newsletters?error=Una+newsletter+con+slug+{clean_slug}+esiste+già",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    now = datetime.now(UTC)
    nl = Newsletter(
        id=str(uuid.uuid4()),
        name=name.strip(),
        slug=clean_slug,
        sender_email=sender_email.strip().lower(),
        retention_max_issues=max_issues,
        retention_max_age_days=max_age_days,
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl)
    return RedirectResponse(
        url=f"/ui/newsletters?message=Newsletter+{nl.name}+creata+con+successo",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@ui_router.post("/newsletters/{nl_id}/edit")
def edit_newsletter_ui(
    nl_id: str,
    request: Request,
    name: Annotated[str, Form()],
    sender_email: Annotated[str, Form()],
    max_issues: Annotated[str | None, Form()] = None,
    max_age_days: Annotated[str | None, Form()] = None,
    apply_retention_now: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    """Update newsletter name, sender, and retention policies."""
    db = _get_db(request)
    fs = _get_fs(request)
    nl = db.get_newsletter_by_id(nl_id)
    if not nl:
        return RedirectResponse(
            url="/ui/newsletters?error=Newsletter+non+trovata",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    clean_name = name.strip()
    if clean_name:
        nl.name = clean_name
    clean_sender = sender_email.strip().lower()
    if clean_sender:
        nl.sender_email = clean_sender

    # Retention max issues
    if max_issues is not None and str(max_issues).strip():
        try:
            val = int(str(max_issues).strip())
            nl.retention_max_issues = val if val > 0 else None
        except ValueError:
            pass
    else:
        nl.retention_max_issues = None

    # Retention max age days
    if max_age_days is not None and str(max_age_days).strip():
        try:
            val = int(str(max_age_days).strip())
            nl.retention_max_age_days = val if val > 0 else None
        except ValueError:
            pass
    else:
        nl.retention_max_age_days = None

    nl.updated_at = datetime.now(UTC)
    db.upsert_newsletter(nl)

    msg = f"Newsletter '{nl.name}' aggiornata con successo!"
    if apply_retention_now:
        from crosspoint_newsletter.storage.retention import RetentionManager

        rm = RetentionManager(db=db, file_store=fs)
        ret_result = rm.apply_retention(newsletter_id=nl.id, dry_run=False)
        if ret_result.pruned_count > 0:
            mb = round(ret_result.bytes_reclaimed / (1024 * 1024), 2)
            msg += f" Pulizia retention completata: {ret_result.pruned_count} uscite rimosse ({mb} MB liberati)."
        else:
            msg += " Nessuna uscita eccedente da eliminare."

    return RedirectResponse(
        url=f"/ui/newsletters?message={urllib.parse.quote_plus(msg)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@ui_router.post("/newsletters/{nl_id}/toggle")
def toggle_newsletter(nl_id: str, request: Request) -> RedirectResponse:
    """Enable or disable a newsletter."""
    db = _get_db(request)
    nl = db.get_newsletter_by_id(nl_id)
    if nl:
        nl.enabled = not nl.enabled
        nl.updated_at = datetime.now(UTC)
        db.upsert_newsletter(nl)
        state_str = "attivata" if nl.enabled else "disattivata"
        return RedirectResponse(
            url=f"/ui/newsletters?message=Newsletter+{nl.name}+{state_str}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url="/ui/newsletters", status_code=status.HTTP_303_SEE_OTHER)


@ui_router.post("/newsletters/{nl_id}/delete")
def delete_newsletter_ui(nl_id: str, request: Request) -> RedirectResponse:
    """Delete newsletter and all files."""
    db = _get_db(request)
    fs = _get_fs(request)

    nl = db.get_newsletter_by_id(nl_id)
    if nl:
        issues = db.list_issues_by_newsletter(nl.id, limit=1000)
        for iss in issues:
            if iss.epub_path:
                fs.delete_epub(iss.epub_path)
            if iss.raw_path:
                fs.delete_raw(iss.raw_path)
        db.delete_newsletter(nl.id)
        return RedirectResponse(
            url=f"/ui/newsletters?message=Newsletter+{nl.name}+eliminata",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url="/ui/newsletters", status_code=status.HTTP_303_SEE_OTHER)


@ui_router.get("/library", response_class=HTMLResponse)
def library_view(
    request: Request,
    newsletter: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None),
) -> Response:
    """Browse library issues."""
    db = _get_db(request)
    all_nls = db.get_all_newsletters()
    nl_map = {n.id: n.name for n in all_nls}

    nl_id = None
    if newsletter:
        found_nl = db.get_newsletter_by_slug(newsletter) or db.get_newsletter_by_id(newsletter)
        if found_nl:
            nl_id = found_nl.id

    issues = db.list_issues(
        newsletter_id=nl_id,
        status=status_filter if status_filter in ("done", "pending", "error") else None,
        search=search,
        limit=100,
    )

    issue_rows = []
    for iss in issues:
        issue_rows.append({
            "id": iss.id,
            "newsletter_name": nl_map.get(iss.newsletter_id, "Newsletter"),
            "subject": iss.subject,
            "series_index": iss.series_index,
            "status": iss.status,
            "received_at": iss.received_at.isoformat() if iss.received_at else None,
            "error_detail": iss.error_detail,
        })

    return templates.TemplateResponse(
        request=request,
        name="library.html",
        context={
            "active_page": "library",
            "issues": issue_rows,
            "all_newsletters": all_nls,
            "selected_nl": newsletter,
            "selected_status": status_filter,
            "search": search,
        },
    )


@ui_router.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_detail_view(
    issue_id: str,
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """View details of a single issue."""
    db = _get_db(request)
    issue = db.get_issue(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue non trovata")

    nl = db.get_newsletter_by_id(issue.newsletter_id)
    issue_data = {
        "id": issue.id,
        "newsletter_name": nl.name if nl else "Sconosciuto",
        "subject": issue.subject,
        "series_index": issue.series_index,
        "email_message_id": issue.email_message_id,
        "status": issue.status,
        "received_at": (
            format_datetime_local(issue.received_at, "%Y-%m-%d %H:%M")
            if issue.received_at
            else None
        ),
        "processed_at": (
            format_datetime_local(issue.processed_at, "%Y-%m-%d %H:%M")
            if issue.processed_at
            else None
        ),
        "epub_path": issue.epub_path,
        "raw_path": issue.raw_path,
        "metadata": issue.metadata,
        "error_detail": issue.error_detail,
    }

    return templates.TemplateResponse(
        request=request,
        name="issue_detail.html",
        context={
            "active_page": "library",
            "issue": issue_data,
            "message": message,
            "error": error,
        },
    )


@ui_router.post("/issues/{issue_id}/reprocess")
def reprocess_issue_ui(issue_id: str, request: Request) -> RedirectResponse:
    """Reprocess an issue from the UI."""
    db = _get_db(request)
    fs = _get_fs(request)
    issue = db.get_issue(issue_id)
    if not issue or not issue.raw_path:
        return RedirectResponse(
            url=f"/ui/issues/{issue_id}?error=Impossibile+riprocessare:+email+raw+assente",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    raw_file = fs.resolve_path(issue.raw_path)
    if not raw_file or not raw_file.is_file():
        return RedirectResponse(
            url=f"/ui/issues/{issue_id}?error=File+raw+non+trovato+su+disco",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    matcher = NewsletterMatcher(db=db)
    client = EmailIngestClient(matcher=matcher)
    raw_content = client.process_email_bytes(raw_file.read_bytes())
    if not raw_content:
        return RedirectResponse(
            url=f"/ui/issues/{issue_id}?error=Fallito+parsing+del+file+email",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    raw_content.issue_id = issue.id
    raw_content.series_index = issue.series_index

    pipeline = TransformPipeline(db=db)
    res = pipeline.process_raw_content(raw_content)

    if res:
        return RedirectResponse(
            url=f"/ui/issues/{issue_id}?message=Numero+riprocessato+con+successo",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/ui/issues/{issue_id}?error=Errore+durante+la+conversione",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@ui_router.get("/issues/{issue_id}/download")
def download_issue_ui(issue_id: str, request: Request) -> FileResponse:
    """Download EPUB file directly from WebUI without OPDS Basic Auth."""
    db = _get_db(request)
    fs = _get_fs(request)
    issue = db.get_issue(issue_id)
    if not issue or issue.status != "done":
        raise HTTPException(status_code=404, detail="Issue non trovata o non completata")

    file_path = fs.resolve_path(issue.epub_path)
    if not file_path or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File EPUB non trovato su disco")

    return FileResponse(
        path=file_path,
        media_type=EPUB_MIME,
        filename=file_path.name,
    )



@ui_router.get("/settings", response_class=HTMLResponse)
def settings_view(
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """System settings and connectivity overview."""
    fs = _get_fs(request)
    disk = fs.get_disk_usage()

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "host_ip": _get_local_ip(),
            "message": message,
            "error": error,
            "imap_config": {
                "host": config.IMAP_HOST,
                "port": config.IMAP_PORT,
                "user": config.IMAP_USER,
                "password_set": bool(config.IMAP_PASSWORD),
                "folder": config.IMAP_FOLDER,
            },
            "opds_config": {
                "host": config.OPDS_HOST,
                "port": config.OPDS_PORT,
                "auth_enabled": bool(config.OPDS_USERNAME and config.OPDS_PASSWORD),
                "username": config.OPDS_USERNAME,
            },
            "storage_config": {
                "data_dir": str(config.DATA_DIR),
                "db_path": str(config.DB_PATH),
                "epub_dir": str(config.EPUB_DIR),
                "keep_raw": config.CN_KEEP_RAW,
                "total_files": disk["total_files"],
                "total_bytes_str": _format_bytes(disk["total_bytes"]),
            },
            "timezone_config": {
                "current": config.TIMEZONE,
                "current_time": format_datetime_local(datetime.now(UTC), "%Y-%m-%d %H:%M:%S"),
            },
        },
    )


@ui_router.post("/settings/timezone/save")
def save_timezone_ui(
    request: Request,
    timezone: Annotated[str, Form()],
) -> RedirectResponse:
    """Save updated timezone setting."""
    clean_tz = timezone.strip() if timezone else ""
    if not clean_tz:
        return RedirectResponse(
            url="/ui/settings?error=Il+fuso+orario+non+può+essere+vuoto",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        config.update_timezone(clean_tz)
        return RedirectResponse(
            url=f"/ui/settings?message=Fuso+orario+aggiornato+con+successo+a+{urllib.parse.quote_plus(config.TIMEZONE)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/ui/settings?error=Fuso+orario+non+valido:+{urllib.parse.quote_plus(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@ui_router.post("/settings/imap/save")
def save_imap_ui(
    request: Request,
    host: Annotated[str, Form()],
    port: Annotated[int, Form()] = 993,
    user: Annotated[str, Form()] = "",
    password: Annotated[str | None, Form()] = None,
    folder: Annotated[str, Form()] = "INBOX",
) -> RedirectResponse:
    """Save updated IMAP credentials to .env and runtime config."""
    if not host.strip() or not user.strip():
        return RedirectResponse(
            url="/ui/settings?error=Host+server+e+Account+utente+sono+obbligatori",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        config.update_imap_config(
            host=host,
            port=port,
            user=user,
            password=password if password and password.strip() else None,
            folder=folder,
        )
        return RedirectResponse(
            url="/ui/settings?message=Configurazione+IMAP+salvata+con+successo",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/ui/settings?error=Errore+durante+il+salvataggio:+{exc}",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@ui_router.post("/settings/test-imap", response_class=HTMLResponse)
@ui_router.post("/settings/imap/test", response_class=HTMLResponse)
def test_imap_ui(
    request: Request,
    host: Annotated[str | None, Form()] = None,
    port: Annotated[int | None, Form()] = None,
    user: Annotated[str | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
    folder: Annotated[str | None, Form()] = None,
) -> Response:
    """Run IMAP connection test using form inputs or current config."""
    fs = _get_fs(request)
    disk = fs.get_disk_usage()

    test_host = (host.strip() if host and host.strip() else None) or config.IMAP_HOST
    test_port = port or config.IMAP_PORT
    test_user = (user.strip() if user and user.strip() else None) or config.IMAP_USER
    test_pw = (
        password.strip() if password and password.strip() else None
    ) or config.IMAP_PASSWORD
    test_folder = (
        folder.strip() if folder and folder.strip() else None
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

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "host_ip": _get_local_ip(),
            "test_result": {"success": success, "message": msg},
            "imap_config": {
                "host": test_host,
                "port": test_port,
                "user": test_user,
                "password_set": bool(test_pw),
                "folder": test_folder,
            },
            "opds_config": {
                "host": config.OPDS_HOST,
                "port": config.OPDS_PORT,
                "auth_enabled": bool(config.OPDS_USERNAME and config.OPDS_PASSWORD),
                "username": config.OPDS_USERNAME,
            },
            "storage_config": {
                "data_dir": str(config.DATA_DIR),
                "db_path": str(config.DB_PATH),
                "epub_dir": str(config.EPUB_DIR),
                "keep_raw": config.CN_KEEP_RAW,
                "total_files": disk["total_files"],
                "total_bytes_str": _format_bytes(disk["total_bytes"]),
            },
            "timezone_config": {
                "current": config.TIMEZONE,
                "current_time": format_datetime_local(datetime.now(UTC), "%Y-%m-%d %H:%M:%S"),
            },
        },
    )


# --- Inbox / Triage Routes ---
@ui_router.get("/inbox", response_class=HTMLResponse)
def inbox_list(
    request: Request,
    message: str | None = None,
    error: str | None = None,
) -> Response:
    """Inbox triage page for unassigned newsletter emails and blacklist management."""
    db = _get_db(request)
    items = db.list_inbox_emails(limit=100)
    inbox_count = len(items)

    formatted_items = []
    for item in items:
        formatted_items.append({
            "id": item.id,
            "sender_name": item.sender_name,
            "sender_email": item.sender_email,
            "subject": item.subject,
            "received_at": item.received_at.isoformat() if item.received_at else None,
        })

    blacklist_entries = db.list_blacklist(limit=200)
    formatted_blacklist = []
    for b in blacklist_entries:
        formatted_blacklist.append({
            "id": b.id,
            "sender_email": b.sender_email,
            "sender_name": b.sender_name or "-",
            "reason": b.reason or "-",
            "created_at": format_datetime_local(b.created_at, "%Y-%m-%d %H:%M"),
        })

    return templates.TemplateResponse(
        request=request,
        name="inbox.html",
        context={
            "active_page": "inbox",
            "inbox_count": inbox_count,
            "items": formatted_items,
            "blacklist": formatted_blacklist,
            "blacklist_count": len(formatted_blacklist),
            "message": message,
            "error": error,
        },
    )


@ui_router.post("/inbox/{inbox_id}/approve")
def inbox_approve_form(
    inbox_id: str,
    request: Request,
    name: Annotated[str | None, Form()] = None,
    slug: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Handle approval of an unassigned email via WebUI form."""
    db = _get_db(request)
    item = db.get_inbox_email(inbox_id)
    if not item:
        return RedirectResponse(url="/ui/inbox?error=Email+non+trovata", status_code=303)

    nl_name = (name.strip() if name and name.strip() else None) or item.sender_name or "Newsletter"
    nl_slug = (slug.strip() if slug and slug.strip() else None) or nl_name.lower().replace(" ", "-").replace("'", "")

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

    # 3. Remove from inbox
    db.delete_inbox_email(inbox_id)

    msg = f"Newsletter '{nl.name}' approvata con successo!" + (
        " Numero convertito in EPUB ed esposto su OPDS." if created_epub else ""
    )
    return RedirectResponse(
        url=f"/ui/inbox?message={urllib.parse.quote_plus(msg)}",
        status_code=303,
    )


@ui_router.post("/inbox/{inbox_id}/dismiss")
def inbox_dismiss_form(inbox_id: str, request: Request) -> RedirectResponse:
    """Dismiss/delete an email from the inbox queue via WebUI form."""
    db = _get_db(request)
    item = db.get_inbox_email(inbox_id)
    if not item:
        return RedirectResponse(url="/ui/inbox?error=Email+non+trovata", status_code=303)

    if item.raw_eml_path:
        Path(item.raw_eml_path).unlink(missing_ok=True)
    db.delete_inbox_email(inbox_id)

    msg = "Email scartata e rimossa dalla coda."
    return RedirectResponse(
        url=f"/ui/inbox?message={urllib.parse.quote_plus(msg)}",
        status_code=303,
    )


@ui_router.post("/inbox/{inbox_id}/block")
def inbox_block_form(inbox_id: str, request: Request) -> RedirectResponse:
    """Dismiss an email and permanently blacklist its sender via WebUI."""
    db = _get_db(request)
    item = db.get_inbox_email(inbox_id)
    if not item:
        return RedirectResponse(url="/ui/inbox?error=Email+non+trovata", status_code=303)

    db.add_to_blacklist(
        sender_email=item.sender_email,
        sender_name=item.sender_name,
        reason="Rifiutato da Inbox",
    )
    if item.raw_eml_path:
        Path(item.raw_eml_path).unlink(missing_ok=True)
    db.delete_inbox_email(inbox_id)

    msg = f"Mittente '{item.sender_email}' aggiunto alla Blacklist ed email rimossa dalla coda."
    return RedirectResponse(
        url=f"/ui/inbox?message={urllib.parse.quote_plus(msg)}",
        status_code=303,
    )


@ui_router.post("/blacklist/add")
def blacklist_add_form(
    request: Request,
    email: Annotated[str, Form()],
    name: Annotated[str | None, Form()] = None,
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Add a sender email to the blacklist via WebUI form."""
    db = _get_db(request)
    clean_email = email.strip()
    if not clean_email:
        return RedirectResponse(url="/ui/inbox?error=Indirizzo+email+obbligatorio", status_code=303)

    entry = db.add_to_blacklist(
        sender_email=clean_email,
        sender_name=name.strip() if name and name.strip() else None,
        reason=reason.strip() if reason and reason.strip() else "Manuale",
    )
    msg = f"Indirizzo '{entry.sender_email}' aggiunto alla Blacklist."
    return RedirectResponse(
        url=f"/ui/inbox?message={urllib.parse.quote_plus(msg)}",
        status_code=303,
    )


@ui_router.post("/blacklist/{entry_id}/delete")
def blacklist_delete_form(entry_id: str, request: Request) -> RedirectResponse:
    """Remove an entry from the blacklist via WebUI form."""
    db = _get_db(request)
    entry = db.get_blacklist_entry(entry_id)
    email_str = entry.sender_email if entry else entry_id
    deleted = db.remove_from_blacklist(entry_id)
    if deleted:
        msg = f"Mittente '{email_str}' rimosso dalla Blacklist."
        return RedirectResponse(
            url=f"/ui/inbox?message={urllib.parse.quote_plus(msg)}",
            status_code=303,
        )
    return RedirectResponse(url="/ui/inbox?error=Voce+blacklist+non+trovata", status_code=303)

