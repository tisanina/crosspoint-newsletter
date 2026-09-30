"""Minimal SQLite database layer for newsletter and issue tracking."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from crosspoint_newsletter.storage.models import BlacklistEntry, Issue, Newsletter, UnmatchedEmail


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


class Database:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS newsletters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    sender_email TEXT NOT NULL,
                    sender_rules TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    retention_max_issues INTEGER,
                    retention_max_age_days INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS issues (
                    id TEXT PRIMARY KEY,
                    newsletter_id TEXT NOT NULL,
                    email_message_id TEXT UNIQUE NOT NULL,
                    subject TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    processed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    epub_path TEXT,
                    raw_path TEXT,
                    series_index INTEGER NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    calibre_synced INTEGER NOT NULL DEFAULT 0,
                    error_detail TEXT,
                    FOREIGN KEY (newsletter_id) REFERENCES newsletters (id) ON DELETE CASCADE,
                    UNIQUE (newsletter_id, series_index)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbox (
                    id TEXT PRIMARY KEY,
                    email_message_id TEXT UNIQUE NOT NULL,
                    sender_header TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    raw_eml_path TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    id TEXT PRIMARY KEY,
                    sender_email TEXT UNIQUE NOT NULL,
                    sender_name TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );
            """)


    def upsert_newsletter(self, nl: Newsletter) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO newsletters (
                    id, name, slug, sender_email, sender_rules, enabled,
                    retention_max_issues, retention_max_age_days, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name = excluded.name,
                    sender_email = excluded.sender_email,
                    sender_rules = excluded.sender_rules,
                    enabled = excluded.enabled,
                    retention_max_issues = excluded.retention_max_issues,
                    retention_max_age_days = excluded.retention_max_age_days,
                    updated_at = excluded.updated_at
            """, (
                nl.id,
                nl.name,
                nl.slug,
                nl.sender_email,
                json.dumps(nl.sender_rules),
                1 if nl.enabled else 0,
                nl.retention_max_issues,
                nl.retention_max_age_days,
                nl.created_at.isoformat(),
                nl.updated_at.isoformat(),
            ))

    def get_newsletter_by_sender(self, sender: str) -> Newsletter | None:
        clean_sender = sender.strip().lower()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM newsletters WHERE LOWER(sender_email) = ? AND enabled = 1",
                (clean_sender,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_newsletter(row)

    def get_all_newsletters(self) -> list[Newsletter]:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM newsletters ORDER BY name ASC")
            return [self._row_to_newsletter(r) for r in cursor.fetchall()]

    def issue_exists(self, email_message_id: str) -> bool:
        if not email_message_id:
            return False
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM issues WHERE email_message_id = ?",
                (email_message_id.strip(),),
            )
            return cursor.fetchone() is not None

    def get_next_series_index(self, newsletter_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT MAX(series_index) AS max_idx FROM issues WHERE newsletter_id = ?",
                (newsletter_id,),
            )
            row = cursor.fetchone()
            if row and row["max_idx"] is not None:
                return int(row["max_idx"]) + 1
            return 1

    def create_issue(self, issue: Issue) -> Issue:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO issues (
                    id, newsletter_id, email_message_id, subject, received_at,
                    processed_at, status, epub_path, raw_path, series_index,
                    metadata, calibre_synced, error_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                issue.id,
                issue.newsletter_id,
                issue.email_message_id.strip(),
                issue.subject,
                issue.received_at.isoformat(),
                issue.processed_at.isoformat() if issue.processed_at else None,
                issue.status,
                issue.epub_path,
                issue.raw_path,
                issue.series_index,
                json.dumps(issue.metadata),
                1 if issue.calibre_synced else 0,
                issue.error_detail,
            ))
        return issue

    def update_issue_status(
        self,
        issue_id: str,
        status: str,
        epub_path: str | None = None,
        error_detail: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata) if metadata is not None else None
        with self._get_connection() as conn:
            if meta_json is not None:
                conn.execute("""
                    UPDATE issues SET
                        status = ?,
                        epub_path = COALESCE(?, epub_path),
                        error_detail = ?,
                        processed_at = ?,
                        metadata = ?
                    WHERE id = ?
                """, (status, epub_path, error_detail, now, meta_json, issue_id))
            else:
                conn.execute("""
                    UPDATE issues SET
                        status = ?,
                        epub_path = COALESCE(?, epub_path),
                        error_detail = ?,
                        processed_at = ?
                    WHERE id = ?
                """, (status, epub_path, error_detail, now, issue_id))

    def get_newsletter_by_id(self, newsletter_id: str) -> Newsletter | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM newsletters WHERE id = ?", (newsletter_id,))
            row = cursor.fetchone()
            return self._row_to_newsletter(row) if row else None

    def get_newsletter_by_slug(self, slug: str) -> Newsletter | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM newsletters WHERE slug = ?", (slug,))
            row = cursor.fetchone()
            return self._row_to_newsletter(row) if row else None

    def delete_newsletter(self, newsletter_id: str) -> bool:
        """Delete a newsletter and all its issues via foreign key cascade."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM newsletters WHERE id = ?", (newsletter_id,))
            return cursor.rowcount > 0

    def delete_issue(self, issue_id: str) -> bool:
        """Delete an individual issue from the database."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
            return cursor.rowcount > 0

    def list_issues_by_newsletter(
        self,
        newsletter_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Issue]:
        """List issues for a newsletter ordered by series_index descending."""
        query = "SELECT * FROM issues WHERE newsletter_id = ?"
        params: list[object] = [newsletter_id]

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY series_index DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def list_issues(
        self,
        newsletter_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Issue]:
        """List issues across newsletters with optional filters, search, and pagination."""
        conditions: list[str] = []
        params: list[object] = []

        if newsletter_id:
            conditions.append("newsletter_id = ?")
            params.append(newsletter_id)

        if status:
            conditions.append("status = ?")
            params.append(status)

        if search:
            conditions.append("subject LIKE ?")
            params.append(f"%{search.strip()}%")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM issues {where_clause} ORDER BY received_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def get_issues_for_feed(
        self, newsletter_id: str, limit: int | None = None, descending: bool = False
    ) -> list[Issue]:
        """Get all 'done' issues ordered by series_index for OPDS catalogs."""
        order = "DESC" if descending else "ASC"
        query = f"SELECT * FROM issues WHERE newsletter_id = ? AND status = 'done' ORDER BY series_index {order}"
        params: list[object] = [newsletter_id]
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def get_recent_done_issues(self, limit: int = 30) -> list[Issue]:
        """Get latest 'done' issues across all newsletters ordered by received_at DESC."""
        query = "SELECT * FROM issues WHERE status = 'done' ORDER BY received_at DESC LIMIT ?"
        with self._get_connection() as conn:
            cursor = conn.execute(query, (limit,))
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def search_issues(self, query: str, limit: int = 20) -> list[Issue]:
        """Search issues across newsletters by subject keyword."""
        pattern = f"%{query.strip()}%"
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM issues WHERE subject LIKE ? ORDER BY received_at DESC LIMIT ?",
                (pattern, limit),
            )
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def get_issues_older_than(self, newsletter_id: str, cutoff_date: datetime) -> list[Issue]:
        """Get issues older than a given date, excluding in-flight 'processing' state."""
        cutoff_iso = cutoff_date.isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM issues WHERE newsletter_id = ? AND status != 'processing' AND received_at < ? ORDER BY series_index ASC",
                (newsletter_id, cutoff_iso),
            )
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def get_issues_exceeding_count(self, newsletter_id: str, keep_count: int) -> list[Issue]:
        """Get oldest issues that exceed the maximum retention count (to be pruned)."""
        with self._get_connection() as conn:
            # Select total count of completed/error issues
            cursor = conn.execute(
                "SELECT id FROM issues WHERE newsletter_id = ? AND status != 'processing' ORDER BY series_index DESC",
                (newsletter_id,),
            )
            all_ids = [r["id"] for r in cursor.fetchall()]

        if len(all_ids) <= keep_count:
            return []

        excess_ids = all_ids[keep_count:]
        placeholders = ",".join("?" * len(excess_ids))
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM issues WHERE id IN ({placeholders}) ORDER BY series_index ASC",
                tuple(excess_ids),
            )
            return [self._row_to_issue(r) for r in cursor.fetchall()]

    def get_stats(self) -> dict:
        """Aggregate catalog statistics."""
        with self._get_connection() as conn:
            nl_count = conn.execute("SELECT COUNT(*) AS c FROM newsletters").fetchone()["c"]
            issue_count = conn.execute("SELECT COUNT(*) AS c FROM issues").fetchone()["c"]
            done_count = conn.execute("SELECT COUNT(*) AS c FROM issues WHERE status = 'done'").fetchone()["c"]
            error_count = conn.execute("SELECT COUNT(*) AS c FROM issues WHERE status = 'error'").fetchone()["c"]
            pending_count = conn.execute("SELECT COUNT(*) AS c FROM issues WHERE status = 'pending'").fetchone()["c"]

            per_nl = []
            cursor = conn.execute("""
                SELECT n.id, n.name, n.slug,
                       COUNT(i.id) AS total_issues,
                       SUM(CASE WHEN i.status = 'done' THEN 1 ELSE 0 END) AS done_issues,
                       MAX(i.series_index) AS max_series_index,
                       MAX(i.received_at) AS last_received_at
                FROM newsletters n
                LEFT JOIN issues i ON n.id = i.newsletter_id
                GROUP BY n.id
                ORDER BY n.name ASC
            """)
            for r in cursor.fetchall():
                per_nl.append({
                    "id": r["id"],
                    "name": r["name"],
                    "slug": r["slug"],
                    "total_issues": r["total_issues"] or 0,
                    "done_issues": r["done_issues"] or 0,
                    "max_series_index": r["max_series_index"],
                    "last_received_at": r["last_received_at"],
                })

        return {
            "newsletters_count": nl_count,
            "total_issues": issue_count,
            "done_issues": done_count,
            "error_issues": error_count,
            "pending_issues": pending_count,
            "newsletters": per_nl,
        }

    def get_issue(self, issue_id: str) -> Issue | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_issue(row)

    def _row_to_newsletter(self, row: sqlite3.Row) -> Newsletter:
        now = datetime.now(UTC)
        return Newsletter(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            sender_email=row["sender_email"],
            sender_rules=json.loads(row["sender_rules"] or "[]"),
            enabled=bool(row["enabled"]),
            retention_max_issues=row["retention_max_issues"],
            retention_max_age_days=row["retention_max_age_days"],
            created_at=_parse_iso(row["created_at"]) or now,
            updated_at=_parse_iso(row["updated_at"]) or now,
        )

    def _row_to_issue(self, row: sqlite3.Row) -> Issue:
        now = datetime.now(UTC)
        return Issue(
            id=row["id"],
            newsletter_id=row["newsletter_id"],
            email_message_id=row["email_message_id"],
            subject=row["subject"],
            received_at=_parse_iso(row["received_at"]) or now,
            processed_at=_parse_iso(row["processed_at"]),
            status=row["status"],
            epub_path=row["epub_path"],
            raw_path=row["raw_path"],
            series_index=int(row["series_index"]),
            metadata=json.loads(row["metadata"] or "{}"),
            calibre_synced=bool(row["calibre_synced"]),
            error_detail=row["error_detail"],
        )

    def _row_to_inbox(self, row: sqlite3.Row) -> UnmatchedEmail:
        now = datetime.now(UTC)
        return UnmatchedEmail(
            id=row["id"],
            email_message_id=row["email_message_id"],
            sender_header=row["sender_header"],
            sender_email=row["sender_email"],
            sender_name=row["sender_name"],
            subject=row["subject"],
            received_at=_parse_iso(row["received_at"]) or now,
            raw_eml_path=row["raw_eml_path"],
            created_at=_parse_iso(row["created_at"]) or now,
        )

    def add_inbox_email(self, item: UnmatchedEmail) -> bool:
        """Store an unassigned email in the triage/inbox queue."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO inbox (
                    id, email_message_id, sender_header, sender_email, sender_name,
                    subject, received_at, raw_eml_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.id,
                item.email_message_id,
                item.sender_header,
                item.sender_email,
                item.sender_name,
                item.subject,
                item.received_at.isoformat(),
                item.raw_eml_path,
                item.created_at.isoformat(),
            ))
            return cursor.rowcount > 0

    def list_inbox_emails(self, limit: int = 50) -> list[UnmatchedEmail]:
        """List unassigned emails waiting for approval."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM inbox
                ORDER BY received_at DESC
                LIMIT ?
            """, (limit,))
            return [self._row_to_inbox(row) for row in cursor.fetchall()]

    def get_inbox_email(self, inbox_id: str) -> UnmatchedEmail | None:
        """Get an unassigned email by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,))
            row = cursor.fetchone()
            return self._row_to_inbox(row) if row else None

    def delete_inbox_email(self, inbox_id: str) -> bool:
        """Delete an email from the triage queue."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
            return cursor.rowcount > 0

    def count_inbox_emails(self) -> int:
        """Count unassigned emails waiting for approval."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM inbox")
            return cursor.fetchone()[0]

    def _row_to_blacklist(self, row: sqlite3.Row) -> BlacklistEntry:
        now = datetime.now(UTC)
        return BlacklistEntry(
            id=row["id"],
            sender_email=row["sender_email"],
            sender_name=row["sender_name"],
            reason=row["reason"],
            created_at=_parse_iso(row["created_at"]) or now,
        )

    def add_to_blacklist(
        self,
        sender_email: str,
        sender_name: str | None = None,
        reason: str | None = None,
    ) -> BlacklistEntry:
        """Add an email address to the blacklist (case-insensitive unique)."""
        clean_email = sender_email.strip().lower()
        now = datetime.now(UTC)
        entry_id = str(uuid.uuid4())
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO blacklist (id, sender_email, sender_name, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sender_email) DO UPDATE SET
                    sender_name = COALESCE(excluded.sender_name, blacklist.sender_name),
                    reason = COALESCE(excluded.reason, blacklist.reason)
            """, (
                entry_id,
                clean_email,
                sender_name.strip() if sender_name else None,
                reason.strip() if reason else None,
                now.isoformat(),
            ))
            # Retrieve the created or existing entry
            cursor = conn.execute("SELECT * FROM blacklist WHERE sender_email = ?", (clean_email,))
            return self._row_to_blacklist(cursor.fetchone())

    def is_blacklisted(self, sender_email: str) -> bool:
        """Check if an email address is in the blacklist."""
        if not sender_email:
            return False
        clean_email = sender_email.strip().lower()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM blacklist WHERE LOWER(sender_email) = ?",
                (clean_email,),
            )
            return cursor.fetchone() is not None

    def list_blacklist(self, limit: int = 100) -> list[BlacklistEntry]:
        """List all blacklisted senders ordered by creation date descending."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM blacklist ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [self._row_to_blacklist(r) for r in cursor.fetchall()]

    def get_blacklist_entry(self, id_or_email: str) -> BlacklistEntry | None:
        """Get a blacklist entry by ID or email address."""
        val = id_or_email.strip().lower()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM blacklist WHERE id = ? OR LOWER(sender_email) = ?",
                (id_or_email.strip(), val),
            )
            row = cursor.fetchone()
            return self._row_to_blacklist(row) if row else None

    def remove_from_blacklist(self, id_or_email: str) -> bool:
        """Remove an entry from the blacklist by ID or email address."""
        val = id_or_email.strip().lower()
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM blacklist WHERE id = ? OR LOWER(sender_email) = ?",
                (id_or_email.strip(), val),
            )
            return cursor.rowcount > 0

    def count_blacklist(self) -> int:
        """Count total blacklisted senders."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM blacklist")
            return cursor.fetchone()[0]

