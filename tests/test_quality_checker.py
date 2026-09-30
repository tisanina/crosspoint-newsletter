"""Unit tests for QualityChecker module."""

from __future__ import annotations

import zipfile
from pathlib import Path

from crosspoint_newsletter.transform.epub_builder import EpubBuilder
from crosspoint_newsletter.transform.quality_checker import QualityChecker


def test_quality_checker_passes_valid_epub(tmp_path: Path) -> None:
    builder = EpubBuilder()
    out_file = tmp_path / "valid.epub"

    builder.build_epub(
        newsletter_name="The Bull",
        newsletter_slug="the-bull",
        series_index=1,
        subject="Mercati in rialzo",
        message_id="bull-01@thebull.example.com",
        html_body="<p>Analisi di apertura borse europee con testo sufficientemente lungo per superare la soglia di qualità minima.</p>",
        text_body=None,
        output_path=out_file,
    )

    checker = QualityChecker()
    report = checker.check_epub(out_file)

    assert report.is_valid
    assert report.score >= 0.9
    assert len(report.errors) == 0
    assert report.text_length >= 50


def test_quality_checker_fails_nonexistent_or_corrupt_file(tmp_path: Path) -> None:
    checker = QualityChecker()

    # Non-existent
    missing_rep = checker.check_epub(tmp_path / "does_not_exist.epub")
    assert not missing_rep.is_valid
    assert missing_rep.score == 0.0

    # Not a zip
    corrupt_file = tmp_path / "corrupt.epub"
    corrupt_file.write_bytes(b"invalid data")
    corrupt_rep = checker.check_epub(corrupt_file)
    assert not corrupt_rep.is_valid
    assert corrupt_rep.score == 0.0


def test_quality_checker_fails_empty_content(tmp_path: Path) -> None:
    builder = EpubBuilder()
    out_file = tmp_path / "empty.epub"

    # Very short body (< 100 chars and 0 images)
    builder.build_epub(
        newsletter_name="Empty NL",
        newsletter_slug="empty-nl",
        series_index=1,
        subject="Empty",
        message_id="empty@test.local",
        html_body="<p>Troppo corto</p>",
        text_body=None,
        output_path=out_file,
    )

    checker = QualityChecker(min_text_length=100)
    report = checker.check_epub(out_file)

    # Must fail because text < 100 and no images
    assert not report.is_valid
    assert any("too short" in e.lower() for e in report.errors)


def test_quality_checker_detects_broken_image_references(tmp_path: Path) -> None:
    # Build a valid EPUB first
    builder = EpubBuilder(download_remote_images=False)
    out_file = tmp_path / "broken_img.epub"

    builder.build_epub(
        newsletter_name="Test",
        newsletter_slug="test",
        series_index=1,
        subject="Broken Image Test",
        message_id="broken@test.local",
        html_body='<p>Questo testo è sufficientemente lungo da superare agevolmente il limite dei cento caratteri del controllo qualità dell\'EPUB.</p><img src="images/missing_photo.png" alt="Missing"/>',
        text_body=None,
        output_path=out_file,
    )

    checker = QualityChecker()
    report = checker.check_epub(out_file)

    # Must detect missing_photo.png not in archive
    assert not report.is_valid
    assert any("missing_photo.png" in e for e in report.errors)
