"""自适应流式布局，等价于 SwiftUI 的 LazyVGrid(.adaptive(minimum: 310))。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None,
                 margin: int = 0, spacing: int = 20) -> None:
        super().__init__(parent)
        if parent is not None:
            parent.setLayout(self)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list[QLayoutItem] = []

    # -- QLayout overrides --------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._reflow(QRect(0, 0, width, 0), apply_geometry=False)

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._reflow(rect, apply_geometry=True)

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # -- internals ----------------------------------------------------
    def _reflow(self, rect: QRect, apply_geometry: bool) -> int:
        left, top, _right, _bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -left, -top)
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            widget = item.widget()
            # 只跳过被显式隐藏的控件；父级不可见不影响几何计算，
            # 否则滚动区在隐藏状态下重建布局会得到 0 高度。
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if x != effective.x() and next_x - spacing > effective.right():
                x = effective.x()
                y += line_height + spacing
                next_x = x + hint.width() + spacing
            if apply_geometry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        if line_height == 0:
            return 0
        return y + line_height - effective.y() + self.getContentsMargins()[3]
