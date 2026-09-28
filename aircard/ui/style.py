"""统一的中文界面视觉规范。

原版 macOS 应用使用 SwiftUI 浅色主题：强调蓝、密码主题紫、成功绿。
这里用 Fusion 风格 + 自定义调色板，在 Windows 上复刻同一套观感。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

APP_FONT_FAMILY = "Microsoft YaHei UI"
MONO_FONT_FAMILY = "Cascadia Mono"

# 语义色
WINDOW_BG = "#F2F2F7"
CONTROL_BG = "#FFFFFF"
CONTROL_BG_SOFT = "#F7F7FA"
SEPARATOR = "#D9D9DE"

TEXT_PRIMARY = "#1C1C1E"
TEXT_SECONDARY = "#6E6E73"
TEXT_DISABLED = "#A0A0A6"

ACCENT = "#0A84FF"
ACCENT_SOFT = "#E7F1FF"
PURPLE = "#A855F7"
PURPLE_SOFT = "#F3E8FF"
GREEN = "#34C759"
GREEN_SOFT = "#E4F8EA"
RED = "#FF3B30"
ORANGE = "#FF9500"
BLUE_INFO = "#0A84FF"
INFO_SOFT = "#E7F1FF"

BORDER = "#E0E0E5"
RADIUS = 12


def rgba(hex_color: str, alpha: int) -> str:
    """把 #RRGGBB + 0~255 透明度转成 Qt QSS 认识的 rgba() 写法。

    ⚠️ 不要在 f-string 里把 alpha 直接拼在颜色常量后面（例如 {PURPLE}
    后面直接写两位 16 进制透明度）：Qt QSS 把 8 位 hex 解析为 #AARRGGBB
    （alpha 在最前），紫色 #A855F7 加 4D 会被读成 alpha=A8、RGB=55F74D
    —— 界面上直接显示成"绿色"，这正是此前绿框 bug 的根因。
    """
    value = hex_color.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({red}, {green}, {blue}, {alpha})"


def _who(name: str, size: int, weight: int = 400, mono: bool = False) -> QFont:
    font = QFont(name, size)
    font.setWeight(QFont.Weight(weight))
    if mono:
        font.setStyleHint(QFont.StyleHint.Monospace)
    return font


class Type:
    """与 SwiftUI 字阶一一对应的字体工厂。"""

    @staticmethod
    def title2_bold() -> QFont:
        return _who(APP_FONT_FAMILY, 17, 700)

    @staticmethod
    def title3_bold() -> QFont:
        return _who(APP_FONT_FAMILY, 15, 700)

    @staticmethod
    def headline_bold() -> QFont:
        return _who(APP_FONT_FAMILY, 13, 700)

    @staticmethod
    def headline() -> QFont:
        return _who(APP_FONT_FAMILY, 13, 400)

    @staticmethod
    def subheadline(weight: int = 400) -> QFont:
        return _who(APP_FONT_FAMILY, 12, weight)

    @staticmethod
    def body(weight: int = 400) -> QFont:
        return _who(APP_FONT_FAMILY, 13, weight)

    @staticmethod
    def caption(weight: int = 400) -> QFont:
        return _who(APP_FONT_FAMILY, 10, weight)

    @staticmethod
    def caption2(weight: int = 400) -> QFont:
        return _who(APP_FONT_FAMILY, 9, weight)

    @staticmethod
    def mono(size: int = 10, weight: int = 400) -> QFont:
        return _who(MONO_FONT_FAMILY, size, weight)


APP_QSS = f"""
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: "{APP_FONT_FAMILY}";
    font-size: 12px;
}}

QWidget#root, QMainWindow, QMainWindow > QWidget {{
    background: {WINDOW_BG};
}}

QToolTip {{
    background: #2C2C2E;
    color: #FFFFFF;
    border: none;
    padding: 4px 8px;
    font-size: 11px;
}}

/* ---------- 通用容器 ---------- */
QFrame#panel {{
    background: rgba(0, 0, 0, 0.03);
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#panelPurple {{
    background: {CONTROL_BG_SOFT};
    border: 1px solid {rgba(PURPLE, 77)};
    border-radius: 10px;
}}
QFrame#separator, QFrame[shape="4"] {{
    background: {SEPARATOR};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}
