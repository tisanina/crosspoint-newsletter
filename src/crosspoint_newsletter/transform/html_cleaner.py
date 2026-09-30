"""HTML cleaning and semantic formatting for e-ink EPUB rendering."""

from __future__ import annotations

import html
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Elements to remove completely
FORBIDDEN_TAGS = {
    "script",
    "style",
    "iframe",
    "form",
    "input",
    "button",
    "select",
    "textarea",
    "noscript",
    "svg",
    "canvas",
    "object",
    "embed",
}

# Regex to match invisible characters used for preheader spacing in newsletter engines
INVISIBLE_CHARS_RE = re.compile(r"[\s\u200c\u200b\u200d\ufeff\u034f\u2007\u00a0\u00ad]+")

# Common newsletter service, preheader, and footer patterns
DISCARD_PATTERNS = re.compile(
    r"(unsubscribe|disiscriviti|cancella l'iscrizione|"
    r"view (this (email|message) )?in (your )?browser|"
    r"visualizza (quest[ao] (email|messaggio) )?nel browser|"
    r"apri (quest[ao] (email|messaggio) )?nel browser|"
    r"leggi (quest[ao] (email|messaggio) )?nel browser|"
    r"leggi nell'app|read in app|read in the app|"
    r"hai inoltrato questa email\?|iscriviti qui|iscriviti adesso|subscribe now|"
    r"manage preferences|gestisci preferenze|aggiorna le tue preferenze|"
    r"modifica (le tue )?preferenze|privacy policy|informativa sulla privacy|restack)",
    re.IGNORECASE,
)

DISCARD_HREF_PATTERNS = re.compile(
    r"(unsubscribe|list-manage\.com/(profile|unsubscribe)|mailchi\.mp/[^/]+/test-newsletter|"
    r"/profile\?u=|manage-preferences|email-read-in-app|subscribe-widget|action=restack)",
    re.IGNORECASE,
)


def _is_data_table(table: Tag) -> bool:
    """Check if a table contains genuine tabular data rather than email layout scaffolding."""
    # If the table explicitly defines headers, treat as tabular data
    if table.find("th"):
        return True

    # Role presentation/none is explicitly marked as layout
    role = str(table.get("role", "")).lower()
    if role in ("presentation", "none"):
        return False

    rows = table.find_all("tr", recursive=False)
    if not rows and table.find("tbody"):
        rows = table.find("tbody").find_all("tr", recursive=False)

    # Need at least 2 rows to be a meaningful data table
    if len(rows) < 2:
        return False

    # Count columns per row
    col_counts = [len(r.find_all(["td", "th"], recursive=False)) for r in rows]
    # If every row has only 1 column, it's a vertical layout container
    if max(col_counts or [0]) <= 1:
        return False

    # Email layouts frequently nest tables inside cells
    if table.find_all("table"):
        return False

    # Check classes / IDs typical of email layouts
    class_str = " ".join(table.get("class", [])) if isinstance(table.get("class"), list) else str(table.get("class", ""))
    id_str = str(table.get("id", ""))
    combined = f"{class_str} {id_str}".lower()
    if any(k in combined for k in ("bodytable", "mcntable", "container", "wrapper", "root")):
        return False

    return True


