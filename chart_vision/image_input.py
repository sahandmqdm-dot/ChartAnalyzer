"""Validate and decode bounded base64 image uploads."""
from __future__ import annotations
import base64
import binascii
import re
from io import BytesIO

_DATA_URI = re.compile(r"^data:image/(?:png|jpeg|webp);base64,", re.I)
_ALLOWED = {"PNG", "JPEG", "WEBP"}


def decode_image(value: str, *, max_bytes: int = 3 * 1024 * 1024) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError("image_base64 must be a non-empty string")
    if not 1 <= int(max_bytes) <= 10 * 1024 * 1024:
        raise ValueError("max_bytes must be between 1 byte and 10 MiB")
    encoded = _DATA_URI.sub("", value.strip(), count=1)
    if len(encoded) > ((max_bytes + 2) // 3) * 4 + 8:
        raise ValueError("image exceeds upload size limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image_base64 is invalid") from exc
    if not raw or len(raw) > max_bytes:
        raise ValueError("image is empty or exceeds upload size limit")
    return raw


def inspect_image(data: bytes, *, max_dimension: int = 4096, max_pixels: int = 16_000_000):
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as im:
            fmt = str(im.format or "").upper()
            width, height = im.size
            if fmt not in _ALLOWED:
                raise ValueError("only PNG, JPEG and WEBP are accepted")
            if width < 32 or height < 32 or width > max_dimension or height > max_dimension or width * height > max_pixels:
                raise ValueError("image dimensions are outside the allowed range")
            im.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("uploaded data is not a valid supported image") from exc
    return {"format": fmt.lower(), "width": int(width), "height": int(height), "bytes": len(data)}
