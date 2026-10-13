"""Safe PDF optimization for files consumed and served by analysis jobs."""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import uuid
from typing import Any, Callable

import fitz


MAX_REPEATED_IMAGE_EDGE = 4096
MIN_REPEATED_IMAGE_PIXELS = 16_000_000
JPEG_QUALITY = 88


def _validate_pdf(path: Path, expected_pages: int) -> None:
    """Open the result and rasterize representative pages before publishing it."""

    with fitz.open(path) as document:
        if document.page_count != expected_pages:
            raise ValueError(f"PDF优化后页数异常：{document.page_count} != {expected_pages}")
        if document.page_count:
            page_numbers = {0, document.page_count - 1}
            for page_number in page_numbers:
                pixmap = document.load_page(page_number).get_pixmap(matrix=fitz.Matrix(0.1, 0.1))
                if pixmap.width <= 0 or pixmap.height <= 0:
                    raise ValueError(f"PDF优化后第 {page_number + 1} 页无法渲染")


def _downsample_repeated_images(document: fitz.Document) -> tuple[int, int]:
    """Reduce pathological, reused raster resources while preserving page geometry."""

    usage: Counter[int] = Counter()
    masks: dict[int, int] = {}
    for page in document:
        for image in page.get_images(full=True):
            xref = int(image[0])
            usage[xref] += 1
            masks[xref] = int(image[1] or 0)

    changed = 0
    pixels_avoided = 0
    for xref, count in usage.items():
        if count < 2 or masks.get(xref):
            continue
        pixmap = fitz.Pixmap(document, xref)
        if pixmap.alpha or pixmap.n not in {1, 3}:
            continue
        original_width, original_height = pixmap.width, pixmap.height
        if (
            max(original_width, original_height) <= MAX_REPEATED_IMAGE_EDGE
            or original_width * original_height < MIN_REPEATED_IMAGE_PIXELS
        ):
            continue
        factor = 0
        while max(original_width, original_height) // (2 ** (factor + 1)) > MAX_REPEATED_IMAGE_EDGE:
            factor += 1
        if factor == 0:
            factor = 1
        pixmap.shrink(factor)
        encoded = pixmap.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
        document.update_stream(xref, encoded, compress=False)
        document.xref_set_key(xref, "Width", str(pixmap.width))
        document.xref_set_key(xref, "Height", str(pixmap.height))
        document.xref_set_key(xref, "BitsPerComponent", "8")
        document.xref_set_key(xref, "ColorSpace", "/DeviceGray" if pixmap.n == 1 else "/DeviceRGB")
        document.xref_set_key(xref, "Filter", "/DCTDecode")
        changed += 1
        pixels_avoided += count * (
            original_width * original_height - pixmap.width * pixmap.height
        )
    return changed, pixels_avoided


def optimize_pdf(
    source: Path,
    output: Path,
    *,
    cancellation_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Deduplicate streams, downsample repeated huge images, and atomically publish."""

    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_dedup = output.with_name(f".{output.name}.{uuid.uuid4().hex}.dedup.tmp")
    temporary_final = output.with_name(f".{output.name}.{uuid.uuid4().hex}.final.tmp")
    original_size = source.stat().st_size
    try:
        with fitz.open(source) as document:
            page_count = document.page_count
            document.save(
                temporary_dedup,
                garbage=4,
                deflate=True,
                deflate_images=True,
                use_objstms=1,
                compression_effort=50,
            )
        if cancellation_check:
            cancellation_check()
        downsampled_images = 0
        pixels_avoided = 0
        with fitz.open(temporary_dedup) as optimized:
            repeated_counts = Counter(
                int(image[0])
                for page in optimized
                for image in page.get_images(full=True)
            )
            shared_image_objects = sum(1 for count in repeated_counts.values() if count > 1)
            downsampled_images, pixels_avoided = _downsample_repeated_images(optimized)
            if downsampled_images:
                optimized.save(
                    temporary_final,
                    garbage=4,
                    deflate=True,
                    deflate_images=True,
                    use_objstms=1,
                    compression_effort=50,
                )
        if not downsampled_images:
            os.replace(temporary_dedup, temporary_final)
        if cancellation_check:
            cancellation_check()
        _validate_pdf(temporary_final, page_count)
        optimized_size = temporary_final.stat().st_size
        # Preserve the optimized structure even when a small PDF grows slightly: Range
        # loading still benefits, but reject clearly pathological optimizer output.
        if original_size and optimized_size > original_size * 1.25:
            raise ValueError("PDF优化结果异常增大，已保留原文件")
        os.replace(temporary_final, output)
        return {
            "status": "complete",
            "sourceFile": source.name,
            "optimizedFile": output.name,
            "pageCount": page_count,
            "originalSize": original_size,
            "optimizedSize": optimized_size,
            "savedBytes": max(0, original_size - optimized_size),
            "savedPercent": round(max(0.0, 1 - optimized_size / max(1, original_size)) * 100, 2),
            "sharedImageObjects": shared_image_objects,
            "downsampledImages": downsampled_images,
            "decodedPixelsAvoided": pixels_avoided,
        }
    finally:
        temporary_dedup.unlink(missing_ok=True)
        temporary_final.unlink(missing_ok=True)
