"""Unit and integration tests for OPDS 1.2 catalog server, feed builder and authentication."""

from __future__ import annotations

import base64
import html
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crosspoint_newsletter.serve.opds_app import create_app
from crosspoint_newsletter.serve.opds_builder import (
    EPUB_MIME,
    IMAGE_PNG_MIME,
    OPDS_CATALOG_MIME,
    OpdsFeedBuilder,
)
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Issue, Newsletter


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch):
    """Set up an isolated database and file store with sample newsletters and issues."""
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_USERNAME", "")
    monkeypatch.setattr("crosspoint_newsletter.config.OPDS_PASSWORD", "")
    db_path = tmp_path / "data" / "db" / "newsletter.db"
    db = Database(db_path)
    fs = FileStore(base_dir=tmp_path / "data")

    # 1. Create newsletters
    now = datetime.now(UTC)
    nl_bull = Newsletter(
        id="nl-bull",
        name="The Bull & Co.",
        slug="the-bull",
        sender_email="editor@thebull.com",
        created_at=now,
        updated_at=now,
    )
    nl_storto = Newsletter(
        id="nl-storto",
        name="Storto Magazine",
        slug="storto",
        sender_email="editor@storto.com",
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl_bull)
    db.upsert_newsletter(nl_storto)

    # 2. Create sample EPUB file
    epub_file = fs.save_epub(
        "the-bull", "the-bull-001.epub", b"PK\x03\x04fake-epub-content-for-testing"
    )

    # 3. Create done issue with EPUB
    issue_done = Issue(
        id="issue-001",
        newsletter_id=nl_bull.id,
        email_message_id="msg-001@thebull.com",
        subject="Mercati in fiamme & Tassi d'interesse",
        received_at=now,
        series_index=1,
        status="done",
        epub_path=str(epub_file),
    )
    db.create_issue(issue_done)

    # 4. Create pending issue without EPUB
    issue_pending = Issue(
        id="issue-002",
        newsletter_id=nl_bull.id,
        email_message_id="msg-002@thebull.com",
        subject="Anteprima prossimo numero",
        received_at=now,
        series_index=2,
        status="pending",
    )
    db.create_issue(issue_pending)

    return {
        "db": db,
        "fs": fs,
        "issue_done": issue_done,
        "nl_bull": nl_bull,
        "nl_storto": nl_storto,
    }


class TestOpdsFeedBuilder:
    def test_build_root_catalog_structure_and_escaping(self) -> None:
        builder = OpdsFeedBuilder(base_title="CrossPoint Testing")
        newsletters = [
            {
                "slug": "the-bull",
                "name": "The Bull & Partners <Finance>",
                "done_issues": 5,
                "last_received_at": None,
            }
        ]
        xml = builder.build_root_catalog(newsletters=newsletters, recent_count=3)

        assert '<?xml version="1.0" encoding="utf-8"?>' in xml
        assert '<feed xmlns="http://www.w3.org/2005/Atom"' in xml
        assert 'xmlns:opds="http://opds-spec.org/2010/catalog"' in xml
        assert "<title>CrossPoint Testing</title>" in xml
        assert "<title>Ultime Uscite</title>" in xml
        assert 'href="/opds/recent"' in xml
        # Check XML entity escaping
        assert "The Bull &amp; Partners &lt;Finance&gt;" in xml
        assert 'href="/opds/newsletter/the-bull"' in xml

    def test_build_series_feed(self, test_env) -> None:
        builder = OpdsFeedBuilder()
        nl = test_env["nl_bull"]
        issue = test_env["issue_done"]

        xml = builder.build_series_feed(
            newsletter=nl, issues=[issue], feed_url="/opds/newsletter/the-bull"
        )

        assert f"<title>{html.escape(nl.name)}</title>" in xml
        assert 'rel="http://opds-spec.org/acquisition"' in xml
        assert f'href="/opds/download/{issue.id}"' in xml
        assert f'type="{EPUB_MIME}"' in xml
        assert f'href="/opds/cover/{issue.id}"' in xml
        assert f'type="{IMAGE_PNG_MIME}"' in xml
        assert "Mercati in fiamme &amp; Tassi d&#x27;interesse" in xml


