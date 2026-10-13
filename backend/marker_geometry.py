"""Independent frame dimensions shared by layout and PDF export."""
import math


def marker_geometry(item, style):
    def bounded(key, fallback, minimum, maximum):
        try:
            value = float(style.get(key) or fallback)
        except (TypeError, ValueError):
            value = fallback
        if not math.isfinite(value):
            value = fallback
        return max(minimum, min(maximum, value))

    size = bounded("frameSize", 28.0, 18.0, 64.0)
    configured_font = bounded("fontSize", 15.0, 7.0, 24.0)
    split = style.get("shape") == "split-circle"
    text_length = max(len(str(item.get("number") or "?")), len(str(item.get("secondaryText") or "")) if split else 0)
    width = max(size * 1.35, 10.0 + text_length * configured_font * 0.68) if style.get("shape") == "rectangle" else size
    font_size = min(configured_font, max(4.0, (width - 6.0) / max(text_length * 0.58, 1.0)))
    if split:
        font_size = min(font_size, max(4.0, (size - 6.0) / 2.4))
    return width, size, font_size
