"""IMAP email client for newsletter ingestion."""

from __future__ import annotations

import email
import email.header
import email.utils
import hashlib
import imaplib
import logging
import time
import uuid
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

from crosspoint_newsletter import config
from crosspoint_newsletter.ingest.matcher import NewsletterMatcher, extract_email_address
from crosspoint_newsletter.ingest.models import RawContent
from crosspoint_newsletter.storage.models import UnmatchedEmail

logger = logging.getLogger(__name__)


def extract_sender_name(from_header: str) -> str:
    """Extract clean display name from a From header string."""
    if not from_header:
        return "Unknown"
    clean = from_header.strip()
    if "<" in clean:
        name_part = clean.split("<")[0].strip(' "\'')
        if name_part:
            return name_part
    if '"' in clean:
        name_part = clean.split('"')[1].strip()
        if name_part:
            return name_part
    addr = extract_email_address(clean)
    if "@" in addr:
        return addr.split("@")[0].capitalize()
    return clean or "Unknown"


def decode_mime_header(header_val: str | None) -> str:
    """Safely decode RFC2047 MIME encoded email headers."""
    if not header_val:
        return ""
    try:
        decoded_slices = email.header.decode_header(header_val)
        parts: list[str] = []
        for content, encoding in decoded_slices:
            if isinstance(content, bytes):
                encoding = encoding or "utf-8"
                try:
                    parts.append(content.decode(encoding, errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    parts.append(content.decode("utf-8", errors="replace"))
            else:
                parts.append(str(content))
        return "".join(parts).strip()
    except Exception as exc:
        logger.debug("Failed decoding header '%s': %s", header_val, exc)
        return str(header_val).strip()


def parse_email_date(date_str: str | None) -> datetime:
    """Parse RFC2822 date header or fallback to current UTC time."""
    if not date_str:
        return datetime.now(UTC)
    try:
        parsed_tuple = email.utils.parsedate_to_datetime(date_str)
        # Convert to naive UTC or keep standard datetime
        if parsed_tuple.tzinfo is not None:
            return parsed_tuple.astimezone(UTC)
        return parsed_tuple.replace(tzinfo=UTC)
    except Exception:
        return datetime.now(UTC)


class EmailIngestClient:
    def __init__(
        self,
        matcher: NewsletterMatcher,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        folder: str | None = None,
        keep_raw: bool | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.matcher = matcher
        raw_host = host or config.IMAP_HOST
        self.host = raw_host.strip() if raw_host else ""
        self.port = port or config.IMAP_PORT

        raw_user = user or config.IMAP_USER
        self.user = (
            raw_user.replace("\xa0", " ").strip() if raw_user else ""
        )

        raw_password = password or config.IMAP_PASSWORD
        if raw_password:
            # Clean up NBSP, regular spaces (Google App Passwords are 16 letters)
            clean_pw = raw_password.replace("\xa0", " ").strip()
            # If 16 chars + spaces (typical Google app password format like "xxxx xxxx xxxx xxxx")
            if len(clean_pw.replace(" ", "")) == 16:
                clean_pw = clean_pw.replace(" ", "")
            self.password = clean_pw
        else:
            self.password = ""

        raw_folder = folder or config.IMAP_FOLDER
        self.folder = raw_folder.strip() if raw_folder else "INBOX"
        self.keep_raw = config.CN_KEEP_RAW if keep_raw is None else keep_raw
        self.raw_dir = raw_dir or config.RAW_DIR

    def parse_message(self, msg: Message, raw_bytes: bytes | None = None) -> RawContent | None:
        """Extract content, match newsletter and register issue."""
        raw_mid = msg.get("Message-ID", "")
        message_id = decode_mime_header(raw_mid).strip("<> ")
        if not message_id:
            # Generate deterministic fallback message-id from hash of payload
            hash_digest = hashlib.sha256(raw_bytes or msg.as_bytes()).hexdigest()[:16]
            message_id = f"gen-{hash_digest}@crosspoint.local"

        subject = decode_mime_header(msg.get("Subject", "Untitled Newsletter"))
        from_header = decode_mime_header(msg.get("From", ""))
        date_header = msg.get("Date", "")
        received_at = parse_email_date(date_header)

        headers: dict[str, str] = {
            k.lower(): decode_mime_header(v) for k, v in msg.items()
        }

        # Extract body parts and inline images
        html_body: str | None = None
        text_body: str | None = None
        inline_images: dict[str, bytes] = {}

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                # Inline or attached images
                cid = part.get("Content-ID")
                if cid:
                    clean_cid = cid.strip("<> ")
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        inline_images[clean_cid] = payload

                if content_type == "text/html" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            html_body = payload.decode(charset, errors="replace")
                        except (LookupError, UnicodeDecodeError):
                            html_body = payload.decode("utf-8", errors="replace")
                elif content_type == "text/plain" and "attachment" not in content_disposition:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            text_body = payload.decode(charset, errors="replace")
                        except (LookupError, UnicodeDecodeError):
                            text_body = payload.decode("utf-8", errors="replace")
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = msg.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    decoded = payload.decode("utf-8", errors="replace")

                if content_type == "text/html":
                    html_body = decoded
                else:
                    text_body = decoded

        # If neither html nor text found, fail gracefully
        if not html_body and not text_body:
            logger.warning("Email %s has no readable text or html payload", message_id)
            return None

        # If sender is blacklisted, skip message completely
        sender_email = extract_email_address(from_header)
        db = getattr(self.matcher, "db", None)
        if db and sender_email and db.is_blacklisted(sender_email):
            logger.info(
                "Email '%s' from blacklisted sender '%s' skipped completely",
                subject,
                sender_email,
            )
            return None

        # Determine match first to know destination slug
        matched_nl = self.matcher.match_newsletter(from_header, subject, headers)
        if not matched_nl:
            logger.info(
                "Email '%s' from '%s' does not match any configured newsletter",
                subject,
                from_header,
            )
            self._save_to_inbox(
                message_id=message_id,
                from_header=from_header,
                subject=subject,
                received_at=received_at,
                raw_bytes=raw_bytes if raw_bytes else msg.as_bytes(),
            )
            return None


        raw_path: Path | None = None
        if self.keep_raw:
            raw_target_dir = self.raw_dir / matched_nl.slug
            raw_target_dir.mkdir(parents=True, exist_ok=True)
            safe_id = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16]
            raw_path = raw_target_dir / f"{safe_id}.eml"
            with open(raw_path, "wb") as f:
                f.write(raw_bytes if raw_bytes else msg.as_bytes())

        match_res = self.matcher.match_and_create_issue(
            message_id=message_id,
            sender_header=from_header,
            subject=subject,
            received_at=received_at,
            headers=headers,
            raw_path=str(raw_path) if raw_path else None,
            metadata={
                "author": matched_nl.name,
                "from": from_header,
                "has_html": html_body is not None,
                "images_count": len(inline_images),
            },
        )

        if not match_res:
            return None

        newsletter, issue = match_res
        return RawContent(
            newsletter_name=newsletter.name,
            newsletter_slug=newsletter.slug,
            subject=issue.subject,
            sender=from_header,
            message_id=message_id,
            received_at=received_at,
            html_body=html_body,
            text_body=text_body,
            inline_images=inline_images,
            raw_eml_path=raw_path,
            issue_id=issue.id,
            series_index=issue.series_index,
        )

    def process_email_bytes(self, raw_bytes: bytes) -> RawContent | None:
        """Parse raw email bytes and run ingestion."""
        msg = email.message_from_bytes(raw_bytes)
        return self.parse_message(msg, raw_bytes=raw_bytes)

    def _save_to_inbox(
        self,
        message_id: str,
        from_header: str,
        subject: str,
        received_at: datetime,
        raw_bytes: bytes,
    ) -> None:
        """Save unmatched email into triage inbox queue."""
        if not self.matcher or not getattr(self.matcher, "db", None):
            return
        db = self.matcher.db
        if db.issue_exists(message_id):
            return

        sender_email = extract_email_address(from_header)
        if db.is_blacklisted(sender_email):
            logger.info("Email '%s' from blacklisted sender '%s' skipped from inbox", subject, sender_email)
            return

        safe_id = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16]
        inbox_raw_dir = self.raw_dir / "inbox"
        inbox_raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = inbox_raw_dir / f"{safe_id}.eml"

        with open(raw_path, "wb") as f:
            f.write(raw_bytes)

        sender_email = extract_email_address(from_header)
        sender_name = extract_sender_name(from_header)

        item = UnmatchedEmail(
            id=str(uuid.uuid4()),
            email_message_id=message_id,
            sender_header=from_header,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            received_at=received_at,
            raw_eml_path=str(raw_path),
        )
        db.add_inbox_email(item)
        logger.info(
            "Unmatched email '%s' from '%s' saved to inbox queue (ID: %s)",
            subject,
            sender_email,
            item.id,
        )

    def fetch_unseen(
        self,
        max_retry: int = 3,
        mark_seen: bool = True,
        limit: int | None = None,
    ) -> list[RawContent]:
        """Connect to IMAP, poll unseen emails, match and register them."""
        if not self.host or not self.user or not self.password:
            logger.error("IMAP credentials incomplete (host=%s, user=%s)", self.host, self.user)
            return []

        mail: imaplib.IMAP4_SSL | None = None
        results: list[RawContent] = []

        for attempt in range(1, max_retry + 1):
            try:
                logger.info(
                    "Connecting to IMAP %s:%s (attempt %d/%d)...",
                    self.host,
                    self.port,
                    attempt,
                    max_retry,
                )
                mail = imaplib.IMAP4_SSL(self.host, self.port)
                mail.login(self.user, self.password)
                mail.select(self.folder)

                status, search_data = mail.search(None, "UNSEEN")
                if status != "OK":
                    logger.warning("IMAP search returned status %s", status)
                    break

                message_ids = search_data[0].split()
                if limit and limit > 0:
                    message_ids = message_ids[:limit]
                logger.info("Found %d unseen email(s) in folder %s", len(message_ids), self.folder)

                for msg_id in message_ids:
                    res_status, fetch_data = mail.fetch(msg_id, "(BODY.PEEK[])")
                    if res_status != "OK" or not fetch_data or not fetch_data[0]:
                        logger.warning("Failed to fetch message %s", msg_id)
                        continue

                    raw_bytes = fetch_data[0][1]
                    if not isinstance(raw_bytes, bytes):
                        continue

                    raw_content = self.process_email_bytes(raw_bytes)
                    if raw_content:
                        results.append(raw_content)

                    if mark_seen:
                        mail.store(msg_id, "+FLAGS", "\\Seen")

                break
            except imaplib.IMAP4.error as err:
                logger.error("IMAP authentication or protocol error: %s", err)
                break
            except Exception as exc:
                logger.warning("Transient error during IMAP fetch: %s", exc)
                if attempt < max_retry:
                    time.sleep(2 ** attempt)
                else:
                    logger.error("Max retries reached for IMAP fetch.")
            finally:
                if mail:
                    try:
                        mail.close()
                        mail.logout()
                    except Exception:
                        pass

        return results

    def test_connection(self) -> tuple[bool, str]:
        """Test IMAP connection and authentication."""
        if not self.host or not self.user or not self.password:
            return False, "Credenziali IMAP incomplete (host, user o password mancanti)"
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(self.host, self.port)
            mail.login(self.user, self.password)
            typ, _ = mail.select(self.folder)
            msg = (
                f"Connessione IMAP a {self.host}:{self.port} riuscita (cartella '{self.folder}' trovata)"
                if typ == "OK"
                else f"Connessione IMAP a {self.host}:{self.port} riuscita"
            )
            return True, msg
        except Exception as exc:
            return False, f"Errore di connessione IMAP: {exc}"
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass

