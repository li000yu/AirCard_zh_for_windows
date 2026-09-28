"""Wallet card tile - the Qt counterpart of SwiftUI's `WalletCardView`."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QToolButton,
                               QVBoxLayout, QWidget)

from .style import ACCENT, CONTROL_BG, SEPARATOR, TEXT_PRIMARY, TEXT_SECONDARY, Type

CARD_WIDTH = 290
CARD_HEIGHT = 182
CARD_RADIUS = 16.0


class _CardSurface(QWidget):
    """卡通用的圆角卡面，负责绘制皮肤预览或占位符。"""

    clicked = Signal()
    imageDropped = Signal(str)     # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setAcceptDrops(True)
        self._pixmap: QPixmap | None = None
        self._hovered = False
        self._targeted = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._targeted = False
        self.update()
        super().leaveEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self._targeted = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._targeted = False
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._targeted = False
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.imageDropped.emit(path)
                event.acceptProposedAction()
                self.update()
                return
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    # ------------------------------------------------------------------
    def _frame(self) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            CARD_RADIUS, CARD_RADIUS)
        return path

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = QRectF(self.rect())
        frame = self._frame()
        painter.setClipPath(frame)

        if self._pixmap and not self._pixmap.isNull():
            self._paint_skin(painter, rect)
        else:
            self._paint_placeholder(painter, rect)

        painter.setClipping(False)
        # 边框
        if self._targeted:
            pen = QPen(QColor(ACCENT), 2.0)
        elif self._hovered and self._pixmap is None:
            pen = QPen(QColor(120, 120, 128, 100), 1.0)
        else:
            pen = QPen(QColor(120, 120, 128, 60), 1.0)
        if self._pixmap is None:
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([6, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(frame)
        painter.end()

    def _paint_skin(self, painter: QPainter, rect: QRectF) -> None:
        pixmap = self._pixmap
        if pixmap is None:
            return
        image_rect = QRectF(pixmap.rect())
        scale = max(rect.width() / max(1.0, image_rect.width()),
                    rect.height() / max(1.0, image_rect.height()))
        width = image_rect.width() * scale
        height = image_rect.height() * scale
        target = QRectF(rect.center().x() - width / 2.0,
                        rect.center().y() - height / 2.0, width, height)
        painter.drawPixmap(target, pixmap, image_rect)

        gloss = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gloss.setColorAt(0.0, QColor(255, 255, 255, 46))
        gloss.setColorAt(0.5, QColor(255, 255, 255, 0))
        gloss.setColorAt(1.0, QColor(0, 0, 0, 31))
        painter.fillRect(rect, QBrush(gloss))

        if self._hovered:
            self._paint_pill(painter, "更换皮肤")

    def _paint_placeholder(self, painter: QPainter, rect: QRectF) -> None:
        base = QLinearGradient(rect.topLeft(), rect.bottomRight())
        base.setColorAt(0.0, QColor("#FDFDFE"))
        base.setColorAt(1.0, QColor("#EEEEF3"))
        painter.fillRect(rect, QBrush(base))

        # 右上角：非接支付波纹 + 卡片图标
        icon_font = QFont("Segoe UI Symbol", 14)
        painter.setFont(icon_font)
        painter.setPen(QColor(120, 120, 128, 130))
        painter.drawText(QRectF(rect.left() + 14, rect.top() + 12, 60, 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "\U0001F4F7")
        painter.drawText(QRectF(rect.right() - 14 - 60, rect.top() + 12, 60, 22),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         "\U0001F4B3")

        # 居中操作区
        active = self._hovered or self._targeted
        plus_font = QFont("Segoe UI Symbol", 30)
        painter.setFont(plus_font)
        if active or self._targeted:
            painter.setPen(QColor(ACCENT))
        else:
            painter.setPen(QColor(120, 120, 128, 180))
        painter.drawText(QRectF(rect.left(), rect.center().y() - 48,
                                rect.width(), 40.0),
                         Qt.AlignmentFlag.AlignCenter, "\u2795")

        painter.setFont(Type.subheadline(600))
        painter.setPen(QColor(TEXT_PRIMARY))
        title = "将图片放到此处" if self._targeted else "指定卡片皮肤"
        painter.drawText(QRectF(rect.left(), rect.center().y() - 2,
                                rect.width(), 20.0),
                         Qt.AlignmentFlag.AlignCenter, title)

        painter.setFont(Type.caption2())
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(QRectF(rect.left(), rect.center().y() + 18,
                                rect.width(), 16.0),
                         Qt.AlignmentFlag.AlignCenter, "点击浏览或直接拖入图片")

    def _paint_pill(self, painter: QPainter, text: str) -> None:
        rect = QRectF(self.rect())
        width = 108.0
        height = 24.0
        pill = QRectF(rect.center().x() - width / 2.0,
                      rect.bottom() - 12.0 - height, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 224))
        painter.drawRoundedRect(pill, height / 2.0, height / 2.0)
        painter.setFont(Type.caption(600))
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


class CardTileWidget(QFrame):
    """一张卡片：自绘卡面 + 编号、哈希、复制、勾选、删除。"""

    pickRequested = Signal()                      # noqa: N815
    clearRequested = Signal()                     # noqa: N815
    deleteRequested = Signal()                    # noqa: N815
    readRequested = Signal()                      # noqa: N815  只读读取当前卡面用于辨认
    backupRequested = Signal()                    # noqa: N815  备份设备上的原卡面
    restoreRequested = Signal()                   # noqa: N815  写回备份的原卡面
    restoreHistoryRequested = Signal()            # noqa: N815  手动挑选历史备份恢复
    imageDropped = Signal(str)                    # noqa: N815
    selectionChanged = Signal(bool)               # noqa: N815

    def __init__(self, card_hash: str, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.card_hash = card_hash
        self.index = index
        self.found_order = 0
        self.found_at = 0.0
        self._is_newest = False
        # 卡面上显示的是"待刷入的新皮肤"还是"设备原卡面预览"
        self._skin_ready = False
        self.setObjectName("cardTile")
        self.setProperty("selected", False)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.surface = _CardSurface(self)
        self.surface.clicked.connect(self.pickRequested)
        self.surface.imageDropped.connect(self.imageDropped)
        layout.addWidget(self.surface, 0, Qt.AlignmentFlag.AlignCenter)

        # 悬浮的清除按钮置于卡面右上角
        self.clear_button = QToolButton(self.surface)
        self.clear_button.setText("\u2715")
        self.clear_button.setToolTip("移除皮肤")
        self.clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_button.setStyleSheet(f"""
            QToolButton {{
                background: rgba(0, 0, 0, 140);
                color: #FFFFFF; border: none; border-radius: 10px;
                min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
                font-size: 11px; font-weight: 700;
            }}
            QToolButton:hover {{ background: rgba(0, 0, 0, 190); }}
        """)
        self.clear_button.move(CARD_WIDTH - 30, 10)
        self.clear_button.setVisible(False)
        self.clear_button.clicked.connect(self.clearRequested)

        layout.addWidget(self._build_info_row())
        layout.addWidget(self._build_action_row())

        # 卡面尺寸：读取卡面后显示（如「卡面尺寸：1125 × 2436 px」）
        self.size_label = QLabel("", self)
        self.size_label.setObjectName("cardSize")
        self.size_label.setFont(Type.caption2())
        self.size_label.setStyleSheet(
            "color: #8A8A8E; padding: 0 4px 2px;")
        self.size_label.setVisible(False)
        layout.addWidget(self.size_label)

        self.set_selected(False)

    # ------------------------------------------------------------------
    def _build_info_row(self) -> QWidget:
        from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton

        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(8)

        self.checkbox = QCheckBox(host)
        self.checkbox.setToolTip("参与刷入")
        self.checkbox.setChecked(False)
        self.checkbox.toggled.connect(self._on_toggled)
        row.addWidget(self.checkbox)

        self.index_label = QLabel(self._index_text(), host)
        self.index_label.setFont(Type.caption(600))
        row.addWidget(self.index_label)

        self.newest_label = QLabel("最新", host)
        self.newest_label.setStyleSheet(
            "background: #34C759; color: #FFFFFF; border-radius: 4px;"
            "padding: 1px 4px; font-size: 9px; font-weight: 700;")
        self.newest_label.setToolTip("最近一次扫描发现的卡片")
        self.newest_label.setVisible(False)
        row.addWidget(self.newest_label)

        self.hash_label = QLabel(self._hash_text(), host)
        self.hash_label.setObjectName("monoPill")
        row.addWidget(self.hash_label)

        self.copy_button = QPushButton("\u2398", host)
        self.copy_button.setObjectName(None)
        self.copy_button.setFlat(True)
        self.copy_button.setProperty("flat", True)
        self.copy_button.setFixedSize(18, 18)
        self.copy_button.setToolTip("复制完整哈希")
        self.copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_button.clicked.connect(self._copy_hash)
        row.addWidget(self.copy_button)

        row.addStretch(1)

        self.status_label = QLabel("\u2714", host)
        self.status_label.setToolTip("皮肤已就绪")
        self.status_label.setStyleSheet(f"color: #34C759; font-size: 12px;")
        self.status_label.setVisible(False)
        row.addWidget(self.status_label)

        self.delete_button = QPushButton("\U0001F5D1", host)
        self.delete_button.setProperty("flat", True)
        self.delete_button.setFixedSize(22, 18)
        self.delete_button.setToolTip("从列表移除")
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.clicked.connect(self.deleteRequested)
        row.addWidget(self.delete_button)
        return host

    def _build_action_row(self) -> QWidget:
        """第二、三行：读取卡面 / 备份卡面 / 恢复原皮 / 从历史恢复。"""
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

        host = QWidget(self)
        column = QVBoxLayout(host)
        column.setContentsMargins(4, 0, 4, 0)
        column.setSpacing(6)

        def action(text: str, tip: str) -> QPushButton:
            button = QPushButton(text, host)
            button.setObjectName("tileAction")
            button.setFont(Type.caption2())
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("""
                QPushButton#tileAction {
                    background: #FFFFFF; color: #2C2C2E;
                    border: 1px solid #D8D8DE; border-radius: 5px;
                    padding: 2px 7px;
                }
                QPushButton#tileAction:hover { background: #F0F0F5; }
                QPushButton#tileAction:disabled {
                    color: #B2B2BA; border-color: #E8E8EE; background: #FAFAFC;
                }
            """)
            return button

        # 第一行：读取卡面（只读辨认） + 备份卡面（备份原皮）
        row_top = QHBoxLayout()
        row_top.setContentsMargins(0, 0, 0, 0)
        row_top.setSpacing(6)
        self.read_button = action(
            "\U0001F50D 读取卡面",
            "只读地读取这张卡在 iPhone 上“当前”的卡面并显示出来，\n"
            "用于辨认它是哪张卡；不会写入备份，也不会改动原皮。\n"
            "（底层同样走 airlift 的“移动 → 读回 → 写回”流程，风险与备份相同）")
        self.read_button.clicked.connect(self.readRequested)
        row_top.addWidget(self.read_button)

        self.backup_button = action(
            "\U0001F4BE 备份卡面",
            "把 iPhone 上这张卡当前的卡面读取并备份到本机。\n"
            "有备份之后，随时可以点「恢复原皮」退回这张卡本来的样子，\n"
            "也能在「从历史备份恢复」里挑选任意一次历史备份。\n"
            "（底层走 airlift：卡面文件会被临时移动后立即写回原位）")
        self.backup_button.clicked.connect(self.backupRequested)
        row_top.addWidget(self.backup_button)
        row_top.addStretch(1)
        column.addLayout(row_top)

        # 第二行：恢复原皮（一键回退最新原皮） + 从历史备份恢复（手动挑选）
        row_bottom = QHBoxLayout()
        row_bottom.setContentsMargins(0, 0, 0, 0)
        row_bottom.setSpacing(6)
        self.restore_button = action("\u21A9 恢复原皮", "")
        self.restore_button.clicked.connect(self.restoreRequested)
        self.restore_button.setEnabled(False)
        row_bottom.addWidget(self.restore_button)
        self._apply_restore_tip(False)

        self.restore_history_button = action(
            "\U0001F4C2 从历史恢复",
            "手动浏览并选择任意一份历史备份卡面（软件备份目录中的带时间戳快照，\n"
            "或你手头任意一张卡面 PNG）写回这张卡。\n"
            "用于回到更早的、非“最新原皮”的某次卡面。")
        self.restore_history_button.clicked.connect(self.restoreHistoryRequested)
        self.restore_history_button.setEnabled(False)
        row_bottom.addWidget(self.restore_history_button)
        row_bottom.addStretch(1)
        column.addLayout(row_bottom)
        return host

    def _apply_restore_tip(self, backed_up: bool) -> None:
        self.restore_button.setToolTip(
            "把本机备份的初始卡面写回 iPhone，恢复到出厂卡面。"
            if backed_up else
            "本机还没有这张卡的备份，请先点「备份卡面」。")

    def set_backup_states(self, auto_exists: bool, manual_exists: bool) -> None:
        """存在自动备份 →「恢复原皮」可点；存在手动备份 →「从历史恢复」可点。

        两者独立判定：自动备份与手动备份各存各的（同名扁平文件、时间戳区分），
        互不冲突，因此两个按钮的可用状态也分别由各自的备份存在性决定。
        """
        self.restore_button.setEnabled(bool(auto_exists))
        self.restore_history_button.setEnabled(bool(manual_exists))
        self._apply_restore_tip(bool(auto_exists))

    def set_size(self, size) -> None:
        """在卡片下方显示卡面像素尺寸（如 (1125, 2436)），无则隐藏。"""
        if size and len(size) == 2 and size[0] and size[1]:
            self.size_label.setText(f"卡面尺寸：{size[0]} × {size[1]} px")
            self.size_label.setVisible(True)
        else:
            self.size_label.setVisible(False)

    # ------------------------------------------------------------------
    def set_index(self, index: int) -> None:
        self.index = index
        self.index_label.setText(self._index_text())

    def set_found_info(self, order: int, found_at: float,
                       is_newest: bool = False) -> None:
        """记录发现顺序与时刻，用于把电脑上的卡片和手机上的操作对应起来。"""
        self.found_order = int(order or 0)
        self.found_at = float(found_at or 0.0)
        self._is_newest = bool(is_newest)
        self.index_label.setText(self._index_text())
        self.index_label.setToolTip(self._tooltip_text())
        self.hash_label.setToolTip(self._tooltip_text())
        self.newest_label.setVisible(self._is_newest and self.found_order > 0)

    def _index_text(self) -> str:
        head = f"卡片 #{self.found_order if self.found_order > 0 else self.index + 1}"
        stamp = self._found_text()
        return f"{head} · {stamp}" if stamp else head

    def _found_text(self) -> str:
        if self.found_at <= 0:
            return ""
        return time.strftime("%H:%M:%S", time.localtime(self.found_at))

    def _tooltip_text(self) -> str:
        stamp = self._found_text()
        lines = [f"完整哈希：{self.card_hash}"]
        if self.found_order > 0:
            lines.append(f"发现顺序：第 {self.found_order} 张")
        if stamp:
            lines.append(f"发现时间：{stamp}")
        if self._is_newest:
            lines.append("这是最近一次扫描发现的卡片")
        lines.append("手机上是按你点击的顺序出现的，序号即对应点击次序。")
        return "\n".join(lines)

    def _hash_text(self) -> str:
        value = self.card_hash
        if len(value) <= 15:
            return value
        return f"{value[:8]}\u2026{value[-6:]}"

    def _copy_hash(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.card_hash)
        self.copy_button.setText("\u2714")
        self.copy_button.setToolTip("已复制！")

        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, self._restore_copy_icon)

    def _restore_copy_icon(self) -> None:
        self.copy_button.setText("\u2398")
        self.copy_button.setToolTip("复制完整哈希")

    def _on_toggled(self, checked: bool) -> None:
        self.setProperty("selected", checked)
        self.style().polish(self)
        self.selectionChanged.emit(checked)

    def is_selected(self) -> bool:
        return bool(self.checkbox.isChecked())

    def set_selected(self, value: bool) -> None:
        self.checkbox.blockSignals(True)
        self.checkbox.setChecked(value)
        self.checkbox.blockSignals(False)
        self.setProperty("selected", value)
        self.style().polish(self)

    def set_skin(self, pixmap: QPixmap | None, ready: bool = True) -> None:
        """显示卡面。

        ready=True  表示这是"待刷入的新皮肤"（显示 ✔ 与 ✕ 清除按钮）；
        ready=False 表示这是从设备读回的**原卡面备份预览**，
                    不算已指定皮肤，因此不显示 ✔ / ✕。
        """
        self.surface.set_pixmap(pixmap)
        shown = pixmap is not None and not pixmap.isNull()
        self._skin_ready = bool(ready) and shown
        self.clear_button.setVisible(shown and ready)
        self.status_label.setVisible(shown and ready)
        self.surface.setToolTip(
            "iPhone 上读取到的当前卡面（预览，尚未指定新皮肤）"
            if (shown and not ready) else "")

    def has_skin(self) -> bool:
        """是否真的指定了待刷入的皮肤（原卡面预览不算）。"""
        return self._skin_ready
