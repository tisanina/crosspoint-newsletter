"""EPUB 3.0 builder with Calibre series metadata and proportional e-ink image scaling."""

from __future__ import annotations

import email.header
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from ebooklib import epub

from crosspoint_newsletter.transform.cover_generator import CoverGenerator
from crosspoint_newsletter.transform.html_cleaner import HtmlCleaner
from crosspoint_newsletter.transform.image_optimizer import ImageOptimizer

logger = logging.getLogger(__name__)


def clean_subject_title(subject: str) -> str:
    """Safely decode RFC2047 MIME encoded words in subject titles."""
    if not subject:
        return "Untitled"
    if "=?" in subject and "?=" in subject:
        try:
            parts = email.header.decode_header(subject)
            decoded: list[str] = []
            for content, enc in parts:
                if isinstance(content, bytes):
                    decoded.append(content.decode(enc or "utf-8", errors="replace"))
                else:
                    decoded.append(str(content))
            return "".join(decoded).strip()
        except Exception:
            pass
    return subject.strip()


def extract_description(html_content: str | None, text_content: str | None, max_words: int = 120) -> str:
    """Extract a concise plain-text summary (first N words) for DC:description metadata."""
    raw_text = ""
    if html_content:
        soup = BeautifulSoup(html_content, "lxml")
        raw_text = soup.get_text(separator=" ", strip=True)
    elif text_content:
        raw_text = text_content.strip()

    if not raw_text:
        return ""

    words = raw_text.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."

# CSS specifically crafted for high-readability on e-paper screens
EPUB_CSS = """
@namespace epub "http://www.idpf.org/2007/ops";

body {
    font-family: serif;
    font-size: 1.0em;
    line-height: 1.45;
    margin: 4%;
    padding: 0;
    color: #000;
    background-color: #fff;
}

h1, h2, h3, h4, h5, h6 {
    font-family: sans-serif;
    font-weight: bold;
    line-height: 1.25;
    margin-top: 1.2em;
    margin-bottom: 0.5em;
    text-align: left;
    page-break-after: avoid;
}

h1 { font-size: 1.45em; border-bottom: 1px solid #000; padding-bottom: 0.2em; }
h2 { font-size: 1.25em; }
h3 { font-size: 1.1em; }

p {
    margin-top: 0;
    margin-bottom: 0.75em;
    text-align: justify;
    text-justify: inter-word;
    orphans: 2;
    widows: 2;
}

blockquote {
    margin: 1em 1.5em;
    padding-left: 0.75em;
    border-left: 3px solid #666;
    font-style: italic;
}

/* Proportional image display ensuring single-page fit without distortion */
img {
    max-width: 100% !important;
    max-height: 90vh !important;
    height: auto !important;
    width: auto !important;
    display: block !important;
    margin: 1.2em auto !important;
    page-break-inside: avoid;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin: 1em 0;
}

table.data-table, table.data-table th, table.data-table td {
    border: 1px solid #333;
    padding: 6px 8px;
    font-size: 0.9em;
}

th, td {
    padding: 4px 6px;
    vertical-align: top;
    border: none;
}

ul, ol {
    margin-top: 0.5em;
    margin-bottom: 0.8em;
    padding-left: 1.5em;
}

li {
    margin-bottom: 0.3em;
}
"""


