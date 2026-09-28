"""真入口启动冒烟：直接调用 aircard.app.main()，验证完整启动链路。

这条测试专门用来捕捉只在真实入口才会暴露的问题（错误的相对导入、
资源路径、样式表加载等）。

运行：  python tests/launch_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aircard.core import storage  # noqa: E402

TITLES: list[str] = []
ERRORS: list[str] = []

def _finish() -> None:
    try:
        for widget in QApplication.topLevelWidgets():
            if widget.isWindow() and widget.isVisible():
                TITLES.append(widget.windowTitle())
    finally:
        for widget in list(QApplication.topLevelWidgets()):
            widget.close()
        QApplication.quit()


def main() -> int:
    store = storage.CARDS_STORE_PATH
    backup = None
    if store.is_file():
        backup = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        backup.close()
        shutil.copy2(store, backup.name)
        store.unlink()

    try:
        from aircard.app import main as entry

        code = entry(["--version"])
        if code != 0:
            ERRORS.append(f"--version 退出码={code}")
        print(f"[{'OK  ' if code == 0 else 'FAIL'}] --version 退出码  {code}")

        # 预先创建 QApplication：main() 会复用该实例，
        # 这样可以在事件循环启动前挂一个自动退出定时器。
        app = QApplication(sys.argv)
        QTimer.singleShot(1500, _finish)
        code = entry(["--no-agreement"])
        del app
        ok = code == 0
        if not ok:
            ERRORS.append(f"GUI 退出码={code}")
        print(f"[{'OK  ' if ok else 'FAIL'}] GUI 主循环退出码  {code}")

        ok = any("AirCard" in t for t in TITLES)
        if not ok:
            ERRORS.append(f"未找到主窗口标题: {TITLES}")
        print(f"[{'OK  ' if ok else 'FAIL'}] 顶层窗口标题  {TITLES}")
    except Exception as exc:  # noqa: BLE001
        ERRORS.append(f"启动异常: {exc!r}")
        print(f"[FAIL] 启动异常  {exc!r}")
    finally:
        if backup is not None:
            shutil.copy2(backup.name, store)
            os.unlink(backup.name)
        elif store.is_file():
            store.unlink()

    print("\nerrors:", ERRORS)
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
