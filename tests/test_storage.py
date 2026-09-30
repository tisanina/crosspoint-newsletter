"""Unit tests for storage components: Database extensions, FileStore, RetentionManager, and CLI."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crosspoint_newsletter.cli import (
    cmd_issue_list,
    cmd_issue_stats,
    cmd_newsletter_add,
    cmd_newsletter_list,
    cmd_retention_run,
    cmd_storage_check,
)
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Issue, Newsletter
from crosspoint_newsletter.storage.retention import RetentionManager


def _create_sample_newsletter(db: Database, slug: str = "the-bull", max_issues: int | None = None, max_age: int | None = None) -> Newsletter:
    nl = Newsletter(
        id=f"nl-{slug}",
        name=f"Newsletter {slug.title()}",
        slug=slug,
        sender_email=f"editor@{slug}.example.com",
        retention_max_issues=max_issues,
        retention_max_age_days=max_age,
    )
    db.upsert_newsletter(nl)
    return nl


def _create_sample_issue(
    db: Database,
    nl_id: str,
    index: int,
    status: str = "done",
    received_at: datetime | None = None,
    epub_path: str | None = None,
) -> Issue:
    rec = received_at or datetime.now(UTC)
    issue = Issue(
        id=f"issue-{nl_id}-{index}",
        newsletter_id=nl_id,
        email_message_id=f"msg-{nl_id}-{index}@example.com",
        subject=f"Issue #{index} Topic Analysis",
        received_at=rec,
        series_index=index,
        status=status,
        epub_path=epub_path,
    )
    return db.create_issue(issue)


def test_database_crud_and_cascade_delete(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")

    nl = _create_sample_newsletter(db, "test-nl")
    _create_sample_issue(db, nl.id, 1)
    _create_sample_issue(db, nl.id, 2)

    assert db.get_newsletter_by_id(nl.id) is not None
    assert db.get_newsletter_by_slug("test-nl") is not None
    assert len(db.list_issues_by_newsletter(nl.id)) == 2

    # Delete single issue
    deleted = db.delete_issue(f"issue-{nl.id}-1")
    assert deleted
    assert db.get_issue(f"issue-{nl.id}-1") is None
    assert len(db.list_issues_by_newsletter(nl.id)) == 1

    # Delete newsletter with cascade
    nl_deleted = db.delete_newsletter(nl.id)
    assert nl_deleted
    assert db.get_newsletter_by_id(nl.id) is None
    # All cascaded issues must be gone
    assert len(db.list_issues_by_newsletter(nl.id)) == 0


def test_database_feed_query_and_search(tmp_path: Path) -> None:
    db = Database(tmp_path / "test_feed.db")
    nl = _create_sample_newsletter(db, "alpha")

    _create_sample_issue(db, nl.id, 1, status="done")
    _create_sample_issue(db, nl.id, 2, status="pending")  # not in feed
    _create_sample_issue(db, nl.id, 3, status="done")
    _create_sample_issue(db, nl.id, 4, status="error")    # not in feed

    feed_issues = db.get_issues_for_feed(nl.id)
    assert len(feed_issues) == 2
    # Must be ordered by series_index ascending (OPDS order)
    assert [i.series_index for i in feed_issues] == [1, 3]

    # Keyword search
    results = db.search_issues("Topic")
    assert len(results) == 4

    results_none = db.search_issues("nonexistent-keyword")
    assert len(results_none) == 0


def test_file_store_operations_and_disk_usage(tmp_path: Path) -> None:
    fs = FileStore(base_dir=tmp_path / "data")

    # Save EPUB
    dummy_bytes = b"fake epub content 12345"
    saved_path = fs.save_epub("tech-radar", "tech-radar-001.epub", dummy_bytes)

    assert saved_path.exists()
    assert fs.get_epub_path("tech-radar", "tech-radar-001.epub") == saved_path

    # Check disk usage
    usage = fs.get_disk_usage("tech-radar")
    assert usage["total_files"] == 1
    assert usage["total_bytes"] == len(dummy_bytes)

    # Delete EPUB
    assert fs.delete_epub(saved_path)
    assert not saved_path.exists()
    assert not fs.delete_epub(saved_path)  # safe second delete


def test_file_store_consistency_check(tmp_path: Path) -> None:
    db = Database(tmp_path / "test_cons.db")
    fs = FileStore(base_dir=tmp_path / "data")

    nl = _create_sample_newsletter(db, "consistent-nl")

    # 1. Issue with real file on disk
    real_path = fs.save_epub("consistent-nl", "consistent-nl-001.epub", b"content")
    _create_sample_issue(db, nl.id, 1, status="done", epub_path=str(real_path))

    # 2. Issue marked done but missing file on disk
    _create_sample_issue(db, nl.id, 2, status="done", epub_path=str(tmp_path / "data" / "epub" / "consistent-nl" / "missing.epub"))

    # 3. Orphan file on disk not in DB
    fs.save_epub("consistent-nl", "orphan-999.epub", b"orphan content")

    audit = fs.check_consistency(db)
    assert not audit["consistent"]
    assert len(audit["missing_on_disk"]) == 1
    assert audit["missing_on_disk"][0]["issue_id"] == f"issue-{nl.id}-2"
    assert len(audit["orphan_files"]) == 1
    assert "orphan-999.epub" in audit["orphan_files"][0]


def test_retention_by_max_issues(tmp_path: Path) -> None:
    db = Database(tmp_path / "test_ret.db")
    fs = FileStore(base_dir=tmp_path / "data")
    rm = RetentionManager(db=db, file_store=fs)

    # Max issues set to 3
    nl = _create_sample_newsletter(db, "high-volume", max_issues=3)

    # Create 5 issues with physical files
    for idx in range(1, 6):
        epub_file = fs.save_epub("high-volume", f"high-volume-{idx:03d}.epub", b"epub data")
        _create_sample_issue(db, nl.id, idx, status="done", epub_path=str(epub_file))

    assert len(db.list_issues_by_newsletter(nl.id)) == 5

    # 1. Dry run test (must report 2 pruned, but touch nothing)
    dry_res = rm.apply_retention(newsletter_id=nl.id, dry_run=True)
    assert dry_res.dry_run is True
    assert dry_res.pruned_count == 2
    assert len(db.list_issues_by_newsletter(nl.id)) == 5
    assert fs.get_epub_path("high-volume", "high-volume-001.epub") is not None

    # 2. Real execution
    res = rm.apply_retention(newsletter_id=nl.id, dry_run=False)
    assert res.pruned_count == 2
    assert res.bytes_reclaimed > 0

    # Newest 3 remain in DB (#3, #4, #5)
    remaining = db.list_issues_by_newsletter(nl.id)
    assert len(remaining) == 3
    assert [i.series_index for i in remaining] == [5, 4, 3]

    # Files #1 and #2 must be deleted from disk
    assert fs.get_epub_path("high-volume", "high-volume-001.epub") is None
    assert fs.get_epub_path("high-volume", "high-volume-002.epub") is None
    assert fs.get_epub_path("high-volume", "high-volume-003.epub") is not None


def test_retention_by_max_age_days(tmp_path: Path) -> None:
    db = Database(tmp_path / "test_age.db")
    fs = FileStore(base_dir=tmp_path / "data")
    rm = RetentionManager(db=db, file_store=fs)

    nl = _create_sample_newsletter(db, "daily-digest", max_age=10)

    now = datetime.now(UTC)
    old_date = now - timedelta(days=20)
    fresh_date = now - timedelta(days=2)

    # Create 1 expired issue and 1 fresh issue
    f1 = fs.save_epub("daily-digest", "daily-digest-001.epub", b"data1")
    _create_sample_issue(db, nl.id, 1, status="done", received_at=old_date, epub_path=str(f1))

    f2 = fs.save_epub("daily-digest", "daily-digest-002.epub", b"data2")
    _create_sample_issue(db, nl.id, 2, status="done", received_at=fresh_date, epub_path=str(f2))

    res = rm.apply_retention(newsletter_id=nl.id, dry_run=False)
    assert res.pruned_count == 1
    assert res.pruned_issues[0].series_index == 1

    remaining = db.list_issues_by_newsletter(nl.id)
    assert len(remaining) == 1
    assert remaining[0].series_index == 2
    assert not f1.exists()
    assert f2.exists()


def test_cli_subcommands(monkeypatch, tmp_path: Path, capsys) -> None:
    # Patch config to use tmp_path
    db_file = tmp_path / "cli_test.db"
    monkeypatch.setattr("crosspoint_newsletter.config.DB_PATH", db_file)
    monkeypatch.setattr("crosspoint_newsletter.config.DATA_DIR", tmp_path)

    # 1. newsletter add
    args_add = argparse.Namespace(name="The Morning Dispatch", sender="morning@dispatch.com", slug="morning-dispatch", max_issues=50, max_age_days=90)
    cmd_newsletter_add(args_add)
    captured = capsys.readouterr()
    assert "added/updated successfully" in captured.out

    # 2. newsletter list
    args_list = argparse.Namespace()
    cmd_newsletter_list(args_list)
    captured = capsys.readouterr()
    assert "morning-dispatch" in captured.out

    # 3. issue stats
    args_stats = argparse.Namespace()
    cmd_issue_stats(args_stats)
    captured = capsys.readouterr()
    assert "Registered Newsletters: 1" in captured.out

    # 4. retention run (dry run)
    args_ret = argparse.Namespace(newsletter="morning-dispatch", dry_run=True)
    cmd_retention_run(args_ret)
    captured = capsys.readouterr()
    assert "No issues required retention" in captured.out

    # 5. storage check
    args_chk = argparse.Namespace()
    cmd_storage_check(args_chk)
    captured = capsys.readouterr()
    assert "consistent" in captured.out.lower()
