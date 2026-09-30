#!/usr/bin/env python3
"""Manual end-to-end test: .eml file → full pipeline → EPUB on disk.

Usage:
    python scripts/manual_e2e_test.py [path/to/email.eml]

If no .eml path is given, uses all fixtures in tests/fixtures/.

The generated EPUBs are saved under data/e2e-test/ and automatically
opened with the default system reader (Apple Books on macOS).
"""

from __future__ import annotations

import email
import email.header
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure src/ is on the path when running standalone
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from crosspoint_newsletter.transform.epub_builder import EpubBuilder  # noqa: E402


def _decode_header(raw: str) -> str:
    """Decode RFC2047 MIME-encoded headers to plain text."""
    parts = email.header.decode_header(raw)
    decoded: list[str] = []
    for content, charset in parts:
        if isinstance(content, bytes):
            decoded.append(content.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(content))
    return "".join(decoded).strip()


def parse_eml(eml_path: Path) -> dict:
    """Extract fields from a raw .eml file for the builder."""
    raw_bytes = eml_path.read_bytes()
    msg = email.message_from_bytes(raw_bytes)

    subject = _decode_header(str(msg.get("Subject", "Untitled")))
    from_header = _decode_header(str(msg.get("From", "Unknown")))
    raw_mid = str(msg.get("Message-ID", f"manual-{eml_path.stem}@test"))
    message_id = raw_mid.strip("<> ")

    # Derive newsletter name from the From display-name
    if '"' in from_header:
        nl_name = from_header.split('"')[1]
    elif "<" in from_header:
        nl_name = from_header.split("<")[0].strip()
    else:
        nl_name = from_header

    # Extract body
    html_body: str | None = None
    text_body: str | None = None
    inline_images: dict[str, bytes] = {}

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            cid = part.get("Content-ID")

            if cid:
                clean_cid = cid.strip("<> ")
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    inline_images[clean_cid] = payload

            if ct == "text/html" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace")
            elif ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    text_body = payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_body = decoded
            else:
                text_body = decoded

    return {
        "newsletter_name": nl_name,
        "newsletter_slug": nl_name.lower().replace(" ", "-"),
        "subject": subject,
        "message_id": message_id,
        "html_body": html_body,
        "text_body": text_body,
        "inline_images": inline_images,
    }


def run_e2e(eml_path: Path, output_dir: Path, index: int) -> Path:
    """Process a single .eml through the full transform and return EPUB path."""
    print(f"\n{'='*60}")
    print(f"  Processing: {eml_path.name}")
    print(f"{'='*60}")

    data = parse_eml(eml_path)
    slug = data["newsletter_slug"]
    epub_filename = f"{slug}-{index:03d}.epub"
    epub_path = output_dir / slug / epub_filename

    builder = EpubBuilder(
        download_remote_images=True,
        remote_timeout=10.0,
    )
    result = builder.build_epub(
        newsletter_name=data["newsletter_name"],
        newsletter_slug=slug,
        series_index=index,
        subject=data["subject"],
        message_id=data["message_id"],
        html_body=data["html_body"],
        text_body=data["text_body"],
        output_path=epub_path,
        inline_images=data["inline_images"],
        date=datetime.now(UTC),
    )

    print(f"  ✅ EPUB created: {result}")
    print(f"     Size: {result.stat().st_size:,} bytes")
    return result


def main() -> None:
    output_dir = PROJECT_ROOT / "data" / "e2e-test"
    output_dir.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1:
        eml_files = [Path(p) for p in sys.argv[1:]]
    else:
        fixtures_dir = PROJECT_ROOT / "tests" / "fixtures"
        eml_files = sorted(fixtures_dir.glob("*.eml"))

    if not eml_files:
        print("❌ No .eml files found. Pass a path or add fixtures to tests/fixtures/")
        sys.exit(1)

    print(f"📧 Found {len(eml_files)} email(s) to process")
    created_epubs: list[Path] = []

    for idx, eml_path in enumerate(eml_files, start=1):
        if not eml_path.exists():
            print(f"⚠️  File not found: {eml_path}")
            continue
        try:
            epub_path = run_e2e(eml_path, output_dir, index=idx)
            created_epubs.append(epub_path)
        except Exception as exc:
            print(f"  ❌ Failed: {exc}")

    print(f"\n{'='*60}")
    print(f"  Risultati: {len(created_epubs)}/{len(eml_files)} EPUB generati")
    print(f"  Directory: {output_dir}")
    print(f"{'='*60}")

    # Checklist promemoria
    print("""
📋 CHECKLIST DI VERIFICA MANUALE:
  □ L'EPUB si apre correttamente in Apple Books / Calibre
  □ La copertina è visibile (nome newsletter, titolo, numero, data)
  □ Il contenuto è leggibile (niente script, tracking, stili invasivi)
  □ Le immagini inline sono presenti (se l'email le conteneva)
  □ Le immagini non scaricabili mostrano placeholder testuale
  □ I metadati sono corretti:
      - Titolo = subject dell'email
      - Autore = nome della newsletter
      - Serie Calibre = nome newsletter
      - Indice serie = numero progressivo
""")

    # Chiedi se aprire gli EPUB
    if created_epubs and sys.platform == "darwin":
        answer = input("Vuoi aprire gli EPUB generati in Apple Books? [s/N] ").strip().lower()
        if answer in ("s", "si", "sì", "y", "yes"):
            for epub_path in created_epubs:
                subprocess.run(["open", str(epub_path)], check=False)


if __name__ == "__main__":
    main()
