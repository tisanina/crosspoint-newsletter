"""Unit tests for email ingestion pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from crosspoint_newsletter.ingest.email_client import EmailIngestClient
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher
from crosspoint_newsletter.storage.database import Database

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CONFIG_EXAMPLE = Path(__file__).parent.parent / "config" / "newsletters.example.yaml"


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_newsletter.db"
    return Database(db_file)


@pytest.fixture
def configured_matcher(temp_db: Database) -> NewsletterMatcher:
    matcher = NewsletterMatcher(temp_db, config_path=CONFIG_EXAMPLE)
    return matcher


@pytest.fixture
def ingest_client(configured_matcher: NewsletterMatcher, tmp_path: Path) -> EmailIngestClient:
    raw_dir = tmp_path / "raw"
    return EmailIngestClient(
        matcher=configured_matcher,
        keep_raw=True,
        raw_dir=raw_dir,
    )


def test_parse_email_html(ingest_client: EmailIngestClient) -> None:
    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    with open(eml_file, "rb") as f:
        content = ingest_client.process_email_bytes(f.read())

    assert content is not None
    assert content.newsletter_name == "The Bull"
    assert content.newsletter_slug == "the-bull"
    assert "The Bull #1" in content.subject
    assert content.message_id == "bull-issue-001@thebull.example.com"
    assert content.html_body is not None
    assert "I mercati aprono in rialzo" in content.html_body
    assert content.series_index == 1
    assert content.raw_eml_path is not None
    assert content.raw_eml_path.exists()


def test_parse_email_with_cid_images(ingest_client: EmailIngestClient) -> None:
    eml_file = FIXTURES_DIR / "email_html_images.eml"
    with open(eml_file, "rb") as f:
        content = ingest_client.process_email_bytes(f.read())

    assert content is not None
    assert content.newsletter_name == "La Settimana Nat"
    assert "vignetta01" in content.inline_images
    assert len(content.inline_images["vignetta01"]) > 0
    assert content.series_index == 1


def test_parse_email_plain_fallback(ingest_client: EmailIngestClient) -> None:
    eml_file = FIXTURES_DIR / "email_plain_only.eml"
    with open(eml_file, "rb") as f:
        content = ingest_client.process_email_bytes(f.read())

    assert content is not None
    assert content.newsletter_name == "Storto"
    assert content.html_body is None
    assert content.text_body is not None
    assert "Questo testo viene distribuito solo in plain-text" in content.text_body


def test_no_match_skipped(ingest_client: EmailIngestClient, temp_db: Database) -> None:
    eml_file = FIXTURES_DIR / "email_unknown.eml"
    with open(eml_file, "rb") as f:
        content = ingest_client.process_email_bytes(f.read())

    assert content is None
    # Verify no issue was created in DB
    with temp_db._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM issues")
        count = cursor.fetchone()[0]
        assert count == 0


def test_deduplicate_by_message_id(ingest_client: EmailIngestClient, temp_db: Database) -> None:
    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    with open(eml_file, "rb") as f:
        data = f.read()

    # First ingestion: success
    content_first = ingest_client.process_email_bytes(data)
    assert content_first is not None

    # Second ingestion: duplicate skipped
    content_second = ingest_client.process_email_bytes(data)
    assert content_second is None

    # Total issues in DB must remain 1
    with temp_db._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM issues")
        count = cursor.fetchone()[0]
        assert count == 1


def test_raw_not_saved_when_disabled(configured_matcher: NewsletterMatcher, tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    client_no_raw = EmailIngestClient(
        matcher=configured_matcher,
        keep_raw=False,
        raw_dir=raw_dir,
    )

    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    with open(eml_file, "rb") as f:
        content = client_no_raw.process_email_bytes(f.read())

    assert content is not None
    assert content.raw_eml_path is None
    assert not raw_dir.exists() or len(list(raw_dir.glob("**/*.eml"))) == 0


def test_series_index_increments(ingest_client: EmailIngestClient, temp_db: Database) -> None:
    eml_file = FIXTURES_DIR / "email_html_simple.eml"
    with open(eml_file, "rb") as f:
        data = f.read()

    content1 = ingest_client.process_email_bytes(data)
    assert content1 is not None
    assert content1.series_index == 1

    # Ingest a second email for the same newsletter with different Message-ID
    data_issue2 = data.replace(
        b"<bull-issue-001@thebull.example.com>",
        b"<bull-issue-002@thebull.example.com>",
    )
    content2 = ingest_client.process_email_bytes(data_issue2)
    assert content2 is not None
    assert content2.series_index == 2
