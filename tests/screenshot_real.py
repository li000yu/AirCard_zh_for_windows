"""真实 windows 平台截图（用于核对中文字体渲染）。

运行：  python tests/screenshot_real.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aircard.core import storage  # noqa: E402


def main() -> int:
    store = storage.CARDS_STORE_PATH
    backup = None
    if store.is_file():
        backup = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        backup.close()
        shutil.copy2(store, backup.name)
        store.unlink()

    try:
        from aircard.ui.main_window import MainWindow
        from aircard.ui.style import APP_QSS, light_palette

        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setPalette(light_palette(app))
        app.setStyleSheet(APP_QSS)

        win = MainWindow()
        win.resize(1440, 900)
        win.show()

        wallet = win.wallet
        wallet.add_hashes(
            "Y6nDwZrkYbFlsodLgCbvyFZQ1cc=, kJL-D0rrT1mSgNqPxWuVhZbEfAo=, "
            "hwAtAmHKcQpLdNfTxRzYbVmSeAo="
        )
        wallet._set_all_selected(True)

        shots = os.path.join(ROOT, "tests", "_shots")
        os.makedirs(shots, exist_ok=True)

        state = {"tab": "wallet"}

        def snap() -> None:
            win.grab().save(os.path.join(shots, f"real_{state['tab']}.png"))
            if state["tab"] == "wallet":
                state["tab"] = "passcode"
                win.set_tab("passcode")
                QTimer.singleShot(700, snap)
            else:
                win.close()
                app.quit()

        QTimer.singleShot(900, snap)
        return app.exec()
    finally:
        if backup is not None:
            shutil.copy2(backup.name, store)
            os.unlink(backup.name)
        elif store.is_file():
            store.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
