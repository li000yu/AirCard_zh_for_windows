"""Image processing for AirCard - the Pillow replacement for macOS `sips`.

Everything here reproduces what the app did through CoreGraphics/AppKit on
macOS: the 1536x969 card artwork fit, the seamless "poster slice" of the iOS
TelephonyUI @3x grid, and circular key cropping.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageOps

# Card artwork
CARD_SIZE = (1536, 969)

# iOS TelephonyUI @3x keypad grid
GRID_WIDTH = 915.0
GRID_HEIGHT = 1148.0
COL_WIDTH = 305.0
ROW_HEIGHT = 287.0

# Key tile used for circular key artwork
KEY_TARGET_SIZE = (225, 225)
KEY_CIRCLE_DIAMETER = 222.0
SLICE_CIRCLE_DIAMETER = 225.0


@dataclass(frozen=True)
class KeypadButton:
    digit: str
    letters: str
    row: int
    col: int


KEYPAD_BUTTONS: tuple[KeypadButton, ...] = (
    KeypadButton("1", "", 0, 0),
    KeypadButton("2", "A B C", 0, 1),
    KeypadButton("3", "D E F", 0, 2),
    KeypadButton("4", "G H I", 1, 0),
    KeypadButton("5", "J K L", 1, 1),
    KeypadButton("6", "M N O", 1, 2),
    KeypadButton("7", "P Q R S", 2, 0),
    KeypadButton("8", "T U V", 2, 1),
    KeypadButton("9", "W X Y Z", 2, 2),
    KeypadButton("0", "+", 3, 1),
)

KEYPAD_SUBTEXTS: dict[str, str] = {
    "0": "+", "1": "", "2": "A B C", "3": "D E F", "4": "G H I",
    "5": "J K L", "6": "M N O", "7": "P Q R S", "8": "T U V", "9": "W X Y Z",
}

SUPPORTED_LOCALES = (
    "en", "other", "ru", "uk", "es", "fr", "de", "it", "pt", "tr", "pl",
    "nl", "ja", "ko", "zh", "ar", "he",
)

SUPPORTED_IMAGE_FILTER = (
    "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.heic *.heif);;"
    "所有文件 (*.*)"
)

ThemeFilter = "密码主题包 (*.passthm *.passtheme *.zip);;所有文件 (*.*)"


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def load_image(path: str) -> Image.Image | None:
    try:
        image = Image.open(path)
        image.load()
        return image
    except Exception:
        return None


def _scaled(image: Image.Image, width: float, height: float) -> Image.Image:
    return image.convert("RGBA").resize(
        (max(1, int(round(width))), max(1, int(round(height)))),
        Image.Resampling.LANCZOS)


def prepare_card_image(source: str, destination: str) -> bool:
    """Aspect-fill centre crop to the Apple Wallet card size, written as PNG."""
    try:
        image = load_image(source)
        if image is None:
            return False
        image = image.convert("RGBA")
        fitted = ImageOps.fit(image, CARD_SIZE, method=Image.Resampling.LANCZOS)
        fitted.save(destination, format="PNG")
        return True
    except Exception:
        return False


def prepare_card_bytes(source: str) -> bytes:
    image = load_image(source)
    if image is None:
        raise RuntimeError("无法读取该图片")
    image = image.convert("RGBA")
    fitted = ImageOps.fit(image, CARD_SIZE, method=Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    fitted.save(buffer, format="PNG")
    return buffer.getvalue()


def to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def slice_poster(image: Image.Image, zoom: float = 1.0,
                 offset: tuple[float, float] = (0.0, 0.0),
                 mask_to_circles: bool = False) -> dict[str, Image.Image]:
    """Slices one wallpaper into per-key tiles on the @3x keypad grid.

    This mirrors `KeypadSlicer.slicePoster`; the CoreGraphics y-flip resolves to
    a plain top-left paste in Pillow's coordinate system.
    """
    width, height = image.size
    if width <= 0 or height <= 0:
        return {}

    zoom = max(0.1, float(zoom))
    image_aspect = width / height
    grid_aspect = GRID_WIDTH / GRID_HEIGHT
    if image_aspect > grid_aspect:
        scaled_height = GRID_HEIGHT * zoom
        scaled_width = scaled_height * image_aspect
    else:
        scaled_width = GRID_WIDTH * zoom
        scaled_height = scaled_width / image_aspect

    image_x = (GRID_WIDTH - scaled_width) / 2.0 + offset[0] * 3.0
    image_y = (GRID_HEIGHT - scaled_height) / 2.0 + offset[1] * 3.0

    scaled = _scaled(image, scaled_width, scaled_height)
    results: dict[str, Image.Image] = {}

    for button in KEYPAD_BUTTONS:
        zero_seamless = (not mask_to_circles and button.digit == "0")
        tile_width = GRID_WIDTH if zero_seamless else COL_WIDTH
        tile_height = ROW_HEIGHT
        cell_x = 0.0 if zero_seamless else button.col * COL_WIDTH
        cell_y = button.row * ROW_HEIGHT

        tile = Image.new("RGBA", (int(tile_width), int(tile_height)), (0, 0, 0, 0))
        tile.paste(scaled, (int(round(image_x - cell_x)), int(round(image_y - cell_y))))

        if mask_to_circles:
            tile = _apply_circle_mask(tile, SLICE_CIRCLE_DIAMETER)
        results[button.digit] = tile
    return results


def crop_to_circle(image: Image.Image, zoom: float = 1.0,
                   offset: tuple[float, float] = (0.0, 0.0)) -> Image.Image:
    """Mirrors `KeypadSlicer.cropToCircle` for individually edited keys."""
    width, height = image.size
    target_width, target_height = KEY_TARGET_SIZE
    zoom = max(0.1, float(zoom))
    base_scale = max(KEY_CIRCLE_DIAMETER / width, KEY_CIRCLE_DIAMETER / height) * zoom
    scaled_width = width * base_scale
    scaled_height = height * base_scale

    circle_x = (target_width - KEY_CIRCLE_DIAMETER) / 2.0
    circle_y = (target_height - KEY_CIRCLE_DIAMETER) / 2.0
    dest_x = circle_x + (KEY_CIRCLE_DIAMETER - scaled_width) / 2.0 + offset[0] * 3.0
    dest_y = circle_y + (KEY_CIRCLE_DIAMETER - scaled_height) / 2.0 + offset[1] * 3.0

    canvas = Image.new("RGBA", KEY_TARGET_SIZE, (0, 0, 0, 0))
    canvas.paste(_scaled(image, scaled_width, scaled_height),
                 (int(round(dest_x)), int(round(dest_y))))
    return _apply_circle_mask(canvas, KEY_CIRCLE_DIAMETER)


def _apply_circle_mask(tile: Image.Image, diameter: float) -> Image.Image:
    mask = Image.new("L", tile.size, 0)
    draw = ImageDraw.Draw(mask)
    left = (tile.width - diameter) / 2.0
    top = (tile.height - diameter) / 2.0
    draw.ellipse((left, top, left + diameter, top + diameter), fill=255)
    alpha = tile.split()[-1]
    tile.putalpha(ImageChops.multiply(alpha, mask))
    return tile
