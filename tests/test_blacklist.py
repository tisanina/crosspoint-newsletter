"""Unit and integration tests for sender Blacklist management."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from crosspoint_newsletter.ingest.email_client import EmailIngestClient
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
from crosspoint_newsletter.serve.opds_app import create_app
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import UnmatchedEmail

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_database_blacklist_crud(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")

    assert db.count_blacklist() == 0
    assert db.is_blacklisted("spam@domain.com") is False

    # 1. Add
    entry = db.add_to_blacklist(
        sender_email="Spam@Domain.com",
        sender_name="Spam Bot",
        reason="Test reason",
    )
    assert entry.sender_email == "spam@domain.com"
    assert entry.sender_name == "Spam Bot"
    assert entry.reason == "Test reason"
    assert db.count_blacklist() == 1

    # Case-insensitive checks
    assert db.is_blacklisted("spam@domain.com") is True
    assert db.is_blacklisted("SPAM@DOMAIN.COM") is True
    assert db.is_blacklisted("  Spam@Domain.Com  ") is True
    assert db.is_blacklisted("other@domain.com") is False

    # 2. Get and List
    fetched = db.get_blacklist_entry("spam@domain.com")
    assert fetched is not None
    assert fetched.id == entry.id
    assert fetched.sender_name == "Spam Bot"

    items = db.list_blacklist()
    assert len(items) == 1
    assert items[0].id == entry.id

    # 3. Upsert on same email
    entry_updated = db.add_to_blacklist(
        sender_email="spam@domain.com",
        sender_name="Updated Name",
    )
    assert db.count_blacklist() == 1
    assert entry_updated.sender_name == "Updated Name"

    # 4. Remove
    assert db.remove_from_blacklist(entry.id) is True
    assert db.count_blacklist() == 0
    assert db.is_blacklisted("spam@domain.com") is False
    assert db.remove_from_blacklist("spam@domain.com") is False


def test_ingest_skips_blacklisted_sender(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    # Blacklist the sender present in email_html_simple.eml
    db.add_to_blacklist("newsletter@thebull.example.com", sender_name="The Bull")

    raw_dir = tmp_path / "raw"
    matcher = NewsletterMatcher(db=db)
    client = EmailIngestClient(
        matcher=matcher,
        keep_raw=True,
        raw_dir=raw_dir,
    )

    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    raw_content = client.process_email_bytes(eml_file.read_bytes())

    # Should be skipped completely
    assert raw_content is None
    # No inbox entry created
    assert db.count_inbox_emails() == 0
    # No raw file saved in inbox
    inbox_dir = raw_dir / "inbox"
    if inbox_dir.exists():
        assert len(list(inbox_dir.glob("*.eml"))) == 0


def test_block_inbox_email_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")

    db = Database(tmp_path / "test.db")
    fs = FileStore(base_dir=tmp_path)

    raw_eml = tmp_path / "test_raw.eml"
    raw_eml.write_bytes(b"dummy email content")

    item = UnmatchedEmail(
        id="inbox-block-1",
        email_message_id="msg-block-1@spam.com",
        sender_header="Spammer <spammer@bad.com>",
        sender_email="spammer@bad.com",
        sender_name="Spammer",
        subject="Promotional Spam",
        received_at=datetime.now(UTC),
        raw_eml_path=str(raw_eml),
    )
    db.add_inbox_email(item)
    assert db.count_inbox_emails() == 1
    assert db.is_blacklisted("spammer@bad.com") is False

    app = create_app(db=db, file_store=fs)
    client = TestClient(app)

    # Call POST /api/inbox/{id}/block
    resp = client.post("/api/inbox/inbox-block-1/block")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["sender_email"] == "spammer@bad.com"

    # Verify inbox is empty and raw file removed
    assert db.count_inbox_emails() == 0
    assert not raw_eml.exists()

    # Verify sender is now blacklisted
    assert db.is_blacklisted("spammer@bad.com") is True
    bl_entry = db.get_blacklist_entry("spammer@bad.com")
    assert bl_entry is not None
    assert bl_entry.sender_name == "Spammer"


def test_dismiss_with_blacklist_param_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")

    db = Database(tmp_path / "test.db")
    fs = FileStore(base_dir=tmp_path)

    item = UnmatchedEmail(
        id="inbox-param-1",
        email_message_id="msg-param-1@spam.com",
        sender_header="Promo <promo@ad.com>",
        sender_email="promo@ad.com",
        sender_name="Promo",
        subject="Ad 123",
        received_at=datetime.now(UTC),
        raw_eml_path=None,
    )
    db.add_inbox_email(item)

    app = create_app(db=db, file_store=fs)
    client = TestClient(app)

    # Delete with ?blacklist=true
    resp = client.delete("/api/inbox/inbox-param-1?blacklist=true")
    assert resp.status_code == 200
    assert resp.json()["blacklisted"] is True
    assert db.is_blacklisted("promo@ad.com") is True


def test_blacklist_api_endpoints(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")

    db = Database(tmp_path / "test.db")
    fs = FileStore(base_dir=tmp_path)
    app = create_app(db=db, file_store=fs)
    client = TestClient(app)

    # 1. Initially empty
    resp = client.get("/api/blacklist")
    assert resp.status_code == 200
    assert resp.json() == []

    # 2. Add via POST
    resp = client.post(
        "/api/blacklist",
        json={"email": "junk@mail.com", "name": "Junk Sender", "reason": "Unwanted"},
    )
    assert resp.status_code == 201
    entry = resp.json()["entry"]
    assert entry["sender_email"] == "junk@mail.com"
    entry_id = entry["id"]

    # 3. List
    resp = client.get("/api/blacklist")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["sender_email"] == "junk@mail.com"

    # 4. Delete
    resp = client.delete(f"/api/blacklist/{entry_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # Check empty again
    resp = client.get("/api/blacklist")
    assert resp.json() == []

    # Delete 404
    resp = client.delete(f"/api/blacklist/{entry_id}")
    assert resp.status_code == 404


def test_webui_blacklist_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")

    db = Database(tmp_path / "test.db")
    fs = FileStore(base_dir=tmp_path)

    # Create dummy inbox item
    raw_eml = tmp_path / "webui_item.eml"
    raw_eml.write_bytes(b"content")

    item = UnmatchedEmail(
        id="inbox-ui-1",
        email_message_id="msg-ui-1@test.com",
        sender_header="Bad News <bad@news.com>",
        sender_email="bad@news.com",
        sender_name="Bad News",
        subject="Bad Daily",
        received_at=datetime.now(UTC),
        raw_eml_path=str(raw_eml),
    )
    db.add_inbox_email(item)

    app = create_app(db=db, file_store=fs)
    client = TestClient(app)

    # 1. GET /ui/inbox renders page with item and blacklist section
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "Mittenti Bloccati" in resp.text
    assert "bad@news.com" in resp.text
    assert "Blocca" in resp.text

    # 2. POST /ui/inbox/{id}/block
    resp = client.post("/ui/inbox/inbox-ui-1/block", follow_redirects=False)
    assert resp.status_code == 303
    assert db.is_blacklisted("bad@news.com") is True
    assert db.count_inbox_emails() == 0
    assert not raw_eml.exists()

    # 3. Verify blacklist entry shows up in /ui/inbox
    resp = client.get("/ui/inbox")
    assert resp.status_code == 200
    assert "bad@news.com" in resp.text
    bl_entry = db.get_blacklist_entry("bad@news.com")
    assert bl_entry is not None

    # 4. POST /ui/blacklist/add (manual add)
    resp = client.post(
        "/ui/blacklist/add",
        data={"email": "manual@block.com", "name": "Manual", "reason": "Custom"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert db.is_blacklisted("manual@block.com") is True

    # 5. POST /ui/blacklist/{id}/delete (unblock)
    resp = client.post(f"/ui/blacklist/{bl_entry.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert db.is_blacklisted("bad@news.com") is False
