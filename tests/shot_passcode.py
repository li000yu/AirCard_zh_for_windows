"""临时用：截取「密码主题」面板在两种模式下的真实渲染图，用于核对排版。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aircard.ui.context import AppContext  # noqa: E402
from aircard.ui.passcode_panel import PasscodePanel  # noqa: E402
from aircard.ui.style import APP_QSS, light_palette  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setPalette(light_palette(app))
app.setStyleSheet(APP_QSS)

ctx = AppContext()
panel = PasscodePanel(ctx)
panel.resize(1180, 760)
panel.show()
app.processEvents()


def _shot(name: str) -> None:
    panel.adjustSize()
    app.processEvents()
    pixmap = panel.grab()
    path = os.path.join(OUT, name)
    pixmap.save(path)
    print("saved", path, pixmap.width(), pixmap.height())


def finish() -> None:
    panel.set_mode("creator")
    app.processEvents()


def late() -> None:
    _shot("passcode_creator.png")
    app.quit()


QTimer.singleShot(600, lambda: _shot("passcode_apply.png"))
QTimer.singleShot(1600, finish)
QTimer.singleShot(3200, late)
QTimer.singleShot(5000, app.quit)
app.exec()
