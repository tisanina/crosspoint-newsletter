"""Command line management interface for CrossPoint Newsletter Server (cn)."""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime

from crosspoint_newsletter import config
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Newsletter
from crosspoint_newsletter.storage.retention import RetentionManager


def _get_db() -> Database:
    return Database(config.DB_PATH)


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.2f} MB"


def cmd_newsletter_list(args: argparse.Namespace) -> None:
    db = _get_db()
    newsletters = db.get_all_newsletters()
    if not newsletters:
        print("No newsletters registered.")
        return

    print(f"{'SLUG':<22} {'NAME':<28} {'SENDER':<32} {'RETENTION':<18} {'STATUS':<8}")
    print("-" * 112)
    for nl in newsletters:
        retention_parts = []
        if nl.retention_max_issues:
            retention_parts.append(f"max={nl.retention_max_issues}")
        if nl.retention_max_age_days:
            retention_parts.append(f"days={nl.retention_max_age_days}")
        retention_str = ", ".join(retention_parts) or "unlimited"
        status_str = "enabled" if nl.enabled else "disabled"

        print(f"{nl.slug:<22} {nl.name[:26]:<28} {nl.sender_email[:30]:<32} {retention_str:<18} {status_str:<8}")


def cmd_newsletter_add(args: argparse.Namespace) -> None:
    db = _get_db()
    slug = args.slug or args.name.lower().replace(" ", "-").replace("'", "")
    now = datetime.now(UTC)

    nl = Newsletter(
        id=str(uuid.uuid4()),
        name=args.name,
        slug=slug,
        sender_email=args.sender,
        retention_max_issues=args.max_issues,
        retention_max_age_days=args.max_age_days,
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl)
    print(f"✅ Newsletter '{nl.name}' added/updated successfully (slug: {nl.slug}).")


def cmd_newsletter_update(args: argparse.Namespace) -> None:
    db = _get_db()
    nl = db.get_newsletter_by_slug(args.slug_or_id) or db.get_newsletter_by_id(args.slug_or_id)
    if not nl:
        print(f"❌ Newsletter non trovata: {args.slug_or_id}")
        sys.exit(1)

    if args.name:
        nl.name = args.name.strip()
    if args.sender:
        nl.sender_email = args.sender.strip().lower()
    if args.max_issues is not None:
        nl.retention_max_issues = args.max_issues if args.max_issues > 0 else None
    if args.unlimited_issues:
        nl.retention_max_issues = None
    if args.max_age_days is not None:
        nl.retention_max_age_days = args.max_age_days if args.max_age_days > 0 else None
    if args.unlimited_age:
        nl.retention_max_age_days = None
    if args.enable:
        nl.enabled = True
    elif args.disable:
        nl.enabled = False

    nl.updated_at = datetime.now(UTC)
    db.upsert_newsletter(nl)
    print(f"✅ Newsletter '{nl.name}' aggiornata con successo (slug: {nl.slug}).")

    if args.apply_retention:
        rm = RetentionManager(db=db, file_store=FileStore())
        res = rm.apply_retention(newsletter_id=nl.id, dry_run=False)
        mb = round(res.bytes_reclaimed / (1024 * 1024), 2)
        print(f"🧹 Pulizia retention applicata: {res.pruned_count} uscite rimosse ({mb} MB liberati).")


def cmd_issue_list(args: argparse.Namespace) -> None:
    db = _get_db()
    target_nls: list[Newsletter] = []
    if args.newsletter:
        nl = db.get_newsletter_by_slug(args.newsletter) or db.get_newsletter_by_id(args.newsletter)
        if not nl:
            print(f"❌ Newsletter not found: {args.newsletter}")
            sys.exit(1)
        target_nls.append(nl)
    else:
        target_nls = db.get_all_newsletters()

    print(f"{'INDEX':<6} {'STATUS':<10} {'RECEIVED':<20} {'NEWSLETTER':<20} {'SUBJECT':<45}")
    print("-" * 105)

    total_count = 0
    for nl in target_nls:
        issues = db.list_issues_by_newsletter(
            newsletter_id=nl.id,
            status=args.status,
            limit=args.limit,
        )
        for issue in issues:
            rec_str = issue.received_at.strftime("%Y-%m-%d %H:%M")
            subj_str = issue.subject[:42] + "..." if len(issue.subject) > 42 else issue.subject
            print(f"#{issue.series_index:<5} {issue.status:<10} {rec_str:<20} {nl.name[:18]:<20} {subj_str:<45}")
            total_count += 1

    print("-" * 105)
    print(f"Total issues listed: {total_count}")


