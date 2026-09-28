"""Card artwork builder - replaces `card_assets.py` which shelled out to sips."""
from __future__ import annotations

import io

from PIL import Image, ImageOps

PNG_ASSET_NAMES = (
    "cardBackgroundCombined@3x.png",
    "cardBackgroundCombined@2x.png",
)
PDF_ASSET_NAME = "cardBackgroundCombined.pdf"

CARD_SIZE = (1536, 969)


def _fit_png(png_bytes: bytes) -> bytes:
    """Normalises any input to the exact Wallet card size."""
    with Image.open(io.BytesIO(png_bytes)) as image:
        image.load()
        image = image.convert("RGBA")
        fitted = ImageOps.fit(image, CARD_SIZE, method=Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        fitted.save(buffer, format="PNG")
        return buffer.getvalue()


def png_to_pdf(png_bytes: bytes) -> bytes:
    """sips -s format pdf equivalent: single page PDF at native pixel points."""
    with Image.open(io.BytesIO(png_bytes)) as image:
        image.load()
        rgb = image.convert("RGB")
        # Media box equals the pixel dimensions at 72 dpi, matching sips output.
        buffer = io.BytesIO()
        rgb.save(buffer, format="PDF", resolution=72.0, save_all=False)
        return buffer.getvalue()


def build_card_assets(png_bytes: bytes, normalise: bool = True) -> list[tuple[str, bytes]]:
    """Returns the three files that make up one Wallet card face."""
    payload = _fit_png(png_bytes) if normalise else png_bytes
    assets = [(name, payload) for name in PNG_ASSET_NAMES]
    assets.append((PDF_ASSET_NAME, png_to_pdf(payload)))
    return assets
