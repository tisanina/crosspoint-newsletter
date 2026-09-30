"""Unit and integration tests for REST API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crosspoint_newsletter import config
from crosspoint_newsletter.serve.opds_app import create_app
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Issue, Newsletter


@pytest.fixture
def api_client(tmp_path: Path) -> tuple[TestClient, Database, FileStore]:
    db = Database(tmp_path / "data" / "db" / "newsletter.db")
    fs = FileStore(base_dir=tmp_path / "data")

    now = datetime.now(UTC)
    nl = Newsletter(
        id="nl-test-1",
        name="Tech Weekly",
        slug="tech-weekly",
        sender_email="tech@weekly.com",
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl)

    # Issue
    iss = Issue(
        id="iss-test-1",
        newsletter_id=nl.id,
        email_message_id="mid-123@weekly.com",
        subject="AI Trends 2026",
        received_at=now,
        series_index=1,
        status="done",
    )
    db.create_issue(iss)

    app = create_app(db=db, file_store=fs)
    client = TestClient(app)
    return client, db, fs


class TestNewslettersAPI:
    def test_list_newsletters(self, api_client) -> None:
        client, _, _ = api_client
        resp = client.get("/api/newsletters")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["slug"] == "tech-weekly"
        assert data[0]["name"] == "Tech Weekly"

    def test_create_newsletter(self, api_client) -> None:
        client, db, _ = api_client
        payload = {
            "name": "Finance Daily",
            "sender_email": "editor@financedaily.com",
            "slug": "finance-daily",
            "retention_max_issues": 20,
            "retention_max_age_days": 60,
        }
        resp = client.post("/api/newsletters", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "finance-daily"
        assert data["retention_max_issues"] == 20

        # Duplicate slug conflict
        resp_dup = client.post("/api/newsletters", json=payload)
        assert resp_dup.status_code == 409

    def test_get_and_update_newsletter(self, api_client) -> None:
        client, _, _ = api_client
        # Get by slug
        resp = client.get("/api/newsletters/tech-weekly")
        assert resp.status_code == 200
        assert resp.json()["id"] == "nl-test-1"

        # Update name, enabled and retention
        update_payload = {
            "name": "Tech Weekly Pro",
            "enabled": False,
            "retention_max_issues": 15,
            "retention_max_age_days": 30,
        }
        resp_up = client.put("/api/newsletters/tech-weekly", json=update_payload)
        assert resp_up.status_code == 200
        assert resp_up.json()["name"] == "Tech Weekly Pro"
        assert resp_up.json()["enabled"] is False
        assert resp_up.json()["retention_max_issues"] == 15
        assert resp_up.json()["retention_max_age_days"] == 30

        # Reset retention to null
        resp_reset = client.put("/api/newsletters/tech-weekly", json={"retention_max_issues": None})
        assert resp_reset.status_code == 200
        assert resp_reset.json()["retention_max_issues"] is None

    def test_run_retention_api(self, api_client) -> None:
        client, _, _ = api_client
        resp = client.post("/api/newsletters/tech-weekly/retention?dry_run=true")
        assert resp.status_code == 200
        data = resp.json()
        assert "pruned_count" in data
        assert data["dry_run"] is True

    def test_delete_newsletter(self, api_client) -> None:
        client, db, _ = api_client
        resp = client.delete("/api/newsletters/tech-weekly")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert db.get_newsletter_by_slug("tech-weekly") is None
        assert db.get_issue("iss-test-1") is None


class TestIssuesAPI:
    def test_list_issues(self, api_client) -> None:
        client, _, _ = api_client
        resp = client.get("/api/issues")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["subject"] == "AI Trends 2026"
        assert items[0]["newsletter_name"] == "Tech Weekly"

    def test_list_issues_filter_and_search(self, api_client) -> None:
        client, _, _ = api_client
        resp_found = client.get("/api/issues?search=Trends")
        assert len(resp_found.json()) == 1

        resp_none = client.get("/api/issues?search=NonExistent")
        assert len(resp_none.json()) == 0

        resp_status = client.get("/api/issues?status=done")
        assert len(resp_status.json()) == 1

        resp_pending = client.get("/api/issues?status=pending")
        assert len(resp_pending.json()) == 0

    def test_get_issue_detail(self, api_client) -> None:
        client, _, _ = api_client
        resp = client.get("/api/issues/iss-test-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "iss-test-1"

        resp_404 = client.get("/api/issues/unknown-id")
        assert resp_404.status_code == 404

    def test_reprocess_missing_raw_fails(self, api_client) -> None:
        client, _, _ = api_client
        resp = client.post("/api/issues/iss-test-1/reprocess")
        assert resp.status_code == 400
        assert "raw email file not found" in resp.json()["detail"]


class TestImapDiagnosticsAPI:
    def test_imap_test_endpoint(self, api_client, monkeypatch) -> None:
        client, _, _ = api_client
        monkeypatch.setattr(
            "crosspoint_newsletter.ingest.email_client.EmailIngestClient.test_connection",
            lambda self: (False, "Credenziali incomplete"),
        )
        resp = client.post("/api/imap/test")
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert "message" in data

    def test_update_imap_settings_and_test_with_body(self, api_client, monkeypatch, tmp_path: Path) -> None:
        client, _, _ = api_client
        monkeypatch.setattr("crosspoint_newsletter.config.BASE_DIR", tmp_path)

        orig_host = config.IMAP_HOST
        orig_user = config.IMAP_USER
        orig_pass = config.IMAP_PASSWORD
        orig_folder = config.IMAP_FOLDER
        orig_port = config.IMAP_PORT

        try:
            # Save settings via REST API
            resp_save = client.post(
                "/api/settings/imap",
                json={
                    "host": "imap.custom.org",
                    "port": 993,
                    "user": "custom@custom.org",
                    "password": "mypassword",
                    "folder": "Newsletters",
                },
            )
            assert resp_save.status_code == 200
            data = resp_save.json()
            assert data["success"] is True
            assert data["config"]["host"] == "imap.custom.org"
            assert data["config"]["folder"] == "Newsletters"

            # Test IMAP with custom body (mocked)
            monkeypatch.setattr(
                "crosspoint_newsletter.ingest.email_client.EmailIngestClient.test_connection",
                lambda self: (False, "Test simulato fallito"),
            )
            resp_test = client.post(
                "/api/imap/test",
                json={
                    "host": "127.0.0.1",
                    "port": 9999,
                    "user": "fake@fake.org",
                    "password": "pwd",
                    "folder": "INBOX",
                },
            )
            assert resp_test.status_code == 200
            data_test = resp_test.json()
            assert "success" in data_test
            assert data_test["success"] is False
            assert "Test simulato fallito" in data_test["message"]
        finally:
            config.IMAP_HOST = orig_host
            config.IMAP_USER = orig_user
            config.IMAP_PASSWORD = orig_pass
            config.IMAP_FOLDER = orig_folder
            config.IMAP_PORT = orig_port

    def test_system_status_and_poll_api(self, api_client, monkeypatch) -> None:
        client, _, _ = api_client
        # 1. Test /api/status
        resp_status = client.get("/api/status")
        assert resp_status.status_code == 200
        data = resp_status.json()
        assert data["status"] == "healthy"
        assert "volumes" in data
        assert "conf" in data["volumes"]
        assert "data" in data["volumes"]
        assert "polling" in data

        # 2. Test /api/poll/status
        resp_poll_st = client.get("/api/poll/status")
        assert resp_poll_st.status_code == 200
        assert "is_polling" in resp_poll_st.json()

        # 3. Test /api/poll/run (mocking pipeline run)
        monkeypatch.setattr(
            "crosspoint_newsletter.scheduler.run_pipeline_cycle",
            lambda *args, **kwargs: {
                "success": True,
                "unseen_count": 0,
                "epub_generated": 0,
                "errors": 0,
                "retention_pruned": 0,
            },
        )
        resp_poll_run = client.post("/api/poll/run")
        assert resp_poll_run.status_code == 200
        assert resp_poll_run.json()["status"] == "success"