def cmd_issue_stats(args: argparse.Namespace) -> None:
    db = _get_db()
    fs = FileStore()

    db_stats = db.get_stats()
    fs_stats = fs.get_disk_usage()

    print("\n📊 CROSSPOINT NEWSLETTER — CATALOG & STORAGE STATS")
    print("=" * 60)
    print(f"Registered Newsletters: {db_stats['newsletters_count']}")
    print(f"Total Issues in DB:     {db_stats['total_issues']} (done: {db_stats['done_issues']}, pending: {db_stats['pending_issues']}, error: {db_stats['error_issues']})")
    print(f"EPUB Files on Disk:     {fs_stats['total_files']} files")
    print(f"Total Storage Occupied: {_format_bytes(fs_stats['total_bytes'])}")
    print("\nSeries Breakdown:")
    print(f"{'SLUG':<22} {'ISSUES':<10} {'FILES':<8} {'SIZE':<12} {'LAST RECEIVED':<16}")
    print("-" * 70)

    fs_map = {s["slug"]: s for s in fs_stats["series"]}
    for nl in db_stats["newsletters"]:
        slug = nl["slug"]
        file_info = fs_map.get(slug, {"file_count": 0, "total_bytes": 0})
        last_dt = nl["last_received_at"][:10] if nl["last_received_at"] else "never"
        size_str = _format_bytes(file_info["total_bytes"])
        print(f"{slug:<22} {nl['done_issues']}/{nl['total_issues']:<8} {file_info['file_count']:<8} {size_str:<12} {last_dt:<16}")
    print("=" * 70)


def cmd_retention_run(args: argparse.Namespace) -> None:
    db = _get_db()
    rm = RetentionManager(db=db)

    mode_label = "DRY RUN (simulation)" if args.dry_run else "EXECUTION"
    print(f"\n🧹 Running Retention Policy [{mode_label}]...")

    res = rm.apply_retention(newsletter_id=args.newsletter, dry_run=args.dry_run)

    if res.pruned_count == 0:
        print("✨ No issues required retention pruning. Everything is within limits.")
        return

    print(f"Pruned {res.pruned_count} issue(s), reclaimed {_format_bytes(res.bytes_reclaimed)} of disk space:")
    for p in res.pruned_issues:
        print(f"  - [{p.newsletter_slug}] #{p.series_index} '{p.subject}' ({_format_bytes(p.size_bytes)}) -> {p.reason}")

    if args.dry_run:
        print("\n💡 This was a dry run. No files or database records were modified.")


def cmd_storage_check(args: argparse.Namespace) -> None:
    db = _get_db()
    fs = FileStore()

    print("\n🔍 Checking DB and Filesystem Consistency...")
    res = fs.check_consistency(db)

    if res["consistent"]:
        print("✅ Storage is 100% consistent! No orphan files and no missing records.")
    else:
        if res["missing_on_disk"]:
            print(f"⚠️  {len(res['missing_on_disk'])} issue(s) marked 'done' missing physical file on disk:")
            for m in res["missing_on_disk"]:
                print(f"   - Issue ID: {m['issue_id']} ('{m['subject']}') -> {m['expected_path']}")
        if res["orphan_files"]:
            print(f"⚠️  {len(res['orphan_files'])} orphan file(s) found on disk not tracked in database:")
            for o in res["orphan_files"]:
                print(f"   - {o}")


