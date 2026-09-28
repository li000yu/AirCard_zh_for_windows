"""「关于 / 鸣谢」与「手动添加卡片哈希」对话框。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
                               QTextEdit, QVBoxLayout)

from .. import VERSION
from .qt_images import preview_pixmap
from .style import ACCENT_SOFT, BLUE_INFO, CONTROL_BG, ORANGE, PURPLE, Type


def _link(text: str, url: str) -> QLabel:
    label = QLabel(f'<a href="{url}" style="color: {BLUE_INFO}; '
                   f'text-decoration: none;">{text}</a>')
    label.setOpenExternalLinks(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


class CreditsDialog(QDialog):
    """原版 SwiftUI `.sheet(isPresented: $showCredits)`。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于 AirCard")
        self.setModal(True)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        icon = QLabel("\U0001F4B3")
        icon.setStyleSheet(f"font-size: 44px; color: {BLUE_INFO};")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        title = QLabel("AirCard")
        title.setFont(Type.title2_bold())
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("iOS 18+ 苹果钱包卡面皮肤与密码键盘主题工具")
        subtitle.setFont(Type.caption())
        subtitle.setObjectName("secondary")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._divider(layout)

        info = QVBoxLayout()
        info.setSpacing(10)
        for icon_text, icon_color, label_text, widgets in (
                ("\U0001F464", BLUE_INFO, "开发者：",
                 [_link("@mak5er", "https://github.com/mak5er"),
                  QLabel("·"), _link("X / Twitter", "https://x.com/mak5er")]),
                ("\U0001F464", BLUE_INFO, "开发者：",
                 [_link("@Lumid-Off", "https://github.com/Lumid-Off"),
                  QLabel("·"), _link("X / Twitter", "https://x.com/LumidOff")]),
                ("\u26a1", ORANGE, "核心漏洞：",
                 [QLabel("airlift（AirTraffic 同步逃逸）")]),
                ("\U0001F510", PURPLE, "密码主题：",
                 [QLabel(".passthm 标准（Cowabunga / Nugget）")]),
        ):
            row = QHBoxLayout()
            row.setSpacing(8)
            glyph = QLabel(icon_text)
            glyph.setStyleSheet(f"color: {icon_color}; font-size: 14px;")
            row.addWidget(glyph)
            caption = QLabel(label_text)
            caption.setFont(Type.subheadline(500))
            row.addWidget(caption)
            for widget in widgets:
                if isinstance(widget, QLabel) and widget.text() == "·":
                    widget.setObjectName("secondary")
                elif isinstance(widget, QLabel):
                    widget.setObjectName("secondary")
                widget.setFont(Type.subheadline())
                row.addWidget(widget)
            row.addStretch(1)
            info.addLayout(row)
        layout.addLayout(info)

        self._divider(layout)

        version = QLabel(f"Windows 单文件绿色版 v{VERSION}")
        version.setFont(Type.caption())
        version.setObjectName("secondary")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        button = QPushButton("关闭")
        button.setProperty("cta", "true")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.accept)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)

    @staticmethod
    def _divider(layout: QVBoxLayout) -> None:
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #DEDEE3;")
        layout.addWidget(line)


class AddCardDialog(QDialog):
    """原版 SwiftUI `.sheet(isPresented: $vm.showAddCardSheet)`。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("手动添加卡片哈希")
        self.setModal(True)
        self.setFixedWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        title = QLabel("手动添加卡片哈希")
        title.setFont(Type.headline_bold())
        layout.addWidget(title)

        hint = QLabel("粘贴一个或多个卡片哈希（可用空格、逗号或换行分隔）：")
        hint.setFont(Type.caption())
        hint.setObjectName("secondary")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.editor = QTextEdit(self)
        self.editor.setFixedHeight(120)
        self.editor.setPlaceholderText("例如：Y6nDwZrkYbFlsodLgCbvyFZQ1cc=")
        layout.addWidget(self.editor)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm_button = QPushButton("添加到列表")
        self.confirm_button.setProperty("cta", "true")
        self.confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.accept)
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)

        self.editor.textChanged.connect(self._refresh)
        self.editor.setFocus()

    def _refresh(self) -> None:
        self.confirm_button.setEnabled(bool(self.editor.toPlainText().strip()))

    def value(self) -> str:
        return self.editor.toPlainText()


class DiagnosticsDialog(QDialog):
    """连接诊断详情。

    用可滚动、可复制的文本框而不是 QMessageBox：诊断内容可能很长（UDID 候选、
    逐阶段消息、Apple 组件日志），用户需要整段复制回来给我们分析。
    """

    def __init__(self, title: str, body: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)          # 非模态：自动化测试里 exec() 会挂死
        self.resize(720, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        head = QLabel("连接诊断结果")
        head.setFont(Type.title2_bold())
        layout.addWidget(head)

        tip = QLabel("可全选复制后发给开发者，便于定位问题。")
        tip.setObjectName("secondary")
        tip.setFont(Type.caption())
        layout.addWidget(tip)

        self.viewer = QTextEdit(self)
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.viewer.setPlainText(body)
        layout.addWidget(self.viewer, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        copy = QPushButton("复制全部")
        copy.setCursor(Qt.CursorShape.PointingHandCursor)
        copy.clicked.connect(self._copy)
        actions.addWidget(copy)
        close = QPushButton("关闭")
        close.setProperty("cta", "true")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.viewer.toPlainText())
