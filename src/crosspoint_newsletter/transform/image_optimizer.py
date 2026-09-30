"""Image optimizer for e-paper screens: grayscale, scaling, adaptive compression, quotas."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


@dataclass
class ImageStats:
    original_size_bytes: int
    optimized_size_bytes: int
    original_dimensions: tuple[int, int]
    optimized_dimensions: tuple[int, int]
    format: Literal["image/jpeg", "image/png"]
    is_grayscale: bool
    quality_loss: bool


class ImageOptimizer:
    """Optimizes inline and remote images specifically for e-ink reading devices."""

    def __init__(
        self,
        max_width: int = 480,
        max_height: int = 800,
        max_file_size_bytes: int = 500 * 1024,
        jpeg_quality: int = 78,
    ) -> None:
        self.max_width = max_width
        self.max_height = max_height
        self.max_file_size_bytes = max_file_size_bytes
        self.jpeg_quality = jpeg_quality

    def optimize(
        self,
        image_bytes: bytes,
        filename_hint: str = "",
    ) -> tuple[bytes, str, ImageStats] | None:
        """Optimize raw image bytes for high clarity, proportional scaling and fast e-paper rendering.

        Returns (optimized_bytes, mime_type, stats) or None if the image should be skipped.
        """
        if not image_bytes:
            return None

        orig_size = len(image_bytes)

        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                orig_dim = (img.width, img.height)

                # Ignore tiny 1x1 or 2x2 tracking artifacts that may have slipped past HTML cleaner
                if img.width <= 2 and img.height <= 2:
                    return None

                # Handle multi-frame GIF/PNG: take first frame
                if getattr(img, "is_animated", False):
                    img.seek(0)

                # Correct EXIF orientation if present
                img = ImageOps.exif_transpose(img) or img

                # 1. Proportional resize to fit within screen bounds
                if img.width > self.max_width or img.height > self.max_height:
                    img.thumbnail((self.max_width, self.max_height), Image.Resampling.LANCZOS)

                opt_dim = (img.width, img.height)

                # 2. Determine format (PNG for transparency or few colors, JPEG for photos)
                has_transparency = (
                    img.mode in ("RGBA", "LA")
                    or (img.mode == "P" and "transparency" in img.info)
                )

                # Convert to grayscale (8-bit 'L')
                # If transparent, compose over pure white background before grayscale
                if has_transparency:
                    # Check if actually using alpha
                    rgba = img.convert("RGBA")
                    alpha = rgba.split()[3]
                    # If transparent pixels exist, paste onto white background
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=alpha)
                    gray_img = bg.convert("L")
                else:
                    gray_img = img.convert("L")

                # Heuristic: Count unique colors to detect line art/charts vs photos
                colors = gray_img.getcolors(maxcolors=128)
                is_line_art = colors is not None and len(colors) <= 64

                # Choose best format
                buf = io.BytesIO()
                chosen_mime: str

                if is_line_art:
                    # Save as optimized PNG (8-bit grayscale)
                    gray_img.save(buf, format="PNG", optimize=True)
                    chosen_mime = "image/png"
                else:
                    # Save as progressive/optimized JPEG
                    gray_img.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=True)
                    chosen_mime = "image/jpeg"

                opt_bytes = buf.getvalue()

                # 3. Quota check: if still above max_file_size_bytes, reduce JPEG quality
                if len(opt_bytes) > self.max_file_size_bytes:
                    logger.debug(
                        "Image size %d exceeds limit %d, compressing further",
                        len(opt_bytes),
                        self.max_file_size_bytes,
                    )
                    buf = io.BytesIO()
                    gray_img.save(buf, format="JPEG", quality=60, optimize=True)
                    opt_bytes = buf.getvalue()
                    chosen_mime = "image/jpeg"

                    # If STILL above max quota, discard to preserve device stability
                    if len(opt_bytes) > self.max_file_size_bytes:
                        logger.warning(
                            "Image %s exceeds quota even after compression (%d bytes > %d), skipping",
                            filename_hint,
                            len(opt_bytes),
                            self.max_file_size_bytes,
                        )
                        return None

                stats = ImageStats(
                    original_size_bytes=orig_size,
                    optimized_size_bytes=len(opt_bytes),
                    original_dimensions=orig_dim,
                    optimized_dimensions=opt_dim,
                    format=chosen_mime,  # type: ignore[arg-type]
                    is_grayscale=True,
                    quality_loss=len(opt_bytes) < orig_size,
                )

                return opt_bytes, chosen_mime, stats

        except Exception as exc:
            logger.debug("Image optimization fallback for '%s': %s", filename_hint, exc)
            # Check magic bytes to distinguish real images from random corrupt bytes
            is_png = image_bytes.startswith(b"\x89PNG")
            is_jpeg = image_bytes.startswith(b"\xff\xd8")
            is_gif = image_bytes.startswith(b"GIF8")
            is_webp = len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP"

            if not (is_png or is_jpeg or is_gif or is_webp):
                return None

            mime: Literal["image/jpeg", "image/png"] = "image/png" if is_png else "image/jpeg"
            stats = ImageStats(
                original_size_bytes=orig_size,
                optimized_size_bytes=orig_size,
                original_dimensions=(0, 0),
                optimized_dimensions=(0, 0),
                format=mime,
                is_grayscale=False,
                quality_loss=False,
            )
            return image_bytes, mime, stats
