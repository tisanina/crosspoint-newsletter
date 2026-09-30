"""EPUB quality assurance and validation module for e-ink distribution."""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class QualityReport:
    """Detailed quality validation results for a generated EPUB."""
    is_valid: bool
    score: float
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    text_length: int = 0
    images_count: int = 0

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "score": round(self.score, 2),
            "warnings": self.warnings,
            "errors": self.errors,
            "text_length": self.text_length,
            "images_count": self.images_count,
        }


class QualityChecker:
    """Validates EPUB standard conformity, image references, and minimum readable content."""

    def __init__(
        self,
        min_text_length: int = 100,
        enforce_stored_mimetype: bool = True,
    ) -> None:
        self.min_text_length = min_text_length
        self.enforce_stored_mimetype = enforce_stored_mimetype

    def check_epub(self, epub_path: Path | str) -> QualityReport:
        path = Path(epub_path)
        errors: list[str] = []
        warnings: list[str] = []
        score = 1.0

        # 1. Existence and non-empty file
        if not path.exists():
            return QualityReport(
                is_valid=False,
                score=0.0,
                errors=[f"File does not exist: {path}"],
            )

        if path.stat().st_size == 0:
            return QualityReport(
                is_valid=False,
                score=0.0,
                errors=["EPUB file is completely empty (0 bytes)"],
            )

        # 2. ZIP Archive Integrity
        if not zipfile.is_zipfile(path):
            return QualityReport(
                is_valid=False,
                score=0.0,
                errors=["File is not a valid ZIP archive"],
            )

        try:
            with zipfile.ZipFile(path, "r") as zf:
                namelist = zf.namelist()

                # 3. Standard EPUB mimetype validation
                if "mimetype" not in namelist:
                    errors.append("Missing 'mimetype' file at EPUB root")
                else:
                    first_file = namelist[0]
                    if first_file != "mimetype":
                        warnings.append(f"'mimetype' is not the first file in archive (found '{first_file}')")
                        score -= 0.1

                    mimetype_info = zf.getinfo("mimetype")
                    if self.enforce_stored_mimetype and mimetype_info.compress_type != zipfile.ZIP_STORED:
                        warnings.append("'mimetype' file is compressed instead of STORED (uncompressed)")
                        score -= 0.05

                    try:
                        mime_content = zf.read("mimetype").decode("utf-8").strip()
                        if mime_content != "application/epub+zip":
                            errors.append(f"Invalid mimetype content: expected 'application/epub+zip', got '{mime_content}'")
                    except UnicodeDecodeError:
                        errors.append("'mimetype' file is not valid UTF-8")

                # 4. META-INF/container.xml check
                if "META-INF/container.xml" not in namelist:
                    errors.append("Missing required 'META-INF/container.xml'")
                    opf_path = None
                else:
                    try:
                        container_xml = zf.read("META-INF/container.xml")
                        root = ElementTree.fromstring(container_xml)
                        # Find rootfile element
                        rootfile = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
                        if rootfile is not None and "full-path" in rootfile.attrib:
                            opf_path = rootfile.attrib["full-path"]
                        else:
                            # Fallback: search for any .opf
                            opfs = [n for n in namelist if n.endswith(".opf")]
                            opf_path = opfs[0] if opfs else None
                            warnings.append("Could not resolve OPF path from container.xml, using fallback")
                            score -= 0.1
                    except Exception as exc:
                        errors.append(f"Failed to parse META-INF/container.xml: {exc}")
                        opf_path = None

                # 5. OPF Manifest and Metadata check
                if not opf_path or opf_path not in namelist:
                    errors.append(f"Package OPF file not found: '{opf_path}'")
                else:
                    try:
                        opf_xml = zf.read(opf_path).decode("utf-8")
                        if "<dc:title>" not in opf_xml:
                            errors.append("Missing Dublin Core <dc:title> metadata in OPF")
                        if "calibre:series" not in opf_xml:
                            warnings.append("Missing Calibre series metadata in OPF")
                            score -= 0.05
                    except Exception as exc:
                        errors.append(f"Failed to inspect OPF: {exc}")

                # 6. Read and parse all XHTML chapters to check content and broken images
                total_text_len = 0
                image_files_in_zip = {n for n in namelist if n.startswith("EPUB/images/") or n.startswith("images/")}
                referenced_images: set[str] = set()

                xhtml_files = [n for n in namelist if n.endswith((".xhtml", ".html")) and "nav" not in n and "cover" not in n]

                if not xhtml_files:
                    errors.append("EPUB contains no content chapter XHTML files")

                for xf in xhtml_files:
                    try:
                        raw_html = zf.read(xf).decode("utf-8")
                        soup = BeautifulSoup(raw_html, "xml")

                        # Extract text
                        body_text = soup.get_text(separator=" ", strip=True)
                        total_text_len += len(body_text)

                        # Check for referenced images
                        for img in soup.find_all("img"):
                            src = img.get("src", "")
                            if src:
                                # Resolve relative to chapter directory
                                ch_dir = str(Path(xf).parent)
                                resolved = str(Path(ch_dir) / src).replace("\\", "/") if ch_dir != "." else src
                                # Normalize path
                                norm_path = Path(resolved).as_posix()
                                referenced_images.add(norm_path)

                                # If image is missing from archive, report error
                                if norm_path not in namelist and src not in namelist:
                                    errors.append(f"Broken image reference in {xf}: '{src}' not found in archive")

                    except UnicodeDecodeError:
                        errors.append(f"Chapter file '{xf}' contains invalid UTF-8 bytes")
                    except Exception as exc:
                        warnings.append(f"Failed parsing chapter '{xf}': {exc}")
                        score -= 0.1

                # 7. Check minimum text length requirement
                img_count = len(image_files_in_zip)
                if total_text_len < self.min_text_length:
                    if img_count > 0:
                        # Image-centric newsletter (e.g. comics/cartoons): warning only
                        warnings.append(
                            f"Short content: text length ({total_text_len} chars) is below threshold "
                            f"({self.min_text_length} chars), but contains {img_count} image(s)"
                        )
                        score -= 0.1
                    else:
                        # Empty newsletter: fatal error
                        errors.append(
                            f"Content too short ({total_text_len} chars < {self.min_text_length}) "
                            f"with 0 images: empty issue"
                        )

        except Exception as exc:
            return QualityReport(
                is_valid=False,
                score=0.0,
                errors=[f"Fatal exception during EPUB inspection: {exc}"],
            )

        is_valid = len(errors) == 0
        final_score = max(0.0, min(1.0, score if is_valid else 0.0))

        return QualityReport(
            is_valid=is_valid,
            score=final_score,
            warnings=warnings,
            errors=errors,
            text_length=total_text_len,
            images_count=len(image_files_in_zip),
        )
