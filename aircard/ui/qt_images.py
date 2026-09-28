"""PIL <-> Qt 图像互转与预览缩略图工具。"""
from __future__ import annotations

from PIL import Image
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    """PIL 图像转 QPixmap（始终按 RGBA 处理以保证透明通道正确）。"""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    qt_image = ImageQt(image)
    return QPixmap.fromImage(qt_image.copy())


def preview_pixmap(path: str, width: int, height: int) -> QPixmap | None:
    """按 KeepAspectRatioByExpanding 规则生成一张固定尺寸的预览图。

    原图通常很大，直接交给 QPainter 每次重绘都要缩放；这里一次性降采样，
    既保证流畅也避免内存浪费。
    """
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    if pixmap.width() <= 0 or pixmap.height() <= 0:
        return None
    return pixmap.scaled(width, height,
                         Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                         Qt.TransformationMode.SmoothTransformation)
