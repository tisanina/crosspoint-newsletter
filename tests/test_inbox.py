"""Unit and integration tests for Inbox Triage and unassigned newsletter discovery."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crosspoint_newsletter.ingest.email_client import EmailIngestClient, extract_sender_name
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
from crosspoint_newsletter.serve.opds_app import create_app
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import UnmatchedEmail

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_extract_sender_name() -> None:
    assert extract_sender_name("Gameromancer <gameromancer@substack.com>") == "Gameromancer"
    assert extract_sender_name('"The Daily Tech" <daily@tech.com>') == "The Daily Tech"
    assert extract_sender_name("newsletter@substack.com") == "Newsletter"


def test_database_inbox_crud(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    now = datetime.now(UTC)

    assert db.count_inbox_emails() == 0

    item = UnmatchedEmail(
        id="inbox-1",
        email_message_id="msg-1@unknown.com",
        sender_header="Unknown Creator <unknown@creator.com>",
        sender_email="unknown@creator.com",
        sender_name="Unknown Creator",
        subject="Issue #1 - Hello World",
        received_at=now,
        raw_eml_path=str(tmp_path / "raw.eml"),
    )

    # 1. Add
    assert db.add_inbox_email(item) is True
    assert db.count_inbox_emails() == 1

    # Deduplicate insert
    assert db.add_inbox_email(item) is False

    # 2. Get & List
    fetched = db.get_inbox_email("inbox-1")
    assert fetched is not None
    assert fetched.sender_name == "Unknown Creator"
    assert fetched.subject == "Issue #1 - Hello World"

    items = db.list_inbox_emails()
    assert len(items) == 1
    assert items[0].id == "inbox-1"

    # 3. Delete
    assert db.delete_inbox_email("inbox-1") is True
    assert db.count_inbox_emails() == 0
    assert db.get_inbox_email("inbox-1") is None


def test_ingest_saves_unmatched_to_inbox(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    matcher = NewsletterMatcher(db=db)
    client = EmailIngestClient(
        matcher=matcher,
        keep_raw=True,
        raw_dir=tmp_path / "raw",
    )

    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    # The fixture has sender "The Bull <editor@thebull.example.com>" which is not in db
    raw_content = client.process_email_bytes(eml_file.read_bytes())

    # Not matched to any configured newsletter
    assert raw_content is None

    # But saved in inbox triage!
    assert db.count_inbox_emails() == 1
    inbox_items = db.list_inbox_emails()
    assert len(inbox_items) == 1
    assert inbox_items[0].sender_email == "newsletter@thebull.example.com"
    assert "The Bull" in inbox_items[0].sender_name
    assert Path(inbox_items[0].raw_eml_path).exists()


def test_inbox_api_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")
    monkeypatch.setattr("crosspoint_newsletter.config.EPUB_DIR", tmp_path / "epub")

    db = Database(tmp_path / "test.db")
    fs = FileStore(base_dir=tmp_path)
    app = create_app(db=db, file_store=fs)
    client = TestClient(app)

    # Ingest unmatched
    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    ingest = EmailIngestClient(
        matcher=NewsletterMatcher(db=db),
        keep_raw=True,
        raw_dir=tmp_path / "raw",
    )
    ingest.process_email_bytes(eml_file.read_bytes())

    # 1. API list
    resp = client.get("/api/inbox")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    inbox_id = items[0]["id"]
    assert items[0]["sender_name"] == "The Bull"

    # 2. API approve
    resp_approve = client.post(
        f"/api/inbox/{inbox_id}/approve",
        json={"name": "The Bull Approver", "slug": "the-bull-approved"},
    )
    assert resp_approve.status_code == 200
    data_app = resp_approve.json()
    assert data_app["success"] is True
    assert db.count_inbox_emails() == 0

    # Newsletter is created and issue is done
    nl = db.get_newsletter_by_slug("the-bull-approved")
    assert nl is not None
    assert nl.name == "The Bull Approver"
    issues = db.list_issues_by_newsletter(nl.id)
    assert len(issues) == 1
    assert issues[0].status == "done"


def test_inbox_webui_pages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")
    monkeypatch.setattr("crosspoint_newsletter.config.EPUB_DIR", tmp_path / "epub")

    db = Database(tmp_path / "test.db")
    fs = FileStore(base_dir=tmp_path)
    app = create_app(db=db, file_store=fs)
    client = TestClient(app)

    # Unmatched email
    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    ingest = EmailIngestClient(
        matcher=NewsletterMatcher(db=db),
        keep_raw=True,
        raw_dir=tmp_path / "raw",
    )
    ingest.process_email_bytes(eml_file.read_bytes())

    # 1. WebUI Inbox list
    resp_ui = client.get("/ui/inbox")
    assert resp_ui.status_code == 200
    assert "Nuove Newsletter Rilevate" in resp_ui.text
    assert "Approva con 1-Click" in resp_ui.text

    # 2. Dismiss WebUI
    inbox_item = db.list_inbox_emails()[0]
    resp_dismiss = client.post(
        f"/ui/inbox/{inbox_item.id}/dismiss",
        follow_redirects=True,
    )
    assert resp_dismiss.status_code == 200
    assert "scartata e rimossa" in resp_dismiss.text
    assert db.count_inbox_emails() == 0
