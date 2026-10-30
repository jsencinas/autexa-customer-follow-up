"""Preparing photos for the vision model.

On CPU, the time to read a photo grows with its pixel count (the image becomes model tokens),
so photos are scaled down to a size that keeps handwriting legible.
"""

import io

from PIL import Image, ImageOps


def prepare_for_model(data: bytes, max_side: int) -> bytes:
    """Return a JPEG no larger than `max_side` on its long side, upright (EXIF) and in RGB."""
    with Image.open(io.BytesIO(data)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()
