"""Unit and integration tests for WebUI pages, HTML rendering, and form actions."""

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
def web_client(tmp_path: Path) -> tuple[TestClient, Database, FileStore]:
    db = Database(tmp_path / "data" / "db" / "newsletter.db")
    fs = FileStore(base_dir=tmp_path / "data")

    now = datetime.now(UTC)
    nl = Newsletter(
        id="nl-web-1",
        name="The Daily Tech",
        slug="the-daily-tech",
        sender_email="tech@daily.com",
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl)

    epub_file = fs.save_epub("the-daily-tech", "the-daily-tech-001.epub", b"fake-epub")
    iss = Issue(
        id="iss-web-1",
        newsletter_id=nl.id,
        email_message_id="mid-daily-1@test",
        subject="Release Day!",
        received_at=now,
        series_index=1,
        status="done",
        epub_path=str(epub_file),
    )
    db.create_issue(iss)

    app = create_app(db=db, file_store=fs)
    client = TestClient(app)
    return client, db, fs


class TestWebUIPages:
    def test_root_redirect_for_browser(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get(
            "/",
            headers={"Accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/ui/"

    def test_dashboard_renders(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Panoramica del Sistema" in resp.text
        assert "The Daily Tech" in resp.text
        assert "Release Day!" in resp.text
        assert "Auto-Polling IMAP" in resp.text

    def test_dashboard_poll_trigger(self, web_client, monkeypatch) -> None:
        client, _, _ = web_client
        monkeypatch.setattr(
            "crosspoint_newsletter.scheduler.run_pipeline_cycle",
            lambda *args, **kwargs: {
                "success": True,
                "unseen_count": 1,
                "epub_generated": 1,
                "errors": 0,
                "retention_pruned": 0,
            },
        )
        resp = client.post("/ui/poll/trigger", follow_redirects=True)
        assert resp.status_code == 200
        assert "Controllo posta completato" in resp.text

    def test_newsletters_list_and_add(self, web_client) -> None:
        client, db, _ = web_client
        # View page
        resp = client.get("/ui/newsletters")
        assert resp.status_code == 200
        assert "Pubblicazioni Registrate" in resp.text
        assert "The Daily Tech" in resp.text

        # Add newsletter via Form POST
        post_data = {
            "name": "Design Systems",
            "sender_email": "hello@design.org",
            "slug": "design-systems",
            "max_issues": "10",
        }
        resp_post = client.post("/ui/newsletters/add", data=post_data, follow_redirects=True)
        assert resp_post.status_code == 200
        assert "Design Systems" in resp_post.text
        assert db.get_newsletter_by_slug("design-systems") is not None

    def test_newsletter_toggle_and_delete(self, web_client) -> None:
        client, db, _ = web_client
        # Toggle
        resp_toggle = client.post("/ui/newsletters/nl-web-1/toggle", follow_redirects=True)
        assert resp_toggle.status_code == 200
        nl = db.get_newsletter_by_id("nl-web-1")
        assert nl.enabled is False

        # Delete
        resp_del = client.post("/ui/newsletters/nl-web-1/delete", follow_redirects=True)
        assert resp_del.status_code == 200
        assert db.get_newsletter_by_id("nl-web-1") is None

    def test_newsletter_edit_retention(self, web_client) -> None:
        client, db, _ = web_client
        # 1. Update retention to max 5 issues, 45 days
        edit_data = {
            "name": "The Daily Tech Updated",
            "sender_email": "tech-new@daily.com",
            "max_issues": "5",
            "max_age_days": "45",
        }
        resp = client.post("/ui/newsletters/nl-web-1/edit", data=edit_data, follow_redirects=True)
        assert resp.status_code == 200
        assert "aggiornata con successo" in resp.text
        nl = db.get_newsletter_by_id("nl-web-1")
        assert nl.name == "The Daily Tech Updated"
        assert nl.sender_email == "tech-new@daily.com"
        assert nl.retention_max_issues == 5
        assert nl.retention_max_age_days == 45

        # 2. Reset retention to unlimited by leaving fields empty
        clear_data = {
            "name": "The Daily Tech Updated",
            "sender_email": "tech-new@daily.com",
            "max_issues": "",
            "max_age_days": "",
        }
        resp_clear = client.post("/ui/newsletters/nl-web-1/edit", data=clear_data, follow_redirects=True)
        assert resp_clear.status_code == 200
        nl = db.get_newsletter_by_id("nl-web-1")
        assert nl.retention_max_issues is None
        assert nl.retention_max_age_days is None

    def test_newsletter_edit_apply_retention_now(self, web_client) -> None:
        client, db, fs = web_client
        now = datetime.now(UTC)
        # Create extra issues to exceed retention limit
        for i in range(2, 6):
            epub = fs.save_epub("the-daily-tech", f"the-daily-tech-00{i}.epub", b"epub-content")
            db.create_issue(
                Issue(
                    id=f"iss-web-{i}",
                    newsletter_id="nl-web-1",
                    email_message_id=f"mid-{i}@test",
                    subject=f"Issue #{i}",
                    received_at=now,
                    series_index=i,
                    status="done",
                    epub_path=str(epub),
                )
            )
        assert len(db.list_issues_by_newsletter("nl-web-1")) == 5

        # Edit with max_issues=2 and apply_retention_now=true
        data = {
            "name": "The Daily Tech",
            "sender_email": "tech@daily.com",
            "max_issues": "2",
            "apply_retention_now": "true",
        }
        resp = client.post("/ui/newsletters/nl-web-1/edit", data=data, follow_redirects=True)
        assert resp.status_code == 200
        assert "Pulizia retention completata" in resp.text
        # Should now have only 2 newest issues
        remaining = db.list_issues_by_newsletter("nl-web-1")
        assert len(remaining) == 2

    def test_library_view_and_filtering(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/library")
        assert resp.status_code == 200
        assert "Libreria Newsletter EPUB" in resp.text
        assert "Release Day!" in resp.text

        # Filter by search
        resp_filter = client.get("/ui/library?search=Release")
        assert "Release Day!" in resp_filter.text

        resp_no_match = client.get("/ui/library?search=NotFound")
        assert "Nessun numero trovato" in resp_no_match.text

    def test_issue_detail_view(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/issues/iss-web-1")
        assert resp.status_code == 200
        assert "Dettaglio Numero" in resp.text
        assert "Release Day!" in resp.text
        assert "mid-daily-1@test" in resp.text

    def test_settings_view_and_test_imap(self, web_client, monkeypatch) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/settings")
        assert resp.status_code == 200
        has_title = (
            "Impostazioni &amp; Connessioni" in resp.text
            or "Impostazioni & Connessioni" in resp.text
        )
        assert has_title

        # Test IMAP action with mock to prevent network blocking
        monkeypatch.setattr(
            "crosspoint_newsletter.ingest.email_client.EmailIngestClient.test_connection",
            lambda self: (False, "Credenziali incomplete"),
        )
        resp_imap = client.post("/ui/settings/test-imap")
        assert resp_imap.status_code == 200
        assert "Esito Test Connessione IMAP" in resp_imap.text

    def test_settings_save_and_test_imap_form(self, web_client, monkeypatch, tmp_path: Path) -> None:
        client, _, _ = web_client
        monkeypatch.setattr("crosspoint_newsletter.config.BASE_DIR", tmp_path)

        orig_host = config.IMAP_HOST
        orig_user = config.IMAP_USER
        orig_pass = config.IMAP_PASSWORD
        orig_folder = config.IMAP_FOLDER
        orig_port = config.IMAP_PORT

        try:
            # 1. Save IMAP settings
            save_resp = client.post(
                "/ui/settings/imap/save",
                data={
                    "host": "imap.mailtest.org",
                    "port": 993,
                    "user": "reader@mailtest.org",
                    "password": "secretpassword",
                    "folder": "INBOX",
                },
                follow_redirects=True,
            )
            assert save_resp.status_code == 200
            assert "salvata con successo" in save_resp.text
            assert "reader@mailtest.org" in save_resp.text

            # 2. Test IMAP with form values (mocked to prevent network calls)
            monkeypatch.setattr(
                "crosspoint_newsletter.ingest.email_client.EmailIngestClient.test_connection",
                lambda self: (True, "Connessione di test simulata riuscita"),
            )
            test_resp = client.post(
                "/ui/settings/imap/test",
                data={
                    "host": "imap.mailtest.org",
                    "port": 993,
                    "user": "reader@mailtest.org",
                    "password": "pwd",
                    "folder": "INBOX",
                },
            )
            assert test_resp.status_code == 200
            assert "Connessione di test simulata riuscita" in test_resp.text
        finally:
            config.IMAP_HOST = orig_host
            config.IMAP_USER = orig_user
            config.IMAP_PASSWORD = orig_pass
            config.IMAP_FOLDER = orig_folder
            config.IMAP_PORT = orig_port

    def test_static_css_served(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]
        assert "--bg-primary" in resp.text

    def test_local_datetime_formatting(self) -> None:
        from crosspoint_newsletter.serve.webui import format_datetime_local

        # 15:46:46 UTC in September (Europe/Rome CEST is UTC+2) should be 17:46:46
        utc_dt = datetime(2026, 9, 25, 15, 46, 46, tzinfo=UTC)
        formatted = format_datetime_local(utc_dt)
        assert formatted == "2026-09-25 17:46:46"

        iso_str = "2026-09-25T15:46:46.000000+00:00"
        formatted_str = format_datetime_local(iso_str)
        assert formatted_str == "2026-09-25 17:46:46"

    def test_save_timezone_ui(self, web_client, monkeypatch) -> None:
        client, _, _ = web_client
        orig_tz = config.TIMEZONE
        monkeypatch.setattr(config, "update_env_file", lambda k, v: None)
        try:
            resp = client.post(
                "/ui/settings/timezone/save",
                data={"timezone": "UTC"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert "Fuso orario aggiornato con successo a UTC" in resp.text
            assert config.TIMEZONE == "UTC"

            # Invalid timezone test
            bad_resp = client.post(
                "/ui/settings/timezone/save",
                data={"timezone": "Invalid/Fake_Zone"},
                follow_redirects=True,
            )
            assert bad_resp.status_code == 200
            assert "Fuso orario non valido" in bad_resp.text
        finally:
            config.TIMEZONE = orig_tz


class TestWebUII18n:
    def test_i18n_translation_unit(self) -> None:
        from crosspoint_newsletter.serve.i18n import t

        assert t("common.save", "it") == "Salva"
        assert t("common.save", "en") == "Save"
        assert t("dashboard.stat_ready", "it") == "EPUB Pronti per la Lettura"
        assert t("dashboard.stat_ready", "en") == "EPUBs Ready to Read"
        assert t("non_existent_key_xyz", "it") == "non_existent_key_xyz"

    def test_dashboard_renders_english_via_cookie(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/", cookies={"cn_lang": "en"})
        assert resp.status_code == 200
        assert 'lang="en"' in resp.text
        assert "System Overview" in resp.text
        assert "IMAP Auto-Polling:" in resp.text
        assert "Active Publications" in resp.text
        assert "Check Mail Now" in resp.text

    def test_newsletters_renders_english(self, web_client) -> None:
        client, db, _ = web_client
        resp = client.get("/ui/newsletters", cookies={"cn_lang": "en"})
        assert resp.status_code == 200
        assert "Newsletter Publications" in resp.text
        assert "Registered Publications" in resp.text
        assert "Register New Newsletter" in resp.text

    def test_inbox_renders_english(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/inbox", cookies={"cn_lang": "en"})
        assert resp.status_code == 200
        assert "New Newsletters Detected" in resp.text
        assert "Blocked Senders (Blacklist)" in resp.text

    def test_library_and_settings_renders_english(self, web_client) -> None:
        client, _, _ = web_client
        resp_lib = client.get("/ui/library", cookies={"cn_lang": "en"})
        assert resp_lib.status_code == 200
        assert "EPUB Newsletter Library" in resp_lib.text

        resp_set = client.get("/ui/settings", cookies={"cn_lang": "en"})
        assert resp_set.status_code == 200
        assert "Settings &amp; Connections" in resp_set.text
        assert "Web Interface Language" in resp_set.text

    def test_lang_switch_route(self, web_client) -> None:
        client, _, _ = web_client
        resp = client.get("/ui/lang/en?next=/ui/newsletters", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert resp.headers["location"] == "/ui/newsletters"
        assert "cn_lang=en" in resp.headers.get("set-cookie", "")

    def test_save_language_ui(self, web_client, monkeypatch) -> None:
        client, _, _ = web_client
        orig_lang = config.LANGUAGE
        monkeypatch.setattr(config, "update_env_file", lambda k, v: None)
        try:
            resp = client.post(
                "/ui/settings/language/save",
                data={"language": "en"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
            assert config.LANGUAGE == "en"
            assert "Interface language successfully updated to English" in resp.text

            # Test invalid language
            bad_resp = client.post(
                "/ui/settings/language/save",
                data={"language": "invalid_lang"},
                follow_redirects=True,
            )
            assert bad_resp.status_code == 200
            assert "Lingua non supportata" in bad_resp.text or "Unsupported language" in bad_resp.text
        finally:
            config.LANGUAGE = orig_lang


