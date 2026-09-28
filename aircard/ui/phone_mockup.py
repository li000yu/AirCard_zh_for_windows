"""Authentic iPhone lock-screen mockup with an interactive numeric keypad.

Painted with QPainter so the @3x grid geometry from the macOS build is kept
exactly: 305 x 382.67 point canvas inside a 326 x 512 phone body.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import QWidget

from .qt_images import pil_to_pixmap

PHONE_WIDTH = 326.0
PHONE_HEIGHT = 512.0
PHONE_RADIUS = 36.0

GRID_WIDTH = 305.0
GRID_HEIGHT = 1148.0 / 3.0          # 382.667
COL_WIDTH = 305.0 / 3.0             # 101.667
ROW_HEIGHT = 1148.0 / 12.0          # 95.667
BUTTON_DIAMETER = 75.0

HEADER_TOP = 12.0
HEADER_HEIGHT = 60.0
FOOTER_HEIGHT = 29.0
FOOTER_BOTTOM_PAD = 12.0
MIN_SPACER = 2.0

KEYPAD_BUTTONS = (
    ("1", "", 0, 0), ("2", "A B C", 0, 1), ("3", "D E F", 0, 2),
    ("4", "G H I", 1, 0), ("5", "J K L", 1, 1), ("6", "M N O", 1, 2),
    ("7", "P Q R S", 2, 0), ("8", "T U V", 2, 1), ("9", "W X Y Z", 2, 2),
    ("0", "+", 3, 1),
)


class PhoneMockup(QWidget):
    """Draws the device frame, lock header, dialer grid and footer."""

    keyClicked = Signal(str)
    keyDragged = Signal(str, QPointF)           # 增量位移（每次鼠标移动的相对量）
    posterDragged = Signal(QPointF)             # 增量位移（每次鼠标移动的相对量）
    fileDropped = Signal(str, str)          # (path, digit or "")
    keyContextRequested = Signal(str, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = "apply"                     # "apply" | "creator"
        self.creator_sub_mode = "poster"        # "poster" | "individual"
        self.mask_to_circles = False
        self.selected_digit: str | None = None
        self.key_pixmaps: dict[str, QPixmap] = {}
        self.theme_pixmaps: dict[str, QPixmap] = {}
        self.poster: QPixmap | None = None
        self.poster_zoom = 1.0
        self.poster_offset = QPointF(0.0, 0.0)
        self.has_key: Callable[[str], bool] | None = None

        self._dragging_key: str | None = None
        self._drag_start: QPointF | None = None
        self._pan_start: QPointF | None = None
        self.setMinimumSize(int(PHONE_WIDTH) + 20, int(PHONE_HEIGHT) + 20)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def phone_rect(self) -> QRectF:
        left = (self.width() - PHONE_WIDTH) / 2.0
        top = max(0.0, (self.height() - PHONE_HEIGHT) / 2.0)
        return QRectF(left, top, PHONE_WIDTH, PHONE_HEIGHT)

    def grid_rect(self) -> QRectF:
        phone = self.phone_rect()
        available = PHONE_HEIGHT - HEADER_TOP - HEADER_HEIGHT - MIN_SPACER * 2 \
            - FOOTER_HEIGHT - FOOTER_BOTTOM_PAD - HEADER_TOP
        extra = max(0.0, available - GRID_HEIGHT)
        top = phone.top() + HEADER_TOP + HEADER_HEIGHT + MIN_SPACER + extra / 2.0
        left = phone.left() + (PHONE_WIDTH - GRID_WIDTH) / 2.0
        return QRectF(left, top, GRID_WIDTH, GRID_HEIGHT)

    def button_rect(self, digit: str) -> QRectF:
        for value, _letters, row, col in KEYPAD_BUTTONS:
            if value != digit:
                continue
            grid = self.grid_rect()
            center_x = grid.left() + col * COL_WIDTH + COL_WIDTH / 2.0
            center_y = grid.top() + row * ROW_HEIGHT + ROW_HEIGHT / 2.0
            return QRectF(center_x - BUTTON_DIAMETER / 2.0,
                          center_y - BUTTON_DIAMETER / 2.0,
                          BUTTON_DIAMETER, BUTTON_DIAMETER)
        return QRectF()

    def digit_at(self, position: QPointF) -> str | None:
        for value, _letters, _row, _col in KEYPAD_BUTTONS:
            if self.button_rect(value).contains(position):
                return value
        return None

    def _inside_grid(self, position: QPointF) -> bool:
        return self.grid_rect().contains(position)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        phone = self.phone_rect()
        path = QPainterPath()
        path.addRoundedRect(phone, PHONE_RADIUS, PHONE_RADIUS)
        painter.setClipPath(path)

        painter.fillRect(phone, QColor(20, 20, 26))
        gradient = QLinearGradient(phone.topLeft(), phone.bottomLeft())
        gradient.setColorAt(0.0, QColor(255, 255, 255, 10))
        gradient.setColorAt(0.5, QColor(255, 255, 255, 0))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 76))
        painter.fillRect(phone, QBrush(gradient))

        self._paint_header(painter, phone)

        grid = self.grid_rect()
        painter.save()
        painter.setClipRect(grid)
        if self.mode == "creator":
            self._paint_creator_canvas(painter, grid)
        else:
            self._paint_apply_canvas(painter, grid)
        painter.restore()

        self._paint_footer(painter, phone)
        painter.setClipping(False)

        frame = QPainterPath()
        frame.addRoundedRect(phone, PHONE_RADIUS, PHONE_RADIUS)
        painter.setPen(QPen(QColor(255, 255, 255, 51), 1.5))
        painter.drawPath(frame)
        painter.end()

    def _paint_header(self, painter: QPainter, phone: QRectF) -> None:
        center_x = phone.center().x()
        top = phone.top() + HEADER_TOP
        # Dynamic Island style capsule with a lock glyph
        capsule = QRectF(center_x - 30.0, top, 60.0, 18.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 153))
        painter.drawRoundedRect(capsule, 9.0, 9.0)
        painter.setPen(QColor(255, 255, 255, 230))
        lock_font = QFont()
        lock_font.setPointSize(8)
        painter.setFont(lock_font)
        lock_rect = QRectF(capsule.center().x() - 18.0, capsule.top() - 7.0, 36.0, 16.0)
        painter.drawText(lock_rect, Qt.AlignmentFlag.AlignCenter, "\U0001F512")

        title_font = QFont()
        title_font.setPointSize(10)
        painter.setFont(title_font)
        painter.setPen(QColor(255, 255, 255, 242))
        painter.drawText(QRectF(phone.left(), capsule.bottom() + 2.0,
                                phone.width(), 20.0),
                         Qt.AlignmentFlag.AlignCenter, "输入密码")

        dot_top = capsule.bottom() + 2.0 + 20.0 + 2.0
        painter.setPen(QPen(QColor(255, 255, 255, 179), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        spacing = 10.0
        total = 6 * 9.0 + 5 * spacing
        start_x = center_x - total / 2.0 + 4.5
        for index in range(6):
            painter.drawEllipse(QPointF(start_x + index * (9.0 + spacing),
                                        dot_top + 4.5), 4.5, 4.5)

    def _paint_footer(self, painter: QPainter, phone: QRectF) -> None:
        font = QFont()
        font.setPointSize(10)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 230))
        bottom = phone.bottom() - FOOTER_BOTTOM_PAD - 16.0
        painter.drawText(QRectF(phone.left() + 28.0, bottom, 120.0, 16.0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "紧急")
        painter.drawText(QRectF(phone.right() - 28.0 - 120.0, bottom, 120.0, 16.0),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "取消")

    # -- canvas layers -------------------------------------------------
    def _paint_apply_canvas(self, painter: QPainter, grid: QRectF) -> None:
        self._paint_buttons(painter, grid, fill_keys=True,
                            source=self.theme_pixmaps, cover=True)

    def _paint_creator_canvas(self, painter: QPainter, grid: QRectF) -> None:
        if self.creator_sub_mode == "poster" and self.poster \
                and not self.mask_to_circles:
            self._paint_poster_layer(painter, grid)
            self._paint_buttons(painter, grid, fill_keys=False)
        elif self.creator_sub_mode == "poster" and self.mask_to_circles:
            self._paint_buttons(painter, grid, fill_keys=True,
                                source=self.key_pixmaps, cover=True)
        else:
            self._paint_buttons(painter, grid, fill_keys=True,
                                source=self.key_pixmaps, cover=False)

    def _paint_poster_layer(self, painter: QPainter, grid: QRectF) -> None:
        poster = self.poster
        if poster is None or poster.isNull():
            return
        image_aspect = poster.width() / max(1.0, poster.height())
        grid_aspect = GRID_WIDTH / GRID_HEIGHT
        zoom = max(0.1, self.poster_zoom)
        if image_aspect > grid_aspect:
            height = GRID_HEIGHT * zoom
            width = height * image_aspect
        else:
            width = GRID_WIDTH * zoom
            height = width / image_aspect
        center_x = grid.left() + GRID_WIDTH / 2.0 + self.poster_offset.x()
        center_y = grid.top() + GRID_HEIGHT / 2.0 + self.poster_offset.y()
        target = QRectF(center_x - width / 2.0, center_y - height / 2.0, width, height)
        painter.drawPixmap(target, poster, QRectF(poster.rect()))

    def _paint_buttons(self, painter: QPainter, grid: QRectF, fill_keys: bool,
                       source: dict[str, QPixmap] | None = None,
                       cover: bool = True) -> None:
        source = source or {}
        for value, letters, row, col in KEYPAD_BUTTONS:
            center_x = grid.left() + col * COL_WIDTH + COL_WIDTH / 2.0
            center_y = grid.top() + row * ROW_HEIGHT + ROW_HEIGHT / 2.0
            box = QRectF(center_x - BUTTON_DIAMETER / 2.0,
                         center_y - BUTTON_DIAMETER / 2.0,
                         BUTTON_DIAMETER, BUTTON_DIAMETER)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 46))
            painter.drawEllipse(box.center(), BUTTON_DIAMETER / 2.0, BUTTON_DIAMETER / 2.0)

            pixmap = source.get(value) if fill_keys else None
            if pixmap is not None and not pixmap.isNull():
                painter.save()
                clip = QPainterPath()
                clip.addEllipse(box)
                painter.setClipPath(clip)
                self._draw_key_image(painter, pixmap, box, cover)
                painter.restore()

            selected = self.mode == "creator" \
                and self.creator_sub_mode == "individual" \
                and self.selected_digit == value
            if selected:
                painter.setPen(QPen(QColor(168, 85, 247), 2.5))
            else:
                painter.setPen(QPen(QColor(255, 255, 255, 76), 0.8
                                    if self.mode == "apply" else 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box.center(), BUTTON_DIAMETER / 2.0, BUTTON_DIAMETER / 2.0)

            self._paint_key_text(painter, box.center(), value, letters)

    @staticmethod
    def _draw_key_image(painter: QPainter, pixmap: QPixmap, box: QRectF,
                        cover: bool) -> None:
        image_rect = QRectF(pixmap.rect())
        scale = max(box.width() / max(1.0, image_rect.width()),
                    box.height() / max(1.0, image_rect.height())) if cover else min(
            box.width() / max(1.0, image_rect.width()),
            box.height() / max(1.0, image_rect.height()))
        width = image_rect.width() * scale
        height = image_rect.height() * scale
        target = QRectF(box.center().x() - width / 2.0,
                        box.center().y() - height / 2.0, width, height)
        painter.drawPixmap(target, pixmap, image_rect)

    @staticmethod
    def _paint_key_text(painter: QPainter, center: QPointF, digit: str,
                        letters: str) -> None:
        digit_font = QFont()
        digit_font.setPointSize(21)
        digit_font.setWeight(QFont.Weight.Light)
        painter.setFont(digit_font)
        painter.setPen(QColor(255, 255, 255))
        offset = -6.0 if letters else 0.0
        painter.drawText(QRectF(center.x() - 60.0, center.y() - 22.0 + offset,
                                120.0, 30.0),
                         Qt.AlignmentFlag.AlignCenter, digit)
        if letters:
            letters_font = QFont()
            letters_font.setPointSize(7)
            letters_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(letters_font)
            painter.setPen(QColor(255, 255, 255, 230))
            painter.drawText(QRectF(center.x() - 60.0, center.y() + 2.0,
                                    120.0, 14.0),
                             Qt.AlignmentFlag.AlignCenter, letters)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        position = QPointF(event.position())
        digit = self.digit_at(position)
        if self.mode == "creator" and self.creator_sub_mode == "individual" and digit:
            has_image = self.has_key(digit) if self.has_key else False
            if event.button() == Qt.MouseButton.RightButton:
                self.keyContextRequested.emit(digit, event.globalPosition().toPoint())
                return
            if not has_image:
                self.keyClicked.emit(digit)
                return
            self._dragging_key = digit
            self._drag_start = position
            self.selected_digit = digit
            self.keyClicked.emit(digit)
            return
        if self.mode == "creator" and self.creator_sub_mode == "poster" \
                and self._inside_grid(position):
            self._pan_start = position
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        position = QPointF(event.position())
        if self._dragging_key and self._drag_start is not None:
            delta = position - self._drag_start
            self._drag_start = position
            self.keyDragged.emit(self._dragging_key, delta)
            return
        if self._pan_start is not None:
            delta = position - self._pan_start
            self._pan_start = position
            self.posterDragged.emit(delta)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging_key = None
        self._drag_start = None
        self._pan_start = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if not path:
            return
        digit = ""
        if self.mode == "creator" and self.creator_sub_mode == "individual":
            digit = self.digit_at(QPointF(event.position())) or ""
        self.fileDropped.emit(path, digit)
        event.acceptProposedAction()