class TestOpdsServerAppUnauthenticated:
    def test_root_redirect(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/opds"

    def test_status_endpoint(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["newsletters_count"] == 2
        assert data["total_issues"] == 2
        assert data["done_issues"] == 1

    def test_get_root_catalog(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        resp = client.get("/opds")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == OPDS_CATALOG_MIME
        assert "The Bull &amp; Co." in resp.text
        assert "Storto Magazine" in resp.text
        assert "/opds/newsletter/the-bull" in resp.text
        assert "/opds/recent" in resp.text

    def test_get_recent_feed(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        resp = client.get("/opds/recent")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == OPDS_CATALOG_MIME
        assert "Mercati in fiamme" in resp.text
        assert f"/opds/download/{test_env['issue_done'].id}" in resp.text

    def test_get_series_feed(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        # Existing newsletter
        resp = client.get("/opds/newsletter/the-bull")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == OPDS_CATALOG_MIME
        assert "The Bull &amp; Co." in resp.text
        assert f"/opds/download/{test_env['issue_done'].id}" in resp.text

        # Non-existing newsletter
        resp_404 = client.get("/opds/newsletter/non-existent")
        assert resp_404.status_code == 404

    def test_download_epub_success_and_404(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        # 1. Successful download
        resp = client.get(f"/opds/download/{test_env['issue_done'].id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == EPUB_MIME
        assert resp.content == b"PK\x03\x04fake-epub-content-for-testing"

        # 2. Download pending issue (not done) -> 404
        resp_pending = client.get("/opds/download/issue-002")
        assert resp_pending.status_code == 404

        # 3. Download unknown issue -> 404
        resp_unknown = client.get("/opds/download/unknown-id")
        assert resp_unknown.status_code == 404

    def test_get_cover_dynamic_generation(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        # No cover file exists on disk yet -> should generate dynamically
        resp = client.get(f"/opds/cover/{test_env['issue_done'].id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == IMAGE_PNG_MIME
        # PNG signature: \x89PNG\r\n\x1a\n
        assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")

    def test_search_feed(self, test_env) -> None:
        app = create_app(db=test_env["db"], file_store=test_env["fs"])
        client = TestClient(app)

        resp = client.get("/opds/search?q=Mercati")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == OPDS_CATALOG_MIME
        assert "Mercati in fiamme" in resp.text

        # Search with no matches
        resp_empty = client.get("/opds/search?q=NotExistingTerm")
        assert resp_empty.status_code == 200
        assert "<entry>" not in resp_empty.text


class TestOpdsServerAuthentication:
    def test_auth_enforced_when_credentials_configured(self, test_env) -> None:
        app = create_app(
            db=test_env["db"],
            file_store=test_env["fs"],
            auth_username="reader",
            auth_password="secretpassword",
        )
        client = TestClient(app)

        # 1. Unauthenticated request to /opds -> 401
        resp = client.get("/opds")
        assert resp.status_code == 401
        assert 'Basic realm="CrossPoint OPDS"' in resp.headers["www-authenticate"]

        # 2. Invalid credentials -> 401
        resp_wrong = client.get("/opds", headers=_basic_auth_header("reader", "wrongpass"))
        assert resp_wrong.status_code == 401

        # 3. Valid credentials -> 200
        resp_ok = client.get("/opds", headers=_basic_auth_header("reader", "secretpassword"))
        assert resp_ok.status_code == 200

        # 4. Download with valid credentials -> 200
        resp_dl = client.get(
            f"/opds/download/{test_env['issue_done'].id}",
            headers=_basic_auth_header("reader", "secretpassword"),
        )
        assert resp_dl.status_code == 200

        # 5. /api/status is exempt from auth (for Docker health checks)
        resp_status = client.get("/api/status")
        assert resp_status.status_code == 200
