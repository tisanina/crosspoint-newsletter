"""Unit tests for ImageOptimizer module."""

from __future__ import annotations

import io
from PIL import Image

from crosspoint_newsletter.transform.image_optimizer import ImageOptimizer


def _make_test_image(width: int, height: int, mode: str = "RGB", color: tuple = (200, 100, 50)) -> bytes:
    """Helper creating raw image bytes."""
    img = Image.new(mode, (width, height), color=color)
    buf = io.BytesIO()
    fmt = "PNG" if "A" in mode or mode == "P" else "JPEG"
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_optimizer_resizes_proportionally() -> None:
    opt = ImageOptimizer(max_width=480, max_height=800)
    # 1200x800 image (landscape)
    raw = _make_test_image(1200, 800)
    res = opt.optimize(raw, filename_hint="large_photo.jpg")

    assert res is not None
    opt_bytes, mime, stats = res
    assert stats.optimized_dimensions[0] <= 480
    assert stats.optimized_dimensions[1] <= 800
    # Proportions: 1200x800 -> 480x320
    assert stats.optimized_dimensions == (480, 320)


def test_optimizer_converts_to_grayscale() -> None:
    opt = ImageOptimizer()
    raw = _make_test_image(300, 300, mode="RGB", color=(255, 0, 0))
    res = opt.optimize(raw)

    assert res is not None
    opt_bytes, mime, stats = res
    assert stats.is_grayscale

    with Image.open(io.BytesIO(opt_bytes)) as img:
        assert img.mode == "L"


def test_optimizer_handles_transparency_compositing_white_bg() -> None:
    opt = ImageOptimizer()
    # RGBA with transparency
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    # Draw non-transparent black rectangle
    for x in range(50, 150):
        for y in range(50, 150):
            img.putpixel((x, y), (50, 50, 50, 255))

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    res = opt.optimize(buf.getvalue(), filename_hint="transparent_icon.png")
    assert res is not None
    opt_bytes, mime, stats = res

    with Image.open(io.BytesIO(opt_bytes)) as out_img:
        assert out_img.mode == "L"
        # Background pixel should be white (255)
        assert out_img.getpixel((10, 10)) == 255
        # Foreground pixel should be dark
        assert out_img.getpixel((100, 100)) < 100


def test_optimizer_skips_tiny_tracking_pixels() -> None:
    opt = ImageOptimizer()
    tiny = _make_test_image(1, 1)
    res = opt.optimize(tiny)
    assert res is None

    tiny2 = _make_test_image(2, 2)
    res2 = opt.optimize(tiny2)
    assert res2 is None


def test_optimizer_handles_corrupt_bytes_gracefully() -> None:
    opt = ImageOptimizer()
    corrupt = b"this is not an image at all"
    res = opt.optimize(corrupt)
    assert res is None