def _get_uvicorn_log_config() -> dict:
    import copy

    from uvicorn.config import LOGGING_CONFIG

    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    log_config["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    log_config["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    )
    log_config["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    log_config["loggers"]["crosspoint_newsletter"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    return log_config


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    host = args.host or config.OPDS_HOST
    port = args.port or config.OPDS_PORT
    print(f"\n🚀 Starting CrossPoint OPDS Server on http://{host}:{port}/opds ...")
    uvicorn.run(
        "crosspoint_newsletter.serve.opds_app:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
        log_config=_get_uvicorn_log_config(),
    )


def cmd_pipeline_run(args: argparse.Namespace) -> None:

    from pathlib import Path

    from crosspoint_newsletter.ingest.email_client import EmailIngestClient
    from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
    from crosspoint_newsletter.transform.pipeline import TransformPipeline

    db = _get_db()
    matcher = NewsletterMatcher(db=db)
    client = EmailIngestClient(matcher=matcher)
    pipeline = TransformPipeline(db=db)

    items_to_process = []

    local_eml = getattr(args, "local_eml", None)
    if local_eml:
        eml_target = Path(local_eml)
        if not eml_target.exists():
            print(f"❌ File/cartella EML non trovata: {eml_target}")
            sys.exit(1)
        eml_files = sorted(eml_target.glob("*.eml")) if eml_target.is_dir() else [eml_target]
        print(f"\n📂 Modalità locale: elaborazione di {len(eml_files)} file .eml...")
        for eml_file in eml_files:
            raw = client.process_email_bytes(eml_file.read_bytes())
            if raw:
                items_to_process.append(raw)
            else:
                print(f"⚠️  File '{eml_file.name}' non associato a nessuna newsletter registrata.")
    else:
        if not config.IMAP_HOST or not config.IMAP_USER or not config.IMAP_PASSWORD:
            print("\n⚠️  Credenziali IMAP non configurate.")
            print("Configura il file .env oppure imposta i parametri dalla WebUI (http://localhost:8400/ui/settings/).")
            print("In alternativa puoi testare un file EML locale con: cn pipeline run --local-eml <path.eml>")
            sys.exit(1)

        print(f"\n📥 Connessione a IMAP {config.IMAP_HOST}:{config.IMAP_PORT} (cartella '{config.IMAP_FOLDER}')...")
        mark_seen = not getattr(args, "no_mark_seen", False)
        limit = getattr(args, "limit", None)
        items_to_process = client.fetch_unseen(mark_seen=mark_seen, limit=limit)
        if not items_to_process:
            print("✨ Nessuna nuova email non letta trovata nella casella.")
            return

    print(f"\n⚙️  Avvio conversione per {len(items_to_process)} issue registrata/e...")
    success_count = 0
    error_count = 0

    for raw in items_to_process:
        print(f"  → In elaborazione: [{raw.newsletter_name}] #{raw.series_index} - '{raw.subject}'")
        epub_path = pipeline.process_raw_content(raw)
        if epub_path and epub_path.exists():
            size_str = _format_bytes(epub_path.stat().st_size)
            print(f"     ✅ EPUB generato: {epub_path.name} ({size_str})")
            success_count += 1
        else:
            print(f"     ❌ Fallita generazione EPUB per issue #{raw.series_index}")
            error_count += 1

    print("\n" + "=" * 60)
    print(f"🏁 Pipeline completata: {success_count} successo/i, {error_count} errore/i.")
    print("=" * 60)


def cmd_inbox_list(args: argparse.Namespace) -> None:
    db = _get_db()
    items = db.list_inbox_emails(limit=args.limit)
    if not items:
        print("✨ Nessuna newsletter in attesa di approvazione nella coda di triage.")
        return

    print(f"\n📬 NEWSLETTER RILEVATE IN ATTESA DI APPROVAZIONE ({len(items)} email)")
    print(f"{'ID':<38} {'MITTENTE':<25} {'EMAIL':<30} {'OGGETTO':<35}")
    print("-" * 130)
    for item in items:
        subj_str = item.subject[:32] + "..." if len(item.subject) > 32 else item.subject
        print(f"{item.id:<38} {item.sender_name[:23]:<25} {item.sender_email[:28]:<30} {subj_str:<35}")
    print("-" * 130)
    print("💡 Per approvare: cn inbox approve <id> [--name NAME] [--slug SLUG]")
    print("💡 Per scartare:  cn inbox dismiss <id>\n")


def cmd_inbox_approve(args: argparse.Namespace) -> None:
    from pathlib import Path

    from crosspoint_newsletter.ingest.email_client import EmailIngestClient
    from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
    from crosspoint_newsletter.transform.pipeline import TransformPipeline

    db = _get_db()
    item = db.get_inbox_email(args.id)
    if not item:
        print(f"❌ Email non trovata in inbox con ID: {args.id}")
        sys.exit(1)

    nl_name = args.name or item.sender_name or "Newsletter"
    nl_slug = args.slug or nl_name.lower().replace(" ", "-").replace("'", "")

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
        print(f"✅ Newsletter '{nl.name}' registrata (slug: {nl.slug}).")
    else:
        nl = existing_nl
        print(f"ℹ️  Newsletter già esistente '{nl.name}' utilizzata.")

    created_epub = None
    if item.raw_eml_path and Path(item.raw_eml_path).exists():
        raw_bytes = Path(item.raw_eml_path).read_bytes()
        matcher = NewsletterMatcher(db=db)
        client = EmailIngestClient(matcher=matcher)
        pipeline = TransformPipeline(db=db)

        raw_content = client.process_email_bytes(raw_bytes)
        if raw_content:
            created_epub = pipeline.process_raw_content(raw_content)

    db.delete_inbox_email(args.id)

    if created_epub and created_epub.exists():
        print(f"✅ EPUB generato e disponibile su OPDS: {created_epub.name} ({_format_bytes(created_epub.stat().st_size)})")
    else:
        print("⚠️  Email approvata ma nessun file EPUB generato (file raw non trovato).")


def cmd_inbox_dismiss(args: argparse.Namespace) -> None:
    from pathlib import Path

    db = _get_db()
    item = db.get_inbox_email(args.id)
    if not item:
        print(f"❌ Email non trovata in inbox con ID: {args.id}")
        sys.exit(1)

    if item.raw_eml_path:
        Path(item.raw_eml_path).unlink(missing_ok=True)
    db.delete_inbox_email(args.id)
    print(f"🗑️ Email '{item.subject}' da {item.sender_email} scartata ed eliminata.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cn",
        description="CrossPoint Newsletter Server CLI — Catalog and Storage Management",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # pipeline and ingest commands
    for cmd_name in ("pipeline", "ingest"):
        p_parser = subparsers.add_parser(cmd_name, help="Run ingestion and transformation pipeline")
        p_sub = p_parser.add_subparsers(dest="action", required=True)
        p_run = p_sub.add_parser("run", help="Fetch emails from IMAP or local EML and convert to EPUB")
        p_run.add_argument("--local-eml", help="Process a local .eml file or directory of .eml")
        p_run.add_argument("--limit", type=int, help="Max unseen emails to fetch from IMAP")
        p_run.add_argument("--no-mark-seen", action="store_true", help="Do not mark emails as \\Seen on IMAP")
        p_run.set_defaults(func=cmd_pipeline_run)

    # inbox / discovery
    inbox_parser = subparsers.add_parser("inbox", help="Manage unassigned emails waiting for approval")
    inbox_sub = inbox_parser.add_subparsers(dest="action", required=True)

    inbox_list_cmd = inbox_sub.add_parser("list", help="List unassigned emails in triage queue")
    inbox_list_cmd.add_argument("--limit", type=int, default=30, help="Max emails to show")
    inbox_list_cmd.set_defaults(func=cmd_inbox_list)

    inbox_approve_cmd = inbox_sub.add_parser("approve", help="Approve an email, register newsletter and build EPUB")
    inbox_approve_cmd.add_argument("id", help="ID of the inbox email entry to approve")
    inbox_approve_cmd.add_argument("--name", help="Custom newsletter name")
    inbox_approve_cmd.add_argument("--slug", help="Custom newsletter slug")
    inbox_approve_cmd.set_defaults(func=cmd_inbox_approve)

    inbox_dismiss_cmd = inbox_sub.add_parser("dismiss", help="Dismiss and delete an unassigned email")
    inbox_dismiss_cmd.add_argument("id", help="ID of the inbox email entry to dismiss")
    inbox_dismiss_cmd.set_defaults(func=cmd_inbox_dismiss)

    # newsletter
    nl_parser = subparsers.add_parser("newsletter", help="Manage newsletter publications")
    nl_sub = nl_parser.add_subparsers(dest="action", required=True)

    nl_list = nl_sub.add_parser("list", help="List registered newsletters")
    nl_list.set_defaults(func=cmd_newsletter_list)

    nl_add = nl_sub.add_parser("add", help="Add or update a newsletter")
    nl_add.add_argument("name", help="Display name of the newsletter")
    nl_add.add_argument("--sender", required=True, help="Sender email address")
    nl_add.add_argument("--slug", help="Custom slug (defaults to slugified name)")
    nl_add.add_argument("--max-issues", type=int, help="Maximum issues to retain")
    nl_add.add_argument("--max-age-days", type=int, help="Maximum age in days to retain")
    nl_add.set_defaults(func=cmd_newsletter_add)

    nl_update = nl_sub.add_parser("update", help="Update newsletter settings and retention")
    nl_update.add_argument("slug_or_id", help="Newsletter slug or ID")
    nl_update.add_argument("--name", help="New display name")
    nl_update.add_argument("--sender", help="New sender email")
    nl_update.add_argument("--max-issues", type=int, help="Maximum issues to retain (0 for unlimited)")
    nl_update.add_argument("--unlimited-issues", action="store_true", help="Set issues retention to unlimited")
    nl_update.add_argument("--max-age-days", type=int, help="Maximum age in days to retain (0 for unlimited)")
    nl_update.add_argument("--unlimited-age", action="store_true", help="Set age retention to unlimited")
    nl_update.add_argument("--enable", action="store_true", help="Enable newsletter")
    nl_update.add_argument("--disable", action="store_true", help="Disable newsletter")
    nl_update.add_argument("--apply-retention", action="store_true", help="Run retention cleanup immediately")
    nl_update.set_defaults(func=cmd_newsletter_update)

    # issue
    issue_parser = subparsers.add_parser("issue", help="Manage and inspect newsletter issues")
    issue_sub = issue_parser.add_subparsers(dest="action", required=True)

    issue_list = issue_sub.add_parser("list", help="List newsletter issues")
    issue_list.add_argument("--newsletter", help="Filter by newsletter slug or id")
    issue_list.add_argument("--status", choices=["pending", "processing", "done", "error"], help="Filter by status")
    issue_list.add_argument("--limit", type=int, default=30, help="Max issues to show (default 30)")
    issue_list.set_defaults(func=cmd_issue_list)

    issue_stats = issue_sub.add_parser("stats", help="Show global catalog and storage statistics")
    issue_stats.set_defaults(func=cmd_issue_stats)

    # retention
    ret_parser = subparsers.add_parser("retention", help="Manage retention and cleanup")
    ret_sub = ret_parser.add_subparsers(dest="action", required=True)

    ret_run = ret_sub.add_parser("run", help="Run retention cleanup")
    ret_run.add_argument("--newsletter", help="Run retention only for specific slug")
    ret_run.add_argument("--dry-run", action="store_true", help="Simulate without deleting files/records")
    ret_run.set_defaults(func=cmd_retention_run)

    # storage
    st_parser = subparsers.add_parser("storage", help="Inspect and audit storage")
    st_sub = st_parser.add_subparsers(dest="action", required=True)

    st_check = st_sub.add_parser("check", help="Check consistency between DB and disk files")
    st_check.set_defaults(func=cmd_storage_check)

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the OPDS catalog server")
    serve_parser.add_argument(
        "--host", default=config.OPDS_HOST, help=f"Host to bind (default: {config.OPDS_HOST})"
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=config.OPDS_PORT,
        help=f"Port to bind (default: {config.OPDS_PORT})",
    )
    serve_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload for development"
    )
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main() -> None:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()



if __name__ == "__main__":
    main()
