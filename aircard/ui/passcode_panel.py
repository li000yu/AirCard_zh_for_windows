"""「密码主题 (.passthm)」面板 - 应用现成主题 + 主题制作器。"""
from __future__ import annotations

import zipfile
from typing import Any

from PIL import Image

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QLabel, QMenu, QPushButton, QSlider, QVBoxLayout, QWidget)

from ..core.imaging import SUPPORTED_IMAGE_FILTER, ThemeFilter, crop_to_circle, load_image, slice_poster
from ..core.passthm import (bold_codes, bold_label, export_theme, inspect_passthm,
                            language_codes, language_label, stage_temporary_theme,
                            version_codes, version_label)
from .context import AppContext
from .phone_mockup import PhoneMockup
from .qt_images import pil_to_pixmap, preview_pixmap
from .style import CONTROL_BG, ORANGE, PURPLE, Type, rgba
from .workers import FunctionWorker, PasscodeFlashWorker, ThemeInspectWorker, submit

ZOOM_MIN = 0.5
ZOOM_MAX = 3.0


def clear_layout(layout) -> None:
    """递归清空一个布局，确保子布局里的控件也被真正删除。

    只对顶层 takeAt 的话，`addLayout()` 塞进来的行布局（QLabel / QSlider 等）
    不会被 deleteLater —— 控件以旧几何位置悬在原地，视觉上叠在新内容上，
    表现为"文字与图案重叠的残影"。
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            clear_layout(sub)
            sub.deleteLater()


class DropCard(QFrame):
    """虚线拖拽区，等价于 SwiftUI 里的 `.onDrop` 卡片。"""

    fileDropped = Signal(str)   # noqa: N815

    def __init__(self, tint: str = PURPLE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tint = tint
        # objectName 用于把 QSS 限定在拖拽区自身：
        # 以前写 `QFrame {{ ... }}` 会命中所有后代（QLabel 也是 QFrame），
        # 导致虚线框直接套在每一行文字上（文字与边框重叠的根因之一）。
        self.setObjectName("dropCard")
        self.setAcceptDrops(True)
        self.set_object(False)

    def set_object(self, active: bool) -> None:
        # 颜色必须用 rgba()：8 位 hex 会被 Qt 按 #AARRGGBB 误读成绿色。
        border = rgba(self._tint, 230) if active else rgba(self._tint, 89)
        self.setStyleSheet(
            f"QFrame#dropCard {{ background: rgba(120,120,128,0.04); "
            f"border: 1.5px dashed {border}; border-radius: 12px; }}")

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self.set_object(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.set_object(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.set_object(False)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.fileDropped.emit(path)
                event.acceptProposedAction()


class PasscodePanel(QWidget):
    """密码主题工作区（应用主题 / 主题制作器）。"""

    stateChanged = Signal()   # noqa: N815

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.mode = "apply"                 # "apply" | "creator"
        self.sub_mode = "poster"            # "poster" | "individual"
        self.loaded_theme: dict[str, Any] | None = None
        self.is_inspecting = False

        self.poster_image: Image.Image | None = None
        self.poster_path: str | None = None
        self.poster_pixmap: QPixmap | None = None
        self.poster_zoom = 1.0
        self.poster_offset = (0.0, 0.0)
        self.mask_to_circles = False
        self.sliced_keys: dict[str, Image.Image] = {}
        self.custom_keys: dict[str, Image.Image] = {}
        self.raw_individual: dict[str, Image.Image] = {}
        self.individual_offsets: dict[str, tuple[float, float]] = {}
        self.individual_zooms: dict[str, float] = {}
        self.selected_digit: str | None = None

        self._render_seq = 0
        self._render_busy = False
        self._render_pending: tuple[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_toolbar())
        root.addWidget(self._separator())

        self.workspace = QWidget(self)
        self.workspace_layout = QHBoxLayout(self.workspace)
        self.workspace_layout.setContentsMargins(20, 14, 20, 14)
        self.workspace_layout.setSpacing(20)
        root.addWidget(self.workspace, 1)

        self._mount_apply_workspace()
        self._mount_creator_workspace()
        self.setAcceptDrops(True)
        self._sync_mode()

    # ------------------------------------------------------------------
    # 工具条
    # ------------------------------------------------------------------
    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #D9D9DE; max-height: 1px; border: none;")
        return line

    def _segmented(self, options: tuple[tuple[str, str], ...],
                   initial: str, host: QWidget) -> tuple[QWidget, QButtonGroup]:
        frame = QFrame(host)
        frame.setObjectName("segmentHost")
        frame.setFixedHeight(30)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        group = QButtonGroup(frame)
        group.setExclusive(True)
        for code, label_text in options:
            button = QPushButton(label_text, frame)
            button.setProperty("segment", "true")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setChecked(code == initial)
            button.setProperty("value", code)
            layout.addWidget(button)
            group.addButton(button)
        return frame, group

    def _build_toolbar(self) -> QWidget:
        host = QWidget(self)
        host.setFixedHeight(48)
        layout = QHBoxLayout(host)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        frame, group = self._segmented(
            (("apply", "应用密码主题"), ("creator", "主题制作器")), "apply", host)
        layout.addWidget(frame)
        self.mode_group = group
        group.buttonClicked.connect(self._on_mode_button)

        self.choose_theme_button = QPushButton("选择 .passthm 文件…", host)
        self.choose_theme_button.setProperty("cta", "purple")
        self.choose_theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_theme_button.clicked.connect(self.choose_theme_file)
        layout.addWidget(self.choose_theme_button)

        self.choose_poster_button = QPushButton("选择海报…", host)
        self.choose_poster_button.setProperty("cta", "purple")
        self.choose_poster_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_poster_button.clicked.connect(self.choose_poster)
        layout.addWidget(self.choose_poster_button)

        self.export_button = QPushButton("导出 .passthm…", host)
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.clicked.connect(self.export_created_theme)
        layout.addWidget(self.export_button)

        layout.addStretch(1)

        target_label = QLabel("目标版本：", host)
        target_label.setFont(Type.caption())
        target_label.setObjectName("secondary")
        layout.addWidget(target_label)

        self.version_combo = QComboBox(host)
        self.version_combo.setFixedWidth(205)
        for code in version_codes():
            self.version_combo.addItem(version_label(code), code)
        index = self.version_combo.findData(self.ctx.target_version)
        self.version_combo.setCurrentIndex(max(0, index))
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        layout.addWidget(self.version_combo)

        dot = QLabel("·", host)
        dot.setObjectName("secondary")
        layout.addWidget(dot)

        self.clear_theme_button = QPushButton("清除主题", host)
        self.clear_theme_button.setProperty("link", "danger")
        self.clear_theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_theme_button.clicked.connect(self.clear_theme)
        layout.addWidget(self.clear_theme_button)

        self.clear_creator_button = QPushButton("全部清除", host)
        self.clear_creator_button.setProperty("link", "danger")
        self.clear_creator_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_creator_button.clicked.connect(self.clear_creator)
        layout.addWidget(self.clear_creator_button)
        return host

    def _on_mode_button(self, button: QPushButton) -> None:
        value = button.property("value")
        if value and value != self.mode:
            self.set_mode(value)

    def set_mode(self, mode: str) -> None:
        self.mode = "creator" if mode == "creator" else "apply"
        for button in self.mode_group.buttons():
            button.setChecked(button.property("value") == self.mode)
        self._sync_mode()
        self.stateChanged.emit()

    # ------------------------------------------------------------------
    # 工作区切换
    # ------------------------------------------------------------------
    def _left_column(self) -> QWidget:
        host = QWidget(self.workspace)
        host.setFixedWidth(320)
        column = QVBoxLayout(host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        column.setAlignment(Qt.AlignmentFlag.AlignTop)
        return host

    def _mount_apply_workspace(self) -> None:
        self.apply_page = QWidget(self.workspace)
        layout = QHBoxLayout(self.apply_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        left = self._left_column()
        left.layout().addWidget(self._build_apply_controls())
        left.layout().addWidget(self._build_target_card())
        left.layout().addStretch(1)
        layout.addWidget(left)

        right = QWidget(self.apply_page)
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        apply_head, self.apply_preview_hint = self._preview_header("锁屏键盘预览")
        column.addLayout(apply_head)
        self.apply_phone = PhoneMockup(right)
        column.addWidget(self.apply_phone, 1, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(right, 1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def _mount_creator_workspace(self) -> None:
        self.creator_page = QWidget(self.workspace)
        layout = QHBoxLayout(self.creator_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)

        left = self._left_column()
        left.layout().addWidget(self._build_creator_controls())
        left.layout().addWidget(self._build_target_card())
        left.layout().addStretch(1)
        layout.addWidget(left)

        right = QWidget(self.creator_page)
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        creator_head, self.creator_preview_hint = self._preview_header(
            "可交互 iPhone 锁屏预览")
        column.addLayout(creator_head)
        self.creator_phone = PhoneMockup(right)
        self.creator_phone.mode = "creator"
        self.creator_phone.creator_sub_mode = "poster"
        self.creator_phone.has_key = self._has_key
        self.creator_phone.fileDropped.connect(self._on_phone_drop)
        self.creator_phone.keyClicked.connect(self._on_key_clicked)
        self.creator_phone.keyDragged.connect(self._on_key_dragged)
        self.creator_phone.posterDragged.connect(self._on_poster_dragged)
        self.creator_phone.keyContextRequested.connect(self._on_key_context)
        column.addWidget(self.creator_phone, 1, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(right, 1)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def _preview_header(self, title: str, trailing: str = "") -> tuple[QHBoxLayout, QLabel]:
        layout = QHBoxLayout()
        layout.setContentsMargins(6, 0, 6, 0)
        label = QLabel(title)
        label.setFont(Type.caption(600))
        label.setObjectName("secondary")
        layout.addWidget(label)
        layout.addStretch(1)
        hint = QLabel(trailing)
        hint.setFont(Type.caption2())
        hint.setObjectName("secondary")
        layout.addWidget(hint)
        return layout, hint

    def _sync_mode(self) -> None:
        creator = self.mode == "creator"
        for page in (self.apply_page, self.creator_page):
            self.workspace_layout.removeWidget(page)
            page.setParent(None)
        self.workspace_layout.addWidget(
            self.creator_page if creator else self.apply_page)
        self.choose_theme_button.setVisible(not creator)
        self.clear_theme_button.setVisible(not creator)
        self.choose_poster_button.setVisible(creator)
        self.export_button.setVisible(creator)
        self.clear_creator_button.setVisible(creator)
        self._sync_creator_controls()
        self._sync_apply_controls()
        self._sync_target_card()

    # ------------------------------------------------------------------
    # 左栏：应用主题
    # ------------------------------------------------------------------
    def _build_apply_controls(self) -> QWidget:
        card = QFrame()
        card.setObjectName("panel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        title = QLabel("密码主题文件")
        title.setFont(Type.caption(600))
        title.setObjectName("secondary")
        layout.addWidget(title)

        self.apply_placeholder = self._build_theme_placeholder()
        layout.addWidget(self.apply_placeholder)

        self.theme_chip_host = QWidget(card)
        chip_layout = QVBoxLayout(self.theme_chip_host)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(10)
        self.theme_chip_host.setVisible(False)
        layout.addWidget(self.theme_chip_host)
        return card

    def _build_theme_placeholder(self) -> DropCard:
        card = DropCard(PURPLE)
        column = QVBoxLayout(card)
        column.setContentsMargins(10, 12, 10, 20)
        column.setSpacing(10)
        column.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        icon = QLabel("\U0001F4E5")
        icon.setStyleSheet(f"font-size: 32px; color: {PURPLE};")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(icon)

        title = QLabel("将 .passthm 文件拖到此处")
        title.setFont(Type.caption(600))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)

        hint = QLabel("支持来自 Cowabunga 或 Nugget 的 .passthm、.passtheme "
                      "或 .zip 主题包")
        hint.setFont(Type.caption2())
        hint.setObjectName("secondary")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(hint)

        button = QPushButton("选择文件…")
        button.setProperty("cta", "purple")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.choose_theme_file)
        column.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        card.fileDropped.connect(self.inspect_theme)
        return card

    def _sync_apply_controls(self) -> None:
        theme = self.loaded_theme
        self.theme_chip_host.setVisible(theme is not None)
        self.apply_placeholder.setVisible(theme is None)
        self.clear_theme_button.setEnabled(theme is not None)

        layout = self.theme_chip_host.layout()
        clear_layout(layout)
        if theme is None:
            if self.is_inspecting:
                waiting = QLabel("正在解析主题包…")
                waiting.setFont(Type.caption())
                waiting.setObjectName("secondary")
                waiting.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(waiting)
                self.theme_chip_host.setVisible(True)
                self.apply_placeholder.setVisible(False)
            self._sync_phone_preview()
            return

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel("\U0001F510")
        icon.setStyleSheet(f"font-size: 28px; color: {PURPLE};")
        head.addWidget(icon)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(theme.get("name", ""))
        name.setFont(Type.headline_bold())
        name.setWordWrap(True)
        texts.addWidget(name)
        chip = QLabel(theme.get("detectedVersion", "TelephonyUI-10"))
        chip.setObjectName("themeChip")
        texts.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)
        head.addLayout(texts, 1)
        layout.addLayout(head)

        summary = QLabel(f"{theme.get('fileCount', 0)} 个素材已载入 · 可刷入 iPhone")
        summary.setFont(Type.caption2())
        summary.setObjectName("secondary")
        layout.addWidget(summary)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        edit = QPushButton("在制作器中编辑")
        edit.setProperty("cta", "purple")
        edit.setCursor(Qt.CursorShape.PointingHandCursor)
        edit.clicked.connect(self.edit_loaded_theme_in_creator)
        actions.addWidget(edit)
        change = QPushButton("更换…")
        change.setCursor(Qt.CursorShape.PointingHandCursor)
        change.clicked.connect(self.choose_theme_file)
        actions.addWidget(change)
        clear = QPushButton("清除")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear_theme)
        actions.addWidget(clear)
        layout.addLayout(actions)
        self._sync_phone_preview()

    # ------------------------------------------------------------------
    # 左栏：主题制作器
    # ------------------------------------------------------------------
    def _build_creator_controls(self) -> QWidget:
        card = QFrame()
        card.setObjectName("panel")
        self.creator_layout = QVBoxLayout(card)
        self.creator_layout.setContentsMargins(14, 14, 14, 14)
        self.creator_layout.setSpacing(12)
        return card

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #E4E4E8; max-height: 1px; border: none;")
        return line

    def _build_poster_placeholder(self) -> DropCard:
        card = DropCard(PURPLE)
        column = QVBoxLayout(card)
        column.setContentsMargins(10, 10, 10, 16)
        column.setSpacing(8)
        column.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        icon = QLabel("\U0001F5BC")
        icon.setStyleSheet(f"font-size: 26px; color: {PURPLE};")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(icon)
        title = QLabel("将海报或壁纸拖到此处")
        title.setFont(Type.caption(500))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)
        button = QPushButton("选择图片…")
        button.setProperty("cta", "purple")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.choose_poster)
        column.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        card.fileDropped.connect(self.set_poster_file)
        return card

    def _sync_creator_controls(self) -> None:
        layout = self.creator_layout
        clear_layout(layout)

        frame, group = self._segmented(
            (("poster", "海报切片（拼图）"), ("individual", "逐键自定义")),
            self.sub_mode, layout.parentWidget())
        layout.addWidget(frame)
        self.sub_mode_group = group
        group.buttonClicked.connect(self._on_sub_mode_button)

        layout.addWidget(self._divider())
        if self.sub_mode == "poster":
            self._mount_poster_controls(layout)
        else:
            self._mount_individual_controls(layout)

    def _on_sub_mode_button(self, button: QPushButton) -> None:
        value = button.property("value")
        if value and value != self.sub_mode:
            self.set_sub_mode(value)

    def set_sub_mode(self, value: str) -> None:
        self.sub_mode = "individual" if value == "individual" else "poster"
        self.selected_digit = None
        self._sync_creator_controls()
        self.stateChanged.emit()

    def _mount_poster_controls(self, layout: QVBoxLayout) -> None:
        title = QLabel("海报图像")
        title.setFont(Type.caption(600))
        title.setObjectName("secondary")
        layout.addWidget(title)

        if self.poster_image is not None:
            host = QFrame()
            # 作用域限定：无选择器的 setStyleSheet 会波及所有子控件。
            host.setObjectName("posterHost")
            host.setStyleSheet("QFrame#posterHost { background: #FFFFFF; "
                               "border-radius: 10px; border: none; }")
            row = QHBoxLayout(host)
            row.setContentsMargins(10, 10, 10, 10)
            row.setSpacing(12)
            thumb = QLabel()
            thumb.setFixedSize(50, 64)
            pixmap = preview_pixmap(self.poster_path or "", 100, 128)
            if pixmap is not None:
                thumb.setPixmap(pixmap.scaled(50, 64,
                                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation))
                thumb.setScaledContents(False)
            row.addWidget(thumb)
            texts = QVBoxLayout()
            texts.setSpacing(6)
            label = QLabel("海报已载入")
            label.setFont(Type.subheadline(500))
            texts.addWidget(label)
            buttons = QHBoxLayout()
            buttons.setSpacing(8)
            change = QPushButton("更换…")
            change.setCursor(Qt.CursorShape.PointingHandCursor)
            change.clicked.connect(self.choose_poster)
            buttons.addWidget(change)
            remove = QPushButton("移除")
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(self.clear_creator)
            buttons.addWidget(remove)
            texts.addLayout(buttons)
            row.addLayout(texts, 1)
            layout.addWidget(host)
        else:
            layout.addWidget(self._build_poster_placeholder())

        layout.addWidget(self._divider())

        style_title = QLabel("切片方式")
        style_title.setFont(Type.caption(600))
        style_title.setObjectName("secondary")
        layout.addWidget(style_title)

        frame, group = self._segmented(
            (("seamless", "无缝海报"), ("circles", "圆形按键")),
            "circles" if self.mask_to_circles else "seamless",
            layout.parentWidget())
        layout.addWidget(frame)
        group.buttonClicked.connect(self._on_style_button)

        hint = QLabel("画面被裁剪成彼此独立的圆形按键图标。"
                      if self.mask_to_circles else
                      "画面以整幅海报的形式横跨各按键，不做圆形裁切（Adobe Dog 风格）。")
        hint.setFont(Type.caption2())
        hint.setObjectName("secondary")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addWidget(self._divider())

        head = QHBoxLayout()
        zoom_title = QLabel("缩放与构图")
        zoom_title.setFont(Type.caption(600))
        zoom_title.setObjectName("secondary")
        head.addWidget(zoom_title)
        head.addStretch(1)
        reset = QPushButton("重置位置")
        reset.setProperty("link", "true")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setEnabled(self.poster_image is not None)
        reset.clicked.connect(self.reset_poster_framing)
        head.addWidget(reset)
        layout.addLayout(head)

        layout.addLayout(self._build_zoom_row(
            self.poster_zoom, self.poster_image is not None, self._on_zoom_changed))

        drag_hint = QHBoxLayout()
        drag_hint.setSpacing(6)
        hand = QLabel("\u270B")
        hand.setFont(Type.caption2())
        drag_hint.addWidget(hand)
        text = QLabel("在键盘预览区任意位置拖动即可平移")
        text.setFont(Type.caption2())
        text.setObjectName("secondary")
        drag_hint.addWidget(text, 1)
        layout.addLayout(drag_hint)

    def _on_style_button(self, button: QPushButton) -> None:
        value = button.property("value")
        if not value:
            return
        self.mask_to_circles = value == "circles"
        self._sync_creator_controls()
        self.request_slices()

    def _build_zoom_row(self, value: float, enabled: bool,
                        handler) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        minus = QLabel("\u2796")
        minus.setFont(Type.caption())
        minus.setObjectName("secondary")
        row.addWidget(minus)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(ZOOM_MIN * 100), int(ZOOM_MAX * 100))
        slider.setSingleStep(5)
        slider.setPageStep(10)
        slider.setValue(int(round(value * 100)))
        slider.setEnabled(enabled)
        row.addWidget(slider, 1)

        plus = QLabel("\u2795")
        plus.setFont(Type.caption())
        plus.setObjectName("secondary")
        row.addWidget(plus)

        readout = QLabel(f"{value:.1f}x")
        readout.setFont(Type.mono(11, 700))
        readout.setFixedWidth(32)
        readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(readout)

        slider.valueChanged.connect(
            lambda raw, _label=readout: _label.setText(f"{raw / 100.0:.1f}x"))
        slider.valueChanged.connect(lambda raw: handler(raw / 100.0))
        return row

    def _mount_individual_controls(self, layout: QVBoxLayout) -> None:
        head = QHBoxLayout()
        title = QLabel("逐键自定义")
        title.setFont(Type.caption(600))
        title.setObjectName("secondary")
        head.addWidget(title)
        head.addStretch(1)
        if self.selected_digit:
            deselect = QPushButton(f"取消选中按键 {self.selected_digit}")
            deselect.setProperty("link", "true")
            deselect.setCursor(Qt.CursorShape.PointingHandCursor)
            deselect.clicked.connect(lambda: self._select_key(None))
            head.addWidget(deselect)
        layout.addLayout(head)

        digit = self.selected_digit
        if digit and (digit in self.raw_individual or digit in self.custom_keys):
            card = QFrame()
            # 只保留底色，移除边框描边；并用作用域选择器避免样式泄漏到子控件。
            card.setObjectName("keyCard")
            card.setStyleSheet("QFrame#keyCard { background: #FFFFFF; "
                               "border-radius: 10px; border: none; }")
            column = QVBoxLayout(card)
            column.setContentsMargins(10, 10, 10, 10)
            column.setSpacing(8)

            row = QHBoxLayout()
            label = QLabel(f"按键 {digit} 构图")
            label.setFont(Type.headline_bold())
            label.setStyleSheet(f"color: {PURPLE};")
            row.addWidget(label)
            row.addStretch(1)
            reset = QPushButton("重置")
            reset.setProperty("link", "true")
            reset.setCursor(Qt.CursorShape.PointingHandCursor)
            reset.clicked.connect(lambda: self._reset_key(digit))
            row.addWidget(reset)
            column.addLayout(row)

            column.addLayout(self._build_zoom_row(
                self.individual_zooms.get(digit, 1.0), True,
                lambda value, _d=digit: self._on_key_zoom(_d, value)))

            drag = QHBoxLayout()
            drag.setSpacing(6)
            hand = QLabel("\u270B")
            hand.setFont(Type.caption2())
            drag.addWidget(hand)
            text = QLabel(f"在预览区拖动按键 {digit} 即可平移")
            text.setFont(Type.caption2())
            text.setObjectName("secondary")
            drag.addWidget(text, 1)
            column.addLayout(drag)

            actions = QHBoxLayout()
            actions.setSpacing(8)
            change = QPushButton("更换图片…")
            change.setCursor(Qt.CursorShape.PointingHandCursor)
            change.clicked.connect(lambda: self._pick_key_image(digit))
            actions.addWidget(change)
            remove = QPushButton("移除")
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.clicked.connect(lambda: self.clear_key(digit))
            actions.addWidget(remove)
            column.addLayout(actions)
            layout.addWidget(card)
            layout.addWidget(self._divider())

        guide = QLabel("点击键盘上的任意按键选中它，可平移图像、调整缩放，"
                       "或直接拖入图片。")
        guide.setFont(Type.caption2())
        guide.setObjectName("secondary")
        guide.setWordWrap(True)
        layout.addWidget(guide)

        count_row = QHBoxLayout()
        count_row.setSpacing(6)
        check = QLabel("\u2714")
        check.setStyleSheet(f"color: {PURPLE}; font-size: 11px;")
        count_row.addWidget(check)
        counter = QLabel(f"已配置 {len(self.custom_keys)}/10 个按键")
        counter.setFont(Type.caption(500))
        count_row.addWidget(counter, 1)
        layout.addLayout(count_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        if self.sliced_keys:
            fill = QPushButton("从海报填充")
            fill.setCursor(Qt.CursorShape.PointingHandCursor)
            fill.clicked.connect(self.adopt_poster_slices)
            actions.addWidget(fill)
        clear = QPushButton("清空所有按键")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setEnabled(bool(self.custom_keys))
        clear.clicked.connect(self.clear_all_keys)
        actions.addWidget(clear)
        layout.addLayout(actions)

    # ------------------------------------------------------------------
    # 左栏：刷入与语言目标
    # ------------------------------------------------------------------
    def _build_target_card(self) -> QWidget:
        card = QFrame()
        # 移除绿色实线边框（8 位 hex 被 Qt 误读的产物），只保留浅底色；
        # 作用域选择器防止样式波及下拉框与文字。
        card.setObjectName("targetCard")
        card.setStyleSheet("QFrame#targetCard { background: rgba(0,0,0,0.04); "
                           "border: none; border-radius: 10px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        icon = QLabel("\u2699")
        icon.setStyleSheet(f"color: {PURPLE}; font-size: 13px; font-weight: 700;")
        head.addWidget(icon)
        title = QLabel("刷入与语言目标")
        title.setFont(Type.caption(600))
        head.addWidget(title)
        head.addStretch(1)
        self.auto_detect_button = QPushButton("✨ 自动识别")
        self.auto_detect_button.setProperty("flat", True)
        self.auto_detect_button.setFont(Type.caption2(500))
        self.auto_detect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_detect_button.setToolTip("按已连接 iPhone 的语言与字体设置重置目标")
        self.auto_detect_button.clicked.connect(self.auto_detect_targets)
        head.addWidget(self.auto_detect_button)
        layout.addLayout(head)

        lang_label = QLabel("系统语言：")
        lang_label.setFont(Type.caption(500))
        lang_label.setObjectName("secondary")
        layout.addWidget(lang_label)
        self.language_combo = QComboBox()
        for code in language_codes():
            self.language_combo.addItem(language_label(code), code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(self.ctx.target_language)))
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self.language_combo)

        bold_label_widget = QLabel("字重 / 样式：")
        bold_label_widget.setFont(Type.caption(500))
        bold_label_widget.setObjectName("secondary")
        layout.addWidget(bold_label_widget)
        self.bold_combo = QComboBox()
        for code in bold_codes():
            self.bold_combo.addItem(bold_label(code), code)
        self.bold_combo.setCurrentIndex(
            max(0, self.bold_combo.findData(self.ctx.target_bold)))
        self.bold_combo.currentIndexChanged.connect(self._on_bold_changed)
        layout.addWidget(self.bold_combo)

        self.target_hint_host = QWidget()
        hint_layout = QHBoxLayout(self.target_hint_host)
        hint_layout.setContentsMargins(0, 2, 0, 0)
        hint_layout.setSpacing(6)
        self.target_hint_icon = QLabel()
        self.target_hint_icon.setFont(Type.caption2())
        hint_layout.addWidget(self.target_hint_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.target_hint_label = QLabel()
        self.target_hint_label.setFont(Type.caption2())
        self.target_hint_label.setWordWrap(True)
        hint_layout.addWidget(self.target_hint_label, 1)
        layout.addWidget(self.target_hint_host)
        return card

    def _sync_target_card(self) -> None:
        universal = self.ctx.target_language == "all" and self.ctx.target_bold == "both"
        if universal:
            self.target_hint_icon.setText("\U0001F310")
            self.target_hint_icon.setStyleSheet("color: #8E8E93;")
            self.target_hint_label.setStyleSheet("color: #8E8E93;")
            self.target_hint_label.setText(
                "通用模式会为所有语言与粗体写约 600 个文件。指定语言"
                "（如乌克兰语）可显著加快刷入速度。")
        else:
            self.target_hint_icon.setText("\u26a1")
            self.target_hint_icon.setStyleSheet(f"color: {ORANGE};")
            self.target_hint_label.setStyleSheet("color: #1C1C1E;")
            self.target_hint_label.setText(
                f"已选择快速模式：仅针对 {language_label(self.ctx.target_language)} "
                f"与 {bold_label(self.ctx.target_bold)}。")

    def auto_detect_targets(self) -> None:
        device = self.ctx.device
        if not device:
            self.ctx.error("未检测到 iPhone，无法自动识别。")
            return
        self.ctx.apply_device_preferences(device)

    def on_targets_changed(self) -> None:
        index = self.version_combo.findData(self.ctx.target_version)
        if index >= 0:
            self.version_combo.blockSignals(True)
            self.version_combo.setCurrentIndex(index)
            self.version_combo.blockSignals(False)
        index = self.language_combo.findData(self.ctx.target_language)
        if index >= 0:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(index)
            self.language_combo.blockSignals(False)
        index = self.bold_combo.findData(self.ctx.target_bold)
        if index >= 0:
            self.bold_combo.blockSignals(True)
            self.bold_combo.setCurrentIndex(index)
            self.bold_combo.blockSignals(False)
        self._sync_target_card()
        self.stateChanged.emit()

    def on_device_changed(self) -> None:
        self.auto_detect_button.setEnabled(self.ctx.connected())
        self.stateChanged.emit()

    def _on_version_changed(self, index: int) -> None:
        value = self.version_combo.itemData(index)
        if value:
            self.ctx.set_targets(version=value)

    def _on_language_changed(self, index: int) -> None:
        value = self.language_combo.itemData(index)
        if value:
            self.ctx.set_targets(language=value)

    def _on_bold_changed(self, index: int) -> None:
        value = self.bold_combo.itemData(index)
        if value:
            self.ctx.set_targets(bold=value)

    # ------------------------------------------------------------------
    # 主题文件解析
    # ------------------------------------------------------------------
    def choose_theme_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择密码主题包", "", ThemeFilter)
        if path:
            self.inspect_theme(path)

    def inspect_theme(self, path: str) -> None:
        self.is_inspecting = True
        self._sync_apply_controls()
        self.set_mode("apply")
        worker = ThemeInspectWorker(path)
        worker.signals.finished.connect(self._on_theme_inspected)
        submit(worker)

    def _on_theme_inspected(self, info: Any, error: str) -> None:
        self.is_inspecting = False
        if error or not info:
            self.ctx.error(f"无法解析该密码主题包：{error or '压缩包中没有可用素材'}")
            self._sync_apply_controls()
            return
        self.loaded_theme = info
        version = info.get("detectedVersion", "TelephonyUI-10")
        self.ctx.log(f"已载入密码主题：{info.get('name')} [{version}] "
                     f"共 {info.get('fileCount', 0)} 个图片素材")
        self.ctx.set_targets(version=version)
        self.ctx.status(f"已载入密码主题「{info.get('name')}」"
                        f"（{info.get('fileCount', 0)} 个素材）")
        self._sync_apply_controls()
        self.stateChanged.emit()

    def clear_theme(self) -> None:
        self.loaded_theme = None
        self._sync_apply_controls()
        self.stateChanged.emit()

    def edit_loaded_theme_in_creator(self) -> None:
        theme = self.loaded_theme
        if theme is None:
            return
        for digit, image in (theme.get("keysPreview") or {}).items():
            self.custom_keys[digit] = image
            self.raw_individual[digit] = image
            self.individual_offsets[digit] = (0.0, 0.0)
            self.individual_zooms[digit] = 1.0
        self.selected_digit = None
        self.sub_mode = "individual"
        self.set_mode("creator")
        self.ctx.status(f"「{theme.get('name')}」已载入制作器"
                        f"（{len(theme.get('keysPreview') or {})} 个按键可编辑）")
        self.ctx.log(f"已导入主题「{theme.get('name')}」到制作器进行二次编辑")

    # ------------------------------------------------------------------
    # 海报处理
    # ------------------------------------------------------------------
    def choose_poster(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择海报图片", "",
                                              SUPPORTED_IMAGE_FILTER)
        if path:
            self.set_poster_file(path)

    def set_poster_file(self, path: str) -> None:
        image = load_image(path)
        if image is None:
            self.ctx.error("无法读取该海报图片，请选择常见图片格式。")
            return
        self.poster_image = image.convert("RGBA")
        self.poster_path = path
        self.poster_pixmap = preview_pixmap(path, 915, 1148)
        self.poster_zoom = 1.0
        self.poster_offset = (0.0, 0.0)
        self.mode = "creator"
        self.sub_mode = "poster"
        self._sync_mode()
        self.request_slices()
        self.ctx.status("海报已载入 · 可以开始构图与切片")
        self.ctx.log(f"已载入海报：{path}")
        self.stateChanged.emit()

    def reset_poster_framing(self) -> None:
        self.poster_zoom = 1.0
        self.poster_offset = (0.0, 0.0)
        self._sync_creator_controls()
        self.request_slices()

    def _on_zoom_changed(self, value: float) -> None:
        self.poster_zoom = value
        self.request_slices()

    def _on_poster_dragged(self, delta: QPointF) -> None:
        x, y = self.poster_offset
        self.poster_offset = (x + delta.x(), y + delta.y())
        self.request_slices()

    def request_slices(self) -> None:
        if self.poster_image is None:
            self.sliced_keys = {}
            self._sync_phone_preview()
            self.stateChanged.emit()
            return
        snapshot = (self.poster_image, self.poster_zoom,
                    self.poster_offset, self.mask_to_circles)
        if self._render_busy:
            self._render_pending = ("poster", snapshot)
            return
        self._start_render("poster", snapshot)

    def _start_render(self, kind: str, payload: Any) -> None:
        self._render_busy = True
        self._render_seq += 1
        token = self._render_seq
        if kind == "poster":
            image, zoom, offset, circles = payload

            def task() -> Any:
                return slice_poster(image, zoom=zoom, offset=offset,
                                    mask_to_circles=circles)
        else:
            digit, raw, zoom, offset = payload

            def task() -> Any:
                return crop_to_circle(raw, zoom=zoom, offset=offset)

        worker = FunctionWorker(token, task)
        worker.signals.finished.connect(
            lambda _token, result, _kind=kind: self._on_rendered(_token, _kind, payload, result))
        worker.signals.failed.connect(self._on_render_failed)
        submit(worker)

    def _on_rendered(self, token: int, kind: str, payload: Any, result: Any) -> None:
        self._render_busy = False
        if token < self._render_seq:
            self._drain_pending()
            return
        if kind == "poster":
            self.sliced_keys = result or {}
        else:
            self._apply_key_result(payload[0], result)
        self._sync_phone_preview()
        self.stateChanged.emit()
        self._drain_pending()

    def _on_render_failed(self, token: int, message: str) -> None:
        self._render_busy = False
        self.ctx.error(f"图像处理失败：{message}")
        self._drain_pending()

    def _drain_pending(self) -> None:
        if self._render_pending is None:
            return
        kind, payload = self._render_pending
        self._render_pending = None
        self._start_render(kind, payload)

    def _apply_key_result(self, digit: str, image: Image.Image | None) -> None:
        if image is not None:
            self.custom_keys[digit] = image

    # ------------------------------------------------------------------
    # 逐键处理
    # ------------------------------------------------------------------
    def _has_key(self, digit: str) -> bool:
        return digit in self.custom_keys or digit in self.raw_individual

    def _select_key(self, digit: str | None) -> None:
        self.selected_digit = digit
        self._sync_creator_controls()
        self._sync_phone_preview()

    def _on_key_clicked(self, digit: str) -> None:
        if self.sub_mode != "individual":
            return
        if not self._has_key(digit):
            self._pick_key_image(digit)
            return
        self._select_key(None if self.selected_digit == digit else digit)

    def _pick_key_image(self, digit: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择按键 {digit} 的图标", "", SUPPORTED_IMAGE_FILTER)
        if path:
            self.set_key_image(digit, path)

    def set_key_image(self, digit: str, path: str) -> None:
        image = load_image(path)
        if image is None:
            self.ctx.error("无法读取该图片，请选择常见图片格式。")
            return
        self.raw_individual[digit] = image.convert("RGBA")
        self.individual_offsets[digit] = (0.0, 0.0)
        self.individual_zooms[digit] = 1.0
        self.selected_digit = digit
        self._sync_creator_controls()
        snapshot = (digit, self.raw_individual[digit], 1.0, (0.0, 0.0))
        if self._render_busy:
            self._render_pending = ("key", snapshot)
        else:
            self._start_render("key", snapshot)
        self.ctx.status(f"按键 {digit} 已更新 · 可在预览区拖动或调整缩放")
        self.stateChanged.emit()

    def _on_key_dragged(self, digit: str, delta: QPointF) -> None:
        if self.sub_mode != "individual" or not self._has_key(digit):
            return
        if self.selected_digit != digit:
            self._select_key(digit)
        x, y = self.individual_offsets.get(digit, (0.0, 0.0))
        offset = (x + delta.x(), y + delta.y())
        self.individual_offsets[digit] = offset
        raw = self.raw_individual.get(digit)
        if raw is None:
            return
        snapshot = (digit, raw, self.individual_zooms.get(digit, 1.0), offset)
        if self._render_busy:
            self._render_pending = ("key", snapshot)
        else:
            self._start_render("key", snapshot)

    def _on_key_zoom(self, digit: str, value: float) -> None:
        self.individual_zooms[digit] = value
        raw = self.raw_individual.get(digit)
        if raw is None:
            return
        snapshot = (digit, raw, value, self.individual_offsets.get(digit, (0.0, 0.0)))
        if self._render_busy:
            self._render_pending = ("key", snapshot)
        else:
            self._start_render("key", snapshot)

    def _reset_key(self, digit: str) -> None:
        self.individual_offsets[digit] = (0.0, 0.0)
        self.individual_zooms[digit] = 1.0
        raw = self.raw_individual.get(digit)
        self._sync_creator_controls()
        if raw is None:
            return
        snapshot = (digit, raw, 1.0, (0.0, 0.0))
        if self._render_busy:
            self._render_pending = ("key", snapshot)
        else:
            self._start_render("key", snapshot)

    def clear_key(self, digit: str) -> None:
        self.custom_keys.pop(digit, None)
        self.raw_individual.pop(digit, None)
        self.individual_offsets.pop(digit, None)
        self.individual_zooms.pop(digit, None)
        if self.selected_digit == digit:
            self.selected_digit = None
        self._sync_creator_controls()
        self._sync_phone_preview()
        self.ctx.status(f"已清除按键 {digit}")
        self.stateChanged.emit()

    def clear_all_keys(self) -> None:
        self.custom_keys.clear()
        self.raw_individual.clear()
        self.individual_offsets.clear()
        self.individual_zooms.clear()
        self.selected_digit = None
        self._sync_creator_controls()
        self._sync_phone_preview()
        self.ctx.status("已清空所有自定义按键")
        self.stateChanged.emit()

    def adopt_poster_slices(self) -> None:
        for digit, image in self.sliced_keys.items():
            self.custom_keys[digit] = image
            self.raw_individual[digit] = image
            self.individual_offsets[digit] = (0.0, 0.0)
            self.individual_zooms[digit] = 1.0
        self._sync_creator_controls()
        self._sync_phone_preview()
        self.ctx.status("已从海报切片填充逐键素材")
        self.stateChanged.emit()

    def clear_creator(self) -> None:
        self.poster_image = None
        self.poster_path = None
        self.poster_pixmap = None
        self.poster_zoom = 1.0
        self.poster_offset = (0.0, 0.0)
        self.sliced_keys.clear()
        self.clear_all_keys()
        self._sync_creator_controls()
        self._sync_phone_preview()
        self.ctx.status("主题制作器已重置")
        self.stateChanged.emit()

    def _on_key_context(self, digit: str, global_pos) -> None:
        menu = QMenu(self)
        change = menu.addAction(f"更换按键 {digit} 的图片…")
        if self._has_key(digit):
            reset = menu.addAction("重置位置与缩放")
            clear = menu.addAction(f"清除按键 {digit}")
        else:
            reset = clear = None
        chosen = menu.exec(global_pos)
        if chosen == change:
            self._pick_key_image(digit)
        elif reset is not None and chosen == reset:
            self._reset_key(digit)
        elif clear is not None and chosen == clear:
            self.clear_key(digit)

    # ------------------------------------------------------------------
    # 拖放
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self._on_phone_drop(path, "")

    def _on_phone_drop(self, path: str, digit: str) -> None:
        if self.mode != "creator":
            self.inspect_theme(path)
            return
        if self.sub_mode == "individual" and digit:
            if zipfile.is_zipfile(path) or path.lower().endswith(
                    (".passthm", ".passtheme", ".zip")):
                self.ctx.error("逐键模式下请拖入图片文件。")
                return
            self.set_key_image(digit, path)
            return
        if zipfile.is_zipfile(path) or path.lower().endswith(
                (".passthm", ".passtheme", ".zip")):
            self.set_mode("apply")
            self.inspect_theme(path)
            return
        self.set_poster_file(path)

    # ------------------------------------------------------------------
    # 预览同步
    # ------------------------------------------------------------------
    def _sync_phone_preview(self) -> None:
        theme = self.loaded_theme if self.mode == "apply" else None
        if theme is not None:
            pixmaps = {digit: pil_to_pixmap(image)
                       for digit, image in (theme.get("keysPreview") or {}).items()}
        else:
            pixmaps = {}
        self.apply_phone.mode = "apply"
        self.apply_phone.theme_pixmaps = pixmaps
        self.apply_phone.update()

        phone = self.creator_phone
        phone.mode = "creator"
        phone.creator_sub_mode = "poster" if self.sub_mode == "poster" else "individual"
        phone.mask_to_circles = self.mask_to_circles
        phone.selected_digit = self.selected_digit
        if self.sub_mode == "poster":
            source = self.sliced_keys
        else:
            source = self.custom_keys
        phone.key_pixmaps = {digit: pil_to_pixmap(image)
                             for digit, image in source.items()}
        phone.poster_zoom = self.poster_zoom
        phone.poster_offset = QPointF(*self.poster_offset)
        phone.poster = self.poster_pixmap
        phone.update()

        self.apply_preview_hint.setText(
            "自定义主题已载入" if theme is not None else "")
        if self.mode == "creator" and self.sub_mode == "poster" \
                and self.poster_image is not None:
            self.creator_preview_hint.setText("拖动键盘平移 · 使用滑块缩放")
        else:
            self.creator_preview_hint.setText("")

    # ------------------------------------------------------------------
    # 导出 / 刷入
    # ------------------------------------------------------------------
    def effective_keys(self) -> dict[str, Image.Image]:
        return self.sliced_keys if self.sub_mode == "poster" else self.custom_keys

    def summary(self) -> str:
        target = self.ctx.target_summary()
        if self.mode == "creator":
            count = len(self.effective_keys())
            if count:
                return f"主题制作器 · 已配置 {count}/10 个按键 · 目标：{target}"
            return "主题制作器 · 请导入海报或将图标拖到按键上"
        theme = self.loaded_theme
        if theme is not None:
            return f"{theme.get('fileCount', 0)} 个源资源已载入 · 目标：{target}"
        return "未载入 .passthm · 请先选择主题包"

    def flash_button_label(self) -> str:
        if self.ctx.busy:
            return "正在刷入密码…"
        if self.mode == "creator":
            return "刷入 iPhone"
        return "刷入密码主题"

    def flash_enabled(self) -> bool:
        if self.ctx.busy or not self.ctx.connected():
            return False
        if self.mode == "creator":
            return bool(self.effective_keys())
        return self.loaded_theme is not None

    def export_created_theme(self) -> None:
        keys = self.effective_keys()
        if not keys:
            self.ctx.error("请先至少配置一个按键再导出。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出密码主题", "CustomTheme.passthm", ThemeFilter)
        if not path:
            return
        if not path.lower().endswith(".passthm"):
            path += ".passthm"
        snapshot = dict(keys)

        def task() -> str:
            export_theme(snapshot, path,
                         self.ctx.target_language, self.ctx.target_bold)
            return path

        worker = FunctionWorker("export", task)
        worker.signals.finished.connect(
            lambda _token, value: self._on_exported(value))
        worker.signals.failed.connect(
            lambda _token, message: self.ctx.error(f"导出主题失败：{message}"))
        submit(worker)

    def _on_exported(self, path: str) -> None:
        import os
        import subprocess

        self.ctx.status(f"主题已成功导出到 {os.path.basename(path)}")
        self.ctx.log(f"已导出 .passthm：{path}")
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception:
            pass

    def start_flash(self) -> None:
        udid = self.ctx.udid()
        if not udid:
            self.ctx.error("请先用 USB 连接 iPhone 并信任此电脑。")
            return
        if self.mode == "creator":
            keys = dict(self.effective_keys())
            if not keys:
                self.ctx.error("请先添加至少一个按键图标或导入一张海报。")
                return

            def stage() -> str:
                return stage_temporary_theme(
                    keys, self.ctx.target_language, self.ctx.target_bold)

            worker = FunctionWorker("stage", stage)
            worker.signals.finished.connect(
                lambda _token, value: self._flash_path(str(value), len(keys), "自制主题"))
            worker.signals.failed.connect(
                lambda _token, message: self.ctx.error(f"打包主题失败：{message}"))
            submit(worker)
            return

        theme = self.loaded_theme
        if theme is None:
            self.ctx.error("请先选择一个 .passthm 主题包。")
            return
        self._flash_path(str(theme.get("filePath")),
                         int(theme.get("fileCount") or 0),
                         str(theme.get("name")))

    def _flash_path(self, path: str, file_count: int, name: str) -> None:
        udid = self.ctx.udid()
        if not udid:
            self.ctx.error("请先用 USB 连接 iPhone 并信任此电脑。")
            return
        self.ctx.set_busy(True)
        self.ctx.set_op("flash")
        self.ctx.progress(0.0)
        self.ctx.status("正在刷入密码主题…")
        self.ctx.log(f"正在将密码主题「{name}」刷入设备…")
        worker = PasscodeFlashWorker(udid, path, self.ctx.target_version,
                                     self.ctx.target_language, self.ctx.target_bold)
        worker.signals.log.connect(self.ctx.log)
        worker.signals.status.connect(self.ctx.status)
        worker.signals.progress.connect(self.ctx.progress)
        worker.signals.finished.connect(
            lambda ok, message: self._on_flash_finished(ok, message, name))
        submit(worker)
        self.stateChanged.emit()

    def _on_flash_finished(self, ok: bool, message: str, name: str) -> None:
        self.ctx.set_busy(False)
        self.ctx.set_op("")
        self.ctx.progress(1.0 if ok else 0.0)
        if ok:
            self.ctx.status("密码主题已成功应用！")
            self.ctx.success("密码主题已成功应用！\n\n"
                             "请锁定 iPhone（或重启设备）以查看新的密码键盘。")
            self.ctx.log(f"密码主题「{name}」刷入成功！")
        else:
            self.ctx.status(f"刷入失败：{message}")
            self.ctx.error(f"{message}\n\n请查看日志后重试。")
        self.stateChanged.emit()