class EpubBuilder:
    def __init__(
        self,
        default_language: str = "it",
        download_remote_images: bool = True,
        remote_timeout: float = 10.0,
        max_images_size_bytes: int = 5 * 1024 * 1024,
        optimizer: ImageOptimizer | None = None,
    ) -> None:
        self.default_language = default_language
        self.download_remote_images = download_remote_images
        self.remote_timeout = remote_timeout
        self.max_images_size_bytes = max_images_size_bytes
        self.cleaner = HtmlCleaner()
        self.optimizer = optimizer or ImageOptimizer(max_width=480, max_height=800)
        self.cover_gen = CoverGenerator(width=480, height=800)

    def build_epub(
        self,
        newsletter_name: str,
        newsletter_slug: str,
        series_index: int,
        subject: str,
        message_id: str,
        html_body: str | None,
        text_body: str | None,
        output_path: Path,
        inline_images: dict[str, bytes] | None = None,
        date: datetime | None = None,
        language: str | None = None,
        source_url: str | None = None,
    ) -> Path:
        """Construct a validated EPUB 3.0 book with Calibre series tags and proportional images."""
        inline_images = inline_images or {}
        lang = language or self.default_language
        pub_date = date or datetime.now(UTC)
        clean_subject = clean_subject_title(subject)

        book = epub.EpubBook()
        book.set_identifier(message_id)
        book.set_title(clean_subject)
        book.set_language(lang)
        book.add_author(newsletter_name)

        # Dublin Core publication date
        book.add_metadata("DC", "date", pub_date.strftime("%Y-%m-%dT%H:%M:%SZ"))

        # Enriched Dublin Core metadata: description, subject, source
        description = extract_description(html_body, text_body)
        if description:
            book.add_metadata("DC", "description", description)

        book.add_metadata("DC", "subject", newsletter_name)

        if source_url:
            book.add_metadata("DC", "source", source_url)

        # Calibre series custom metadata (both EPUB2 and EPUB3 compatibility)
        book.add_metadata(
            None,
            "meta",
            newsletter_name,
            {"name": "calibre:series", "content": newsletter_name},
        )
        book.add_metadata(
            None,
            "meta",
            str(series_index),
            {"name": "calibre:series_index", "content": str(series_index)},
        )

        # 1. Prepare images & map CIDs
        cid_mapping: dict[str, str] = {}
        epub_images: list[epub.EpubItem] = []
        total_img_size = 0

        # Incorporate inline CID images
        for idx, (cid, img_data) in enumerate(inline_images.items(), start=1):
            if total_img_size + len(img_data) > self.max_images_size_bytes:
                logger.warning("Image quota reached, skipping further inline images")
                break

            opt_res = self.optimizer.optimize(img_data, filename_hint=cid)
            if not opt_res:
                continue

            opt_bytes, media_type, _ = opt_res
            ext = "png" if "png" in media_type else "jpg"
            img_filename = f"images/inline_{idx}.{ext}"

            epub_img = epub.EpubItem(
                uid=f"image_inline_{idx}",
                file_name=img_filename,
                media_type=media_type,
                content=opt_bytes,
            )
            book.add_item(epub_img)
            epub_images.append(epub_img)
            cid_mapping[cid] = img_filename
            total_img_size += len(opt_bytes)

        # 2. Clean HTML content
        cleaned_html = self.cleaner.clean(
            html_content=html_body,
            text_fallback=text_body,
            cid_mapping=cid_mapping,
        )

        # 3. Handle external remote images if enabled
        if self.download_remote_images and "<img" in cleaned_html:
            cleaned_html = self._download_and_embed_remote_images(
                html_snippet=cleaned_html,
                book=book,
                current_size=total_img_size,
            )

        # 4. Generate cover image (480x800)
        cover_png = self.cover_gen.generate_cover_image(
            newsletter_name=newsletter_name,
            title=clean_subject,
            series_index=series_index,
            date=pub_date,
        )
        book.set_cover("cover.png", cover_png)

        # 5. Add custom CSS
        css_item = epub.EpubItem(
            uid="style_nav",
            file_name="style/main.css",
            media_type="text/css",
            content=EPUB_CSS.encode("utf-8"),
        )
        book.add_item(css_item)

        # 6. Main reading chapter
        chapter_html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <title>{clean_subject}</title>
  <link rel="stylesheet" type="text/css" href="style/main.css" />
</head>
<body>
  <section epub:type="chapter">
    <h1>{clean_subject}</h1>
    {cleaned_html}
  </section>
</body>
</html>
"""
        chapter = epub.EpubHtml(
            title=clean_subject,
            file_name="chap_01.xhtml",
            lang=lang,
        )
        chapter.set_content(chapter_html.encode("utf-8"))
        chapter.add_item(css_item)
        book.add_item(chapter)

        # 7. Navigation (TOC, NCX & Nav)
        book.toc = (
            epub.Link("chap_01.xhtml", clean_subject, "chapter_1"),
        )
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        book.spine = ["cover", "nav", chapter]

        # 8. Write EPUB file to destination
        output_path.parent.mkdir(parents=True, exist_ok=True)
        epub.write_epub(str(output_path), book, {})
        logger.info("Successfully created EPUB: %s", output_path)
        return output_path

    def _download_and_embed_remote_images(
        self,
        html_snippet: str,
        book: epub.EpubBook,
        current_size: int,
    ) -> str:
        """Download external http(s) images with timeout and embed proportionally."""
        soup = BeautifulSoup(html_snippet, "lxml")
        body = soup.find("body")
        root = body if body else soup

        img_count = 0
        total_size = current_size

        for img in root.find_all("img"):
            src = str(img.get("src", "")).strip()
            if not src.startswith(("http://", "https://")):
                continue

            alt = img.get("alt", "").strip() or "Immagine"

            if total_size >= self.max_images_size_bytes:
                img.replace_with(soup.new_string(f"[{alt}]"))
                continue

            try:
                img_data = self._fetch_url(src)
                if not img_data:
                    img.replace_with(soup.new_string(f"[{alt}]"))
                    continue

                img_opt_res = self.optimizer.optimize(img_data, filename_hint=src)
                if not img_opt_res:
                    img.replace_with(soup.new_string(f"[{alt}]"))
                    continue

                img_count += 1
                opt_bytes, media_type, _ = img_opt_res
                ext = "png" if "png" in media_type else "jpg"
                filename = f"images/remote_{img_count}.{ext}"

                epub_img = epub.EpubItem(
                    uid=f"image_remote_{img_count}",
                    file_name=filename,
                    media_type=media_type,
                    content=opt_bytes,
                )
                book.add_item(epub_img)
                img["src"] = filename
                total_size += len(opt_bytes)
            except Exception as exc:
                logger.debug("Failed downloading image '%s': %s", src, exc)
                img.replace_with(soup.new_string(f"[{alt}]"))

        res = "".join(str(c) for c in root.children)
        return res

    def _fetch_url(self, url: str) -> bytes | None:
        """Fetch remote URL with timeout safety."""
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None

        with httpx.Client(timeout=self.remote_timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "CrossPointNewsletter/0.1"})
            if resp.status_code == 200:
                return resp.content
            return None
