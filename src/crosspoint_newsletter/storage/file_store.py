"""File storage management for EPUB books, covers, and raw emails."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from crosspoint_newsletter import config

if TYPE_CHECKING:
    from crosspoint_newsletter.storage.database import Database

logger = logging.getLogger(__name__)


class FileStore:
    """Manages files and directories on disk with consistency and usage monitoring."""

    def __init__(
        self,
        base_dir: Path | None = None,
        epub_dir: Path | None = None,
        covers_dir: Path | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.base_dir = base_dir or config.DATA_DIR
        self.epub_dir = epub_dir or (Path(os.getenv("CN_EPUB_DIR")) if os.getenv("CN_EPUB_DIR") else self.base_dir / "epub")
        self.covers_dir = covers_dir or (Path(os.getenv("CN_COVERS_DIR")) if os.getenv("CN_COVERS_DIR") else self.base_dir / "covers")
        self.raw_dir = raw_dir or (Path(os.getenv("CN_RAW_DIR")) if os.getenv("CN_RAW_DIR") else self.base_dir / "raw")

        # Ensure base directories exist
        self.epub_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def get_epub_dest_path(self, slug: str, filename: str) -> Path:
        """Construct the standard destination path for an EPUB."""
        return self.epub_dir / slug / filename

    def save_epub(self, slug: str, filename: str, content: bytes | Path) -> Path:
        """Save EPUB bytes or copy an existing file into the structured hierarchy."""
        dest = self.get_epub_dest_path(slug, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(content, Path):
            shutil.copy2(content, dest)
        else:
            dest.write_bytes(content)

        logger.info("Saved EPUB to FileStore: %s (%d bytes)", dest, dest.stat().st_size)
        return dest

    def get_epub_path(self, slug: str, filename: str) -> Path | None:
        """Locate an EPUB file by newsletter slug and filename."""
        path = self.get_epub_dest_path(slug, filename)
        return path if path.exists() else None

    def resolve_path(self, path_or_str: Path | str | None) -> Path | None:
        """Resolve a relative or absolute path within the filestore or base directory."""
        if not path_or_str:
            return None
        p = Path(path_or_str)
        if p.is_absolute():
            return p if p.exists() else None
        for base in (self.base_dir, config.BASE_DIR):
            cand = base / p
            if cand.exists():
                return cand
        return None

    def delete_epub(self, path_or_str: Path | str) -> bool:
        """Safely delete an EPUB file from disk."""
        if not path_or_str:
            return False

        path = Path(path_or_str)
        # Handle relative path
        if not path.is_absolute():
            # If relative to BASE_DIR or DATA_DIR
            resolved = config.BASE_DIR / path
            if not resolved.exists():
                resolved = self.base_dir / path
            path = resolved

        try:
            if path.exists() and path.is_file():
                path.unlink()
                logger.info("Deleted EPUB from storage: %s", path)

                # Prune parent directory if empty
                parent = path.parent
                if parent != self.epub_dir and not any(parent.iterdir()):
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
                return True
        except Exception as exc:
            logger.warning("Failed deleting EPUB file %s: %s", path, exc)

        return False

    def delete_raw(self, path_or_str: Path | str) -> bool:
        """Delete raw email file or directory."""
        if not path_or_str:
            return False

        path = Path(path_or_str)
        if not path.is_absolute():
            path = config.BASE_DIR / path

        try:
            if path.exists():
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                return True
        except Exception as exc:
            logger.warning("Failed deleting raw file %s: %s", path, exc)

        return False

    def get_disk_usage(self, slug: str | None = None) -> dict:
        """Calculate bytes occupied and file counts per newsletter and total."""
        target_dirs = [self.epub_dir / slug] if slug else [p for p in self.epub_dir.iterdir() if p.is_dir()]

        series_stats: list[dict] = []
        total_bytes = 0
        total_files = 0

        for s_dir in sorted(target_dirs):
            if not s_dir.exists() or not s_dir.is_dir():
                continue
            s_slug = s_dir.name
            files = list(s_dir.glob("*.epub"))
            size = sum(f.stat().st_size for f in files)
            total_bytes += size
            total_files += len(files)

            series_stats.append({
                "slug": s_slug,
                "file_count": len(files),
                "total_bytes": size,
                "total_mb": round(size / (1024 * 1024), 2),
            })

        return {
            "total_bytes": total_bytes,
            "total_mb": round(total_bytes / (1024 * 1024), 2),
            "total_files": total_files,
            "series": series_stats,
        }

    def check_consistency(self, db: Database) -> dict:
        """Audit database records against physical files on disk to find missing or orphan files."""
        missing_on_disk: list[dict] = []
        orphan_files: list[str] = []

        # 1. Check all issues marked 'done'
        with db._get_connection() as conn:
            cursor = conn.execute("SELECT id, newsletter_id, subject, epub_path FROM issues WHERE status = 'done'")
            all_done = cursor.fetchall()

        known_paths: set[str] = set()

        for row in all_done:
            epub_p = row["epub_path"]
            if not epub_p:
                missing_on_disk.append({
                    "issue_id": row["id"],
                    "subject": row["subject"],
                    "expected_path": None,
                    "reason": "epub_path is null in database",
                })
                continue

            # Resolve path
            resolved = Path(epub_p)
            if not resolved.is_absolute():
                resolved = config.BASE_DIR / resolved
            if not resolved.exists():
                missing_on_disk.append({
                    "issue_id": row["id"],
                    "subject": row["subject"],
                    "expected_path": str(resolved),
                    "reason": "File not found on filesystem",
                })
            else:
                known_paths.add(resolved.resolve().as_posix())

        # 2. Check for orphan EPUBs in epub_dir
        for disk_file in self.epub_dir.rglob("*.epub"):
            if disk_file.resolve().as_posix() not in known_paths:
                orphan_files.append(str(disk_file))

        return {
            "missing_on_disk": missing_on_disk,
            "orphan_files": orphan_files,
            "consistent": len(missing_on_disk) == 0 and len(orphan_files) == 0,
        }
