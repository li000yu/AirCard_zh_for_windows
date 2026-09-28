"""离屏 UI 冒烟测试：验证窗口、分页、卡片网格几何与核心交互。

运行：  python tests/smoke_ui.py
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

from PySide6.QtWidgets import QApplication  # noqa: E402

from aircard.core import storage  # noqa: E402
from aircard.ui.main_window import MainWindow  # noqa: E402

ERRORS: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    status = "OK  " if cond else "FAIL"
    if not cond:
        ERRORS.append(f"{label} -> {detail}")
    print(f"[{status}] {label}  {detail}")


def main() -> int:
    # 隔离本地卡片存储，避免污染用户 home 目录
    store = storage.CARDS_STORE_PATH
    backup = None
    if store.is_file():
        backup = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        backup.close()
        shutil.copy2(store, backup.name)
        store.unlink()

    app = QApplication(sys.argv)
    try:
        win = MainWindow()
        win.resize(1440, 900)
        win.show()
        app.processEvents()

        # ---------------- 钱包分页 ----------------
        win.set_tab("wallet")
        app.processEvents()
        wallet = win.wallet
        check("钱包面板已挂载", wallet is not None)
        check("初始卡片为空", len(wallet.cards) == 0, str(len(wallet.cards)))

        added = wallet.add_hashes(
            "Y6nDwZrkYbFlsodLgCbvyFZQ1cc=, kJL-D0rrT1mSgNqPxWuVhZbEfAo=, "
            "hwAtAmHKcQpLdNfTxRzYbVmSeAo="
        )
        app.processEvents()
        app.processEvents()
        check("新增卡片 3 张", added == 3 and len(wallet.cards) == 3,
              f"added={added} total={len(wallet.cards)}")

        area = wallet.area
        canvas = wallet.canvas
        g = canvas.geometry()
        check("网格画布高度 > 100", g.height() > 100, f"h={g.height()}")
        check("网格画布宽度 > 500", g.width() > 500, f"w={g.width()}")
        check("滚动区可见", area.isVisible(), str(area.isVisible()))
        check("滚动区视口高度 > 100", area.viewport().height() > 100,
              str(area.viewport().height()))
        check("空状态已隐藏", not wallet.empty_host.isVisible())

        tiles = list(wallet._tiles.values())
        check("瓦片数量 = 3", len(tiles) == 3, str(len(tiles)))
        check("瓦片提供备份卡面按钮",
              all(hasattr(t, "backup_button") and hasattr(t, "backupRequested")
                  for t in tiles))
        check("瓦片提供读取卡面按钮",
              all(hasattr(t, "read_button") and hasattr(t, "readRequested")
                  for t in tiles))
        check("瓦片提供从历史恢复按钮",
              all(hasattr(t, "restore_history_button")
                  and hasattr(t, "restoreHistoryRequested")
                  for t in tiles))
        check("辨认按钮已彻底移除",
              all(not hasattr(t, "mark_button") and not hasattr(t, "markRequested")
                  for t in tiles))
        for idx, tile in enumerate(tiles):
            tg = tile.geometry()
            ok = tg.width() > 250 and tg.height() > 150
            check(f"瓦片{idx} 几何", ok,
                  f"{tg.width()}x{tg.height()} @({tg.x()},{tg.y()})")

        wallet._set_all_selected(True)
        app.processEvents()
        check("全选生效", wallet.ready_count() == 0, wallet.selection_summary())
        check("已选计数 = 3", wallet.selected_count() == 3,
              str(wallet.selected_count()))
        wallet._set_all_selected(False)
        app.processEvents()
        check("取消全选生效", wallet.selected_count() == 0,
              str(wallet.selected_count()))

        check("刷新按钮标签", bool(wallet.flash_button_label()),
              wallet.flash_button_label())
        check("未选图时禁止刷入", not wallet.flash_enabled())

        check("工具栏含在线制作卡面按钮", hasattr(wallet, "online_button"))
        check("在线制作卡面按钮可点击",
              wallet.online_button.isEnabled())

        # ---------------- 密码分页 ----------------
        win.set_tab("passcode")
        app.processEvents()
        pc = win.passcode
        check("密码面板已挂载", pc is not None)

        pc.set_mode("apply")
        app.processEvents()
        check("模式=应用主题", pc.mode == "apply", pc.mode)
        pc.set_mode("creator")
        app.processEvents()
        check("模式=主题制作器", pc.mode == "creator", pc.mode)
        pc.set_sub_mode("poster")
        app.processEvents()
        check("子模式=海报切片", pc.sub_mode == "poster", pc.sub_mode)
        pc.set_sub_mode("individual")
        app.processEvents()
        check("子模式=逐键自定义", pc.sub_mode == "individual", pc.sub_mode)

        check("刷入按钮标签", bool(pc.flash_button_label()),
              pc.flash_button_label())
        check("状态摘要", bool(pc.summary()), pc.summary())
        check("无主题时禁止刷入", not pc.flash_enabled())

        targets = pc.ctx.target_summary()
        check("目标摘要可用", isinstance(targets, str), targets)

        # ---- 界面修复回归（绿框 / 文字重叠）----
        import re as _re

        from aircard.ui.passcode_panel import DropCard

        hex8 = _re.compile(r"#[0-9A-Fa-f]{8}")
        drop = DropCard()
        check("拖拽区样式限定在 #dropCard（不再套住子控件文字）",
              "QFrame#dropCard" in drop.styleSheet())
        check("拖拽区边框用 rgba 且无 8 位 hex（8 位会被 Qt 误读成绿色）",
              "rgba(" in drop.styleSheet() and not hex8.search(drop.styleSheet()))
        target_card = pc._build_target_card()
        check("「刷入与语言目标」卡片无边框且作用域受限",
              "QFrame#targetCard" in target_card.styleSheet()
              and "border: none" in target_card.styleSheet())
        target_card.deleteLater()
        pc.set_sub_mode("individual")
        pc._select_key("5")
        app.processEvents()
        for child in pc.findChildren(type(pc.creator_page)):
            pass  # 仅触发一次完整布局
        check("模式来回切换后无崩溃", pc.sub_mode == "individual", pc.sub_mode)

        # ---------------- 底部栏 / 日志 / 对话框 ----------------
        win.refresh_bottom()
        app.processEvents()
        check("底部状态非空", bool(win.status_label.text()),
              win.status_label.text())

        win.set_console_visible(True)
        app.processEvents()
        win.set_console_visible(False)
        app.processEvents()
        win.console.append("冒烟测试日志行")
        app.processEvents()
        check("日志控制台可写入", "冒烟" in win.console.editor.toPlainText())

        # 对话框以非模态方式打开（exec() 会阻塞，不适合自动化冒烟）
        from aircard.ui.dialogs import (AddCardDialog, CreditsDialog,
                                        DiagnosticsDialog)

        credits = CreditsDialog(win)
        credits.open()
        app.processEvents()
        check("鸣谢对话框可打开", credits.isVisible())
        credits.close()

        # 诊断详情必须是可滚动可复制的对话框，且不能是模态（模态会挂死自动化）
        report = DiagnosticsDialog("连接诊断", "连接诊断结果\n\n建议：设备已就绪。", win)
        report.show()
        app.processEvents()
        check("诊断对话框可打开", report.isVisible())
        check("诊断内容可见可复制", "设备已就绪" in report.viewer.toPlainText())
        check("诊断对话框非模态", not report.isModal())
        report.close()

        add = AddCardDialog(win)
        add.open()
        app.processEvents()
        add.editor.setPlainText("Zz9KpQwErTyUiOpAsDfGhJkLzXc=")  # QTextEdit
        app.processEvents()
        check("手动添加对话框取值", len(add.value()) >= 16, add.value())
        add.close()
        app.processEvents()

        # 启动协议告知页（以非模态方式打开，验证可构造、可显示）
        from aircard.ui.agreement import AgreementDialog

        agree = AgreementDialog(win)
        agree.open()
        app.processEvents()
        check("协议告知页可打开", agree.isVisible())
        check("协议告知页含同意/拒绝按钮",
              hasattr(agree, "accept_button") and agree.accept_button is not None)
        agree.close()
        app.processEvents()

        # ---------------- 截图 ----------------
        shots = os.path.join(ROOT, "tests", "_shots")
        os.makedirs(shots, exist_ok=True)
        win.set_tab("wallet")
        app.processEvents()
        win.grab().save(os.path.join(shots, "smoke_wallet.png"))
        win.set_tab("passcode")
        app.processEvents()
        win.grab().save(os.path.join(shots, "smoke_passcode.png"))

        win.close()
        app.processEvents()
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