class HtmlCleaner:
    def __init__(self) -> None:
        pass

    def clean(
        self,
        html_content: str | None,
        text_fallback: str | None = None,
        cid_mapping: dict[str, str] | None = None,
    ) -> str:
        """Clean HTML or convert text fallback into semantic HTML for e-ink reading."""
        if not html_content or not html_content.strip():
            return self.text_to_html(text_fallback or "")

        soup = BeautifulSoup(html_content, "lxml")
        cid_mapping = cid_mapping or {}

        # 1. Remove comments
        for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()

        # 2. Remove forbidden tags
        for tag_name in FORBIDDEN_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # 3. Remove tracking pixels and invisible images
        for img in soup.find_all("img"):
            if self._is_tracking_pixel(img):
                img.decompose()
                continue

            # Remap CID images
            src = img.get("src", "")
            if src.lower().startswith("cid:"):
                clean_cid = src[4:].strip("<> ")
                if clean_cid in cid_mapping:
                    img["src"] = cid_mapping[clean_cid]
                else:
                    img.replace_with(soup.new_string("[Immagine non disponibile]"))

        # 4. Remove service/footer links and preheader notices
        for a in soup.find_all("a"):
            text = a.get_text(strip=True)
            href = str(a.get("href", ""))
            if DISCARD_PATTERNS.search(text) or DISCARD_PATTERNS.search(href) or DISCARD_HREF_PATTERNS.search(href):
                # Remove container if link is alone in small container (p, div, span, td)
                parent = a.parent
                if (
                    parent
                    and parent.name in ("p", "div", "span", "td")
                    and len(parent.get_text(strip=True)) < 120
                ):
                    parent.decompose()
                else:
                    a.decompose()

        # 4b. Remove hidden preheader text elements (e.g. MailChimp mcnPreviewText)
        for hidden in soup.find_all(attrs={"class": lambda c: c and any(k in str(c).lower() for k in ("mcnpreviewtext", "preheader", "email-preview"))}):
            hidden.decompose()

        for hidden in soup.find_all(style=lambda s: s and any(k in str(s).lower() for k in ("display:none", "display: none", "visibility:hidden", "font-size:0"))):
            if hidden.name in ("span", "div", "p") and len(hidden.get_text(strip=True)) < 250:
                hidden.decompose()

        # 5. Unwrap email layout tables into clean block elements
        all_tables = list(soup.find_all("table"))
        for t in reversed(all_tables):
            if not _is_data_table(t):
                # Unwrap internal structural containers first
                for sub in list(t.find_all(["tbody", "thead", "tfoot"])):
                    sub.unwrap()
                for tr in list(t.find_all("tr")):
                    tr.unwrap()
                for td in list(t.find_all("td")):
                    cell_text = td.get_text(strip=True)
                    if not cell_text and not td.find("img"):
                        td.decompose()
                    else:
                        td.name = "div"
                t.unwrap()

        # 6. Remove empty spacer elements and invisible preheader padding
        for tag in list(soup.find_all(["div", "p", "span"])):
            text = tag.get_text()
            cleaned_text = INVISIBLE_CHARS_RE.sub("", text)
            if not cleaned_text and not tag.find("img"):
                tag.decompose()

        # 7. Clean styling attributes on remaining tags
        for tag in soup.find_all(True):
            self._clean_tag_attributes(tag)

        # 8. Extract main content container or body
        body = soup.find("body")
        clean_soup = body if body else soup

        # Return sanitized HTML fragment
        res = "".join(str(c) for c in clean_soup.children if isinstance(c, (Tag, NavigableString)))
        return res.strip() if res.strip() else self.text_to_html(text_fallback or "")

    def text_to_html(self, text: str) -> str:
        """Convert plain text fallback into structured HTML paragraphs."""
        if not text or not text.strip():
            return "<p>[Contenuto non disponibile]</p>"

        escaped = html.escape(text.strip())
        paragraphs = re.split(r"\n\s*\n", escaped)
        html_parts: list[str] = []
        for p in paragraphs:
            clean_p = p.strip().replace("\n", "<br/>")
            if clean_p:
                html_parts.append(f"<p>{clean_p}</p>")

        return "\n".join(html_parts)

    def _is_tracking_pixel(self, img: Tag) -> bool:
        """Identify 1x1 tracking pixels, beacon images, or hidden items."""
        width = str(img.get("width", "")).strip()
        height = str(img.get("height", "")).strip()
        if (width in ("0", "1") and height in ("0", "1")) or width == "0" or height == "0":
            return True

        style = str(img.get("style", "")).lower()
        if "display:none" in style or "visibility:hidden" in style:
            return True
        if "width:1px" in style or "height:1px" in style:
            return True

        src = str(img.get("src", "")).lower()
        return bool(
            ("pixel" in src or "track" in src or "beacon" in src)
            and (not width or width in ("0", "1", "2"))
        )

    def _clean_tag_attributes(self, tag: Tag) -> None:
        """Remove colors, background colors, and absolute positioning for e-ink clarity."""
        allowed_attrs = {"src", "href", "alt", "title", "id", "class"}
        attrs_to_remove = [k for k in tag.attrs if k not in allowed_attrs]
        for attr in attrs_to_remove:
            del tag.attrs[attr]

        # Sanitize href
        if "href" in tag.attrs:
            href = str(tag["href"]).strip()
            if href.lower().startswith("javascript:"):
                del tag.attrs["href"]