QFrame#cardTile {{
    background: rgba(120, 120, 128, 0.07);
    border: 1px solid transparent;
    border-radius: 18px;
}}
QFrame#cardTile[selected="true"] {{
    border: 1px solid {rgba(ACCENT, 77)};
    background: rgba(10, 132, 255, 0.06);
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {CONTROL_BG};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 4px 12px;
    font-size: 12px;
    color: {TEXT_PRIMARY};
}}
QPushButton:hover {{
    background: {CONTROL_BG_SOFT};
    border-color: #C8C8CE;
}}
QPushButton:pressed {{
    background: #E9E9EE;
}}
QPushButton:disabled {{
    color: {TEXT_DISABLED};
    background: #F2F2F4;
    border-color: #E6E6EA;
}}
QPushButton[cta="true"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[cta="true"]:hover {{
    background: #3395FF;
}}
QPushButton[cta="true"]:disabled {{
    background: #A9D3FF;
    border-color: #A9D3FF;
    color: #48484A;
}}
QPushButton[cta="danger"] {{
    background: {RED};
    border: 1px solid {RED};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[cta="danger"]:hover {{
    background: #FF5A50;
}}
QPushButton[cta="green"] {{
    background: {GREEN};
    border: 1px solid {GREEN};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[cta="green"]:hover {{
    background: #4FD873;
}}
QPushButton[cta="green"]:disabled {{
    background: #B3EBBF;
    border-color: #B3EBBF;
    color: #3A6B45;
}}
QPushButton[cta="purple"] {{
    background: {PURPLE};
    border: 1px solid {PURPLE};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[cta="purple"]:hover {{
    background: #B96BFA;
}}
QPushButton[cta="purple"]:disabled {{
    background: #DCC4FB;
    border-color: #DCC4FB;
    color: #4A3568;
}}
QPushButton[flat="true"] {{
    background: transparent;
    border: none;
    padding: 2px 6px;
}}
QPushButton[flat="true"]:hover {{
    background: rgba(0, 0, 0, 0.05);
    border: none;
}}
QPushButton[link="true"] {{
    background: transparent;
    border: none;
    color: {ACCENT};
    font-size: 10px;
    padding: 2px 4px;
}}
QPushButton[link="true"]:hover {{
    text-decoration: underline;
}}
QPushButton[link="danger"] {{
    color: {RED};
}}
QPushButton[link="danger"]:disabled {{
    color: #F0B5B2;
}}
QPushButton[link="danger"]:hover {{
    text-decoration: underline;
}}

/* 分段控件：由 toggled 状态驱动 */
QPushButton[segment="true"] {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 4px 18px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton[segment="true"]:checked {{
    background: {CONTROL_BG};
    border: 1px solid #DADAE0;
}}
QFrame#segmentHost {{
    background: #E9E9EE;
    border-radius: 9px;
}}

/* ---------- 输入控件 ---------- */
QComboBox {{
    background: {CONTROL_BG};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 4px 24px 4px 10px;
    min-height: 20px;
    font-size: 12px;
}}
QComboBox:hover {{
    border-color: #C8C8CE;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 20px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {CONTROL_BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT_PRIMARY};
    padding: 4px;
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: #D9D9DE;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: {CONTROL_BG};
    border: 1px solid #C0C0C6;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}

QProgressBar {{
    background: #E3E3E8;
    border: none;
    border-radius: 3px;
    height: 6px;
    min-height: 6px;
    max-height: 6px;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 3px;
}}

QCheckBox {{
    spacing: 4px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #B8B8C0;
    background: {CONTROL_BG};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox::indicator:indeterminate {{
    background: #C7C7CC;
    border-color: #C7C7CC;
}}

QTextEdit, QPlainTextEdit {{
    background: {CONTROL_BG};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    font-family: "{MONO_FONT_FAMILY}";
    font-size: 12px;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #C1C1C8;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #A8A8B0;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: #C1C1C8;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}

QLabel#monoPill {{
    background: rgba(120, 120, 128, 0.12);
    border-radius: 6px;
    padding: 3px 6px;
    font-family: "{MONO_FONT_FAMILY}";
    font-size: 10px;
    color: {TEXT_SECONDARY};
}}
QLabel#versionChip {{
    background: {ACCENT_SOFT};
    color: {ACCENT};
    border-radius: 7px;
    padding: 2px 6px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#themeChip {{
    background: {PURPLE_SOFT};
    color: {PURPLE};
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 9px;
    font-weight: 700;
}}
QLabel#secondary {{
    color: {TEXT_SECONDARY};
}}
QLabel#console {{
    background: {CONTROL_BG};
    font-family: "{MONO_FONT_FAMILY}";
    font-size: 10px;
    color: {TEXT_SECONDARY};
}}
"""


def light_palette(app: QApplication) -> QPalette:
    """固定的浅色调色板，保证在深色系统主题下观感一致。"""
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(CONTROL_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(CONTROL_BG_SOFT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(CONTROL_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2C2C2E"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#FFFFFF"))
    return palette
