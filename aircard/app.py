"""AirCard 桌面入口。

用法：开发态运行 `python -m aircard.app`；打包后运行 `AirCard.exe`。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

from . import VERSION


def _resource_path(name: str) -> Path:
    """打包后资源在 `sys._MEIPASS` 根目录，开发态在本地 assets 目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / name
        if candidate.is_file():
            return candidate
    return _PACKAGE_ROOT / "assets" / name


def _prime_apple_runtime() -> None:
    """在 GUI 初始化前把 Apple 支持目录加入 DLL 搜索路径。"""
    if os.name != "nt":
        return
    try:
        from .native.cf import AppleRuntime
        AppleRuntime.shared().support_dir
    except Exception:
        # 设备不可用不应阻止界面启动，运行时会在状态栏提示。
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="AirCard", description="AirCard for Windows（中文版）")
    parser.add_argument("--version", action="store_true", help="输出版本号后退出")
    parser.add_argument("--no-hidpi", action="store_true", help="关闭高 DPI 缩放")
    parser.add_argument("--no-agreement", action="store_true",
                        help="跳过启动协议告知页（仅用于自动化测试）")
    args, _unknown = parser.parse_known_args(argv)

    if args.version:
        print(f"AirCard for Windows v{VERSION}")
        return 0

    if not args.no_hidpi:
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
        os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

    _prime_apple_runtime()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QApplication, QDialog

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    from .ui.main_window import MainWindow
    from .ui.agreement import AgreementDialog
    from .ui.style import APP_QSS, light_palette

    QApplication.setStyle("Fusion")
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("AirCard")
    app.setApplicationDisplayName("AirCard")
    app.setPalette(light_palette(app))
    app.setStyleSheet(APP_QSS)

    for name in ("aircard.ico", "aircard.png"):
        path = _resource_path(name)
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                app.setWindowIcon(QIcon(pixmap))
                break

    # 启动协议与风险告知页：必须用户「同意」才进入主界面；「拒绝」直接退出。
    # 自动化测试用 --no-agreement 或环境变量 AIRCARD_NO_AGREEMENT=1 跳过。
    if not args.no_agreement and os.environ.get("AIRCARD_NO_AGREEMENT") != "1":
        agreement = AgreementDialog()
        if agreement.exec() != QDialog.DialogCode.Accepted:
            return 0

    window = MainWindow()
    window.show()
    return app.exec()
