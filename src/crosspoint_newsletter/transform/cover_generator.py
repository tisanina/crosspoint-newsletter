"""Cover generator creating high-contrast e-ink friendly covers for 480x800 portrait displays."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _wrap_text(text: str, max_chars_per_line: int = 24) -> list[str]:
    """Wrap long titles into balanced lines."""
    words = text.split()
    lines: list[str] = []
    current_line: list[str] = []
    current_len = 0

    for word in words:
        if current_len + len(word) + 1 <= max_chars_per_line:
            current_line.append(word)
            current_len += len(word) + 1
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_len = len(word)

    if current_line:
        lines.append(" ".join(current_line))

    return lines or [text]


class CoverGenerator:
    WIDTH = 480
    HEIGHT = 800

    def __init__(self, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.width = width
        self.height = height

    def generate_cover_image(
        self,
        newsletter_name: str,
        title: str,
        series_index: int,
        date: datetime | None = None,
        save_path: Path | None = None,
    ) -> bytes:
        """Draw an elegant high-contrast monochrome cover suited for e-ink."""
        # 1-bit or 8-bit grayscale image
        img = Image.new("L", (self.width, self.height), color=255)
        draw = ImageDraw.Draw(img)

        # 1. Outer & Inner decorative borders
        margin = 24
        draw.rectangle(
            [margin, margin, self.width - margin, self.height - margin],
            outline=0,
            width=4,
        )
        draw.rectangle(
            [margin + 6, margin + 6, self.width - margin - 6, self.height - margin - 6],
            outline=0,
            width=1,
        )

        # 2. Fonts (fallback to default font, scaling if possible)
        try:
            # Try system fonts commonly present or default bitmap
            font_series = ImageFont.load_default()
            font_title = ImageFont.load_default()
            font_meta = ImageFont.load_default()
        except Exception:
            font_series = ImageFont.load_default()
            font_title = font_series
            font_meta = font_series

        # 3. Series Header (Top block)
        top_y = margin + 40
        draw.line([margin + 20, top_y + 40, self.width - margin - 20, top_y + 40], fill=0, width=2)

        series_label = f"• {newsletter_name.upper()} •"
        draw.text(
            (self.width // 2, top_y + 15),
            series_label,
            fill=0,
            font=font_series,
            anchor="mm",
        )

        # 4. Central Box for Issue Title
        center_y = self.height // 2 - 50
        wrapped_lines = _wrap_text(title, max_chars_per_line=26)[:6]
        line_height = 28
        total_text_h = len(wrapped_lines) * line_height
        start_y = center_y - (total_text_h // 2)

        for i, line in enumerate(wrapped_lines):
            draw.text(
                (self.width // 2, start_y + i * line_height),
                line,
                fill=0,
                font=font_title,
                anchor="mm",
            )

        # 5. Bottom Info (Issue index + Date)
        bottom_y = self.height - margin - 70
        draw.line(
            [margin + 20, bottom_y - 20, self.width - margin - 20, bottom_y - 20],
            fill=0,
            width=2,
        )

        idx_label = f"NUMERO #{series_index}"
        draw.text(
            (self.width // 2, bottom_y),
            idx_label,
            fill=0,
            font=font_meta,
            anchor="mm",
        )

        date_val = date or datetime.now(UTC)
        date_str = date_val.strftime("%d/%m/%Y")
        draw.text(
            (self.width // 2, bottom_y + 30),
            date_str,
            fill=0,
            font=font_meta,
            anchor="mm",
        )

        # Export as PNG
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        if save_path:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(png_bytes)

        return png_bytes
