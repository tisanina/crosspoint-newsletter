"""OPDS 1.2 Atom XML feed builder for CrossPoint e-ink reader catalog."""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

from crosspoint_newsletter.storage.models import Issue, Newsletter

OPDS_CATALOG_MIME = "application/atom+xml;profile=opds-catalog;charset=utf-8"
EPUB_MIME = "application/epub+zip"
IMAGE_PNG_MIME = "image/png"


def _format_iso(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class OpdsFeedBuilder:
    """Builds OPDS 1.2 compliant Atom XML feeds."""

    def __init__(self, base_title: str = "CrossPoint Newsletters") -> None:
        self.base_title = base_title

    def build_root_catalog(
        self,
        newsletters: list[dict[str, Any]],
        updated_at: datetime | None = None,
        recent_count: int = 0,
    ) -> str:
        """Generate root navigation catalog listing newsletter series and recent issues."""
        updated_str = _format_iso(updated_at)
        entries: list[str] = []

        # 1. Navigation entry: Recent issues (Ultime Uscite)
        recent_summary = (
            f"{recent_count} newsletter recenti disponibili per la lettura"
            if recent_count > 0
            else "Nessuna newsletter recente disponibile"
        )
        entries.append(f"""  <entry>
    <title>Ultime Uscite</title>
    <id>urn:crosspoint:section:recent</id>
    <updated>{updated_str}</updated>
    <author>
      <name>CrossPoint</name>
    </author>
    <content type="text">{html.escape(recent_summary)}</content>
    <link rel="subsection" href="/opds/recent" type="application/atom+xml;profile=opds-catalog" />
  </entry>""")

        # 2. Navigation entry per newsletter series
        for nl in newsletters:
            slug = html.escape(nl.get("slug", ""))
            name = html.escape(nl.get("name", ""))
            total_issues = nl.get("done_issues", 0)
            last_received = nl.get("last_received_at")
            if isinstance(last_received, datetime):
                last_iso = _format_iso(last_received)
            else:
                last_iso = updated_str
            summary = f"{total_issues} numero/i disponibile/i"

            entries.append(f"""  <entry>
    <title>{name}</title>
    <id>urn:crosspoint:newsletter:{slug}</id>
    <updated>{last_iso}</updated>
    <author>
      <name>{name}</name>
    </author>
    <content type="text">{html.escape(summary)}</content>
    <link rel="subsection" href="/opds/newsletter/{slug}" """
                           """type="application/atom+xml;profile=opds-catalog" />
  </entry>""")

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:crosspoint:catalog:root</id>
  <title>{html.escape(self.base_title)}</title>
  <updated>{updated_str}</updated>
  <author>
    <name>CrossPoint Server</name>
  </author>
  <link rel="self" href="/opds" type="application/atom+xml;profile=opds-catalog" />
  <link rel="start" href="/opds" type="application/atom+xml;profile=opds-catalog" />
{chr(10).join(entries)}
</feed>
"""
        return xml

    def build_series_feed(
        self,
        newsletter: Newsletter,
        issues: list[Issue],
        feed_url: str,
        updated_at: datetime | None = None,
    ) -> str:
        """Generate acquisition feed for a specific newsletter series."""
        updated_str = _format_iso(updated_at)
        title = html.escape(newsletter.name)
        slug = html.escape(newsletter.slug)

        entries = [self._build_issue_entry(issue, newsletter.name) for issue in issues]

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:crosspoint:series:{slug}</id>
  <title>{title}</title>
  <updated>{updated_str}</updated>
  <author>
    <name>{title}</name>
  </author>
  <link rel="self" href="{feed_url}" type="application/atom+xml;profile=opds-catalog" />
  <link rel="start" href="/opds" type="application/atom+xml;profile=opds-catalog" />
  <link rel="up" href="/opds" type="application/atom+xml;profile=opds-catalog" />
{chr(10).join(entries)}
</feed>
"""
        return xml

    def build_recent_feed(
        self,
        issues: list[tuple[Issue, str]],  # (issue, newsletter_name)
        feed_url: str = "/opds/recent",
        updated_at: datetime | None = None,
    ) -> str:
        """Generate acquisition feed for latest issues across all newsletters."""
        updated_str = _format_iso(updated_at)
        entries = [self._build_issue_entry(issue, nl_name) for issue, nl_name in issues]

        xml = f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:crosspoint:section:recent</id>
  <title>Ultime Uscite</title>
  <updated>{updated_str}</updated>
  <author>
    <name>CrossPoint</name>
  </author>
  <link rel="self" href="{feed_url}" type="application/atom+xml;profile=opds-catalog" />
  <link rel="start" href="/opds" type="application/atom+xml;profile=opds-catalog" />
  <link rel="up" href="/opds" type="application/atom+xml;profile=opds-catalog" />
{chr(10).join(entries)}
</feed>
"""
        return xml

    def _build_issue_entry(self, issue: Issue, author_name: str) -> str:
        """Build single Atom entry for an issue."""
        title = html.escape(issue.subject or f"Issue #{issue.series_index}")
        author = html.escape(author_name)
        issue_id = html.escape(issue.id)
        updated = _format_iso(issue.received_at)
        summary = html.escape(f"{issue.subject} — {author_name} #{issue.series_index}")

        # Links
        download_url = f"/opds/download/{issue_id}"
        cover_url = f"/opds/cover/{issue_id}"

        return f"""  <entry>
    <title>{title}</title>
    <id>urn:crosspoint:issue:{issue_id}</id>
    <author>
      <name>{author}</name>
    </author>
    <updated>{updated}</updated>
    <published>{updated}</published>
    <summary>{summary}</summary>
    <content type="text">Serie: {author}, Numero: #{issue.series_index}</content>
    <link rel="http://opds-spec.org/acquisition" href="{download_url}" type="{EPUB_MIME}" />
    <link rel="http://opds-spec.org/image" href="{cover_url}" type="{IMAGE_PNG_MIME}" />
    <link rel="http://opds-spec.org/image/thumbnail" href="{cover_url}" type="{IMAGE_PNG_MIME}" />
  </entry>"""
