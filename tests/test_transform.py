"""Unit tests for HTML-to-EPUB transformation pipeline."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from crosspoint_newsletter.ingest.models import RawContent
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.models import Issue, Newsletter
from crosspoint_newsletter.transform.cover_generator import CoverGenerator
from crosspoint_newsletter.transform.epub_builder import EpubBuilder
from crosspoint_newsletter.transform.html_cleaner import HtmlCleaner
from crosspoint_newsletter.transform.pipeline import TransformPipeline


def test_clean_html_removes_scripts_and_trackers() -> None:
    cleaner = HtmlCleaner()
    dirty_html = """
    <html>
      <head><script>alert('bad');</script><style>body { background: black; }</style></head>
      <body>
        <h1>Titolo Notizia</h1>
        <p>Paragrafo normale con <strong>grassetto</strong> e <em>corsivo</em>.</p>
        <img src="https://track.com/pixel.gif" width="1" height="1" alt="tracker" />
        <a href="https://example.com/unsubscribe">Unsubscribe here</a>
      </body>
    </html>
    """
    cleaned = cleaner.clean(dirty_html)

    assert "<script" not in cleaned
    assert "alert(" not in cleaned
    assert "<style" not in cleaned
    assert "pixel.gif" not in cleaned
    assert "Unsubscribe here" not in cleaned
    assert "<h1>Titolo Notizia</h1>" in cleaned
    assert "<strong>grassetto</strong>" in cleaned


def test_clean_html_preserves_semantics() -> None:
    cleaner = HtmlCleaner()
    html_doc = """
    <div>
      <h2>Analisi Trimestrale</h2>
      <blockquote>Citazione importante da un analista.</blockquote>
      <ul>
        <li>Punto A</li>
        <li>Punto B</li>
      </ul>
      <table><tr><th>Col 1</th></tr><tr><td>Val 1</td></tr></table>
    </div>
    """
    cleaned = cleaner.clean(html_doc)
    assert "<h2>Analisi Trimestrale</h2>" in cleaned
    assert "<blockquote>Citazione importante da un analista.</blockquote>" in cleaned
    assert "<li>Punto A</li>" in cleaned
    assert "<table>" in cleaned


def test_plain_text_to_html_fallback() -> None:
    cleaner = HtmlCleaner()
    text = "Primo paragrafo con informazioni.\n\nSecondo paragrafo dopo linea vuota."
    res = cleaner.clean(html_content=None, text_fallback=text)
    assert "<p>Primo paragrafo con informazioni.</p>" in res
    assert "<p>Secondo paragrafo dopo linea vuota.</p>" in res


def test_cover_generator_480x800() -> None:
    gen = CoverGenerator(width=480, height=800)
    png_bytes = gen.generate_cover_image(
        newsletter_name="The Bull",
        title="Mercati in fermento e inflazione in calo",
        series_index=42,
        date=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )

    assert len(png_bytes) > 0
    with Image.open(io.BytesIO(png_bytes)) as img:
        assert img.size == (480, 800)
        assert img.format == "PNG"


def test_build_epub_valid_structure_and_calibre_metadata(tmp_path: Path) -> None:
    builder = EpubBuilder()
    out_file = tmp_path / "the-bull-001.epub"

    builder.build_epub(
        newsletter_name="The Bull",
        newsletter_slug="the-bull",
        series_index=1,
        subject="The Bull #1 - Apertura mercati",
        message_id="bull-001@thebull.example.com",
        html_body="<p>Analisi di apertura borse europee.</p>",
        text_body=None,
        output_path=out_file,
    )

    assert out_file.exists()
    assert zipfile.is_zipfile(out_file)

    with zipfile.ZipFile(out_file, "r") as zf:
        namelist = zf.namelist()
        assert "mimetype" in namelist
        assert "META-INF/container.xml" in namelist

        # Inspect mimetype content
        mimetype_content = zf.read("mimetype").decode("utf-8").strip()
        assert mimetype_content == "application/epub+zip"

        # Search for OPF file and verify Calibre series tags
        opf_file = [n for n in namelist if n.endswith(".opf")][0]
        opf_content = zf.read(opf_file).decode("utf-8")

        assert 'name="calibre:series"' in opf_content
        assert 'content="The Bull"' in opf_content
        assert 'name="calibre:series_index"' in opf_content
        assert 'content="1"' in opf_content


def test_cid_images_embedded_in_epub(tmp_path: Path) -> None:
    builder = EpubBuilder()
    out_file = tmp_path / "nat-010.epub"

    # Tiny 1x1 PNG bytes
    tiny_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
        b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    builder.build_epub(
        newsletter_name="La Settimana Nat",
        newsletter_slug="la-settimana-nat",
        series_index=10,
        subject="La Settimana Nat #10",
        message_id="nat-010@settimananat.example.com",
        html_body='<p>Ecco la vignetta:</p><p><img src="cid:vignetta01" alt="Vignetta"/></p>',
        text_body=None,
        output_path=out_file,
        inline_images={"vignetta01": tiny_png},
    )

    assert out_file.exists()
    with zipfile.ZipFile(out_file, "r") as zf:
        namelist = zf.namelist()
        # Ensure image was placed inside EPUB images folder
        assert any("images/inline_1" in n for n in namelist)


def test_transform_pipeline_success_updates_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    nl = Newsletter(
        id="nl-1",
        name="The Bull",
        slug="the-bull",
        sender_email="newsletter@thebull.example.com",
    )
    db.upsert_newsletter(nl)

    issue = Issue(
        id="issue-1",
        newsletter_id="nl-1",
        email_message_id="mid-123",
        subject="The Bull #1",
        received_at=datetime.now(UTC),
        series_index=1,
        status="pending",
    )
    db.create_issue(issue)

    pipeline = TransformPipeline(db=db, epub_dir=tmp_path / "epub")
    raw = RawContent(
        newsletter_name="The Bull",
        newsletter_slug="the-bull",
        subject="The Bull #1",
        sender="newsletter@thebull.example.com",
        message_id="mid-123",
        received_at=datetime.now(UTC),
        html_body="<p>Analisi di apertura dei mercati finanziari europei: i listini aprono in lieve rialzo sulla scia delle decisioni comunicate ieri dalla banca centrale.</p>",
        issue_id="issue-1",
        series_index=1,
    )

    created_path = pipeline.process_raw_content(raw)
    assert created_path is not None
    assert created_path.exists()

    # Verify DB status updated to 'done'
    updated_issue = db.get_issue("issue-1")
    assert updated_issue is not None
    assert updated_issue.status == "done"
    assert updated_issue.epub_path is not None
    assert updated_issue.processed_at is not None


def test_remote_image_download_failure_inserts_placeholder(tmp_path: Path) -> None:
    """Verify that unreachable remote images get replaced by text placeholder."""
    builder = EpubBuilder(
        download_remote_images=True,
        remote_timeout=0.5,  # short timeout for test speed
    )
    out_file = tmp_path / "fail-img-001.epub"

    html_with_remote = (
        '<p>Intro</p>'
        '<img src="http://unreachable.invalid/photo.jpg" alt="Foto editoriale"/>'
        '<p>Chiusura</p>'
    )

    builder.build_epub(
        newsletter_name="Test NL",
        newsletter_slug="test-nl",
        series_index=1,
        subject="Test immagine non disponibile",
        message_id="test-fail-img@crosspoint.local",
        html_body=html_with_remote,
        text_body=None,
        output_path=out_file,
    )

    assert out_file.exists()

    # Read the chapter XHTML inside the EPUB and verify placeholder
    with zipfile.ZipFile(out_file, "r") as zf:
        chapter_file = [n for n in zf.namelist() if n.endswith(".xhtml") and "chap" in n][0]
        chapter_html = zf.read(chapter_file).decode("utf-8")

        # The remote image should NOT be present as <img>
        assert "unreachable.invalid" not in chapter_html
        # Placeholder text should be present (either alt text or generic)
        assert "Foto editoriale" in chapter_html or "Immagine non disponibile" in chapter_html
        # No remote_* image file should be embedded (download failed)
        image_files = [n for n in zf.namelist() if n.startswith("images/remote_")]
        assert len(image_files) == 0


def test_transform_pipeline_error_updates_db(tmp_path: Path) -> None:
    """Verify that a failed conversion sets issue status to 'error' with detail."""
    db = Database(tmp_path / "test_err.db")
    nl = Newsletter(
        id="nl-err",
        name="Broken NL",
        slug="broken-nl",
        sender_email="broken@example.com",
    )
    db.upsert_newsletter(nl)

    issue = Issue(
        id="issue-err",
        newsletter_id="nl-err",
        email_message_id="mid-err",
        subject="Will Fail",
        received_at=datetime.now(UTC),
        series_index=1,
        status="pending",
    )
    db.create_issue(issue)

    # Use a deliberately broken builder by pointing to an unwritable path
    pipeline = TransformPipeline(db=db, epub_dir=Path("/dev/null/impossible/path"))
    raw = RawContent(
        newsletter_name="Broken NL",
        newsletter_slug="broken-nl",
        subject="Will Fail",
        sender="broken@example.com",
        message_id="mid-err",
        received_at=datetime.now(UTC),
        html_body="<p>Content</p>",
        issue_id="issue-err",
        series_index=1,
    )

    result = pipeline.process_raw_content(raw)
    assert result is None

    updated = db.get_issue("issue-err")
    assert updated is not None
    assert updated.status == "error"
    assert updated.error_detail is not None


def test_clean_html_unwraps_layout_tables_and_removes_spacers() -> None:
    """Verify that nested email layout tables are unwrapped and spacer elements removed."""
    cleaner = HtmlCleaner()
    email_html = """
    <table id="bodyTable" role="presentation">
      <tr>
        <td>
          <span class="mcnPreviewText">Preheader text to be hidden</span>
          <div>&#847;&#847;&#847;&zwnj;&nbsp;</div>
          <table class="mcnTextContentContainer">
            <tr>
              <td>
                <p>Articolo principale della newsletter.</p>
              </td>
            </tr>
          </table>
          <table class="footerTable">
            <tr>
              <td>
                <p><a href="https://example.com/unsubscribe">Visualizza questa email nel browser</a></p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """
    cleaned = cleaner.clean(email_html)
    assert "<table" not in cleaned
    assert "Preheader text to be hidden" not in cleaned
    assert "Visualizza questa email" not in cleaned
    assert "Articolo principale della newsletter." in cleaned


def test_build_epub_decodes_rfc2047_subject(tmp_path: Path) -> None:
    """Verify that RFC2047 encoded subject headers are cleanly decoded in the EPUB."""
    builder = EpubBuilder()
    out_file = tmp_path / "mime-decoded.epub"

    encoded_subject = "=?utf-8?Q?La=20Fed=20alza=20i=20tassi.?="
    builder.build_epub(
        newsletter_name="The Bull",
        newsletter_slug="the-bull",
        series_index=1,
        subject=encoded_subject,
        message_id="test-mime@example.com",
        html_body="<p>Test contenuto</p>",
        text_body=None,
        output_path=out_file,
    )

    with zipfile.ZipFile(out_file, "r") as zf:
        opf = [n for n in zf.namelist() if n.endswith(".opf")][0]
        opf_content = zf.read(opf).decode("utf-8")
        assert "=?utf-8" not in opf_content
        assert "<dc:title>La Fed alza i tassi.</dc:title>" in opf_content


