"""主窗口 - 与原版 SwiftUI `ContentView` 结构一一对应。"""
from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import VERSION
from ..core.device import environment_probe
from .context import AppContext
from .dialogs import AddCardDialog, CreditsDialog, DiagnosticsDialog
from .passcode_panel import PasscodePanel
from .style import (ACCENT, ACCENT_SOFT, CONTROL_BG, GREEN, PURPLE, RED, Type)
from .wallet_panel import WalletPanel
from .workers import DeviceCheckWorker, FunctionWorker, submit

MAX_LOG_LINES = 600


def _format_code(value: Any) -> str:
    """把 MobileDevice 状态码显示为「0（成功）」或「0xE800001E」形式。"""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return str(value)
    if code == 0:
        return "0（成功）"
    return f"0x{code & 0xFFFFFFFF:08X}"


class HeaderBar(QFrame):
    """顶部标题栏：标识、分页切换、设备状态胶囊、鸣谢入口。"""

    creditsRequested = Signal()      # noqa: N815
    refreshRequested = Signal()      # noqa: N815
    diagnoseRequested = Signal()     # noqa: N815
    tabRequested = Signal(str)       # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setStyleSheet(f"background: {CONTROL_BG};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        icon = QLabel("\U0001F4B3")
        icon.setStyleSheet(f"font-size: 30px; color: {ACCENT};")
        layout.addWidget(icon)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(6)
        name = QLabel("AirCard")
        name.setFont(Type.title2_bold())
        line.addWidget(name)
        version = QLabel(f"v{VERSION}")
        version.setObjectName("versionChip")
        line.addWidget(version)
        line.addStretch(1)
        titles.addLayout(line)
        subtitle = QLabel("钱包卡面皮肤与密码键盘主题")
        subtitle.setFont(Type.caption())
        subtitle.setObjectName("secondary")
        titles.addWidget(subtitle)
        layout.addLayout(titles)

        layout.addStretch(1)

        host = QFrame(self)
        host.setObjectName("segmentHost")
        host.setFixedHeight(30)
        tabs = QHBoxLayout(host)
        tabs.setContentsMargins(3, 3, 3, 3)
        tabs.setSpacing(2)
        self.tab_group = QButtonGroup(host)
        self.tab_group.setExclusive(True)
        for code, label_text in (("wallet", "苹果钱包卡片"),
                                 ("passcode", "密码主题 (.passthm)")):
            button = QPushButton(label_text, host)
            button.setProperty("segment", "true")
            button.setProperty("value", code)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setChecked(code == "wallet")
            tabs.addWidget(button)
            self.tab_group.addButton(button)
        host.setFixedWidth(290)
        layout.addWidget(host)
        self.tab_group.buttonClicked.connect(
            lambda button: self.tabRequested.emit(str(button.property("value"))))

        layout.addStretch(1)

        capsule = QFrame(self)
        capsule.setStyleSheet(f"background: #F7F7FA; border-radius: 16px; "
                              f"border: none;")
        capsule_layout = QHBoxLayout(capsule)
        capsule_layout.setContentsMargins(10, 0, 10, 0)
        capsule_layout.setSpacing(8)
        capsule.setFixedHeight(32)

        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.dot.setStyleSheet(f"background: {RED}; border-radius: 4px;")
        capsule_layout.addWidget(self.dot)

        self.device_title = QLabel("未连接 iPhone（USB）")
        self.device_title.setFont(Type.caption(600))
        self.device_subtitle = QLabel("")
        self.device_subtitle.setFont(Type.caption2())
        self.device_subtitle.setObjectName("secondary")
        self.device_texts = QVBoxLayout()
        self.device_texts.setSpacing(1)
        self.device_texts.addWidget(self.device_title)
        self.device_texts.addWidget(self.device_subtitle)
        capsule_layout.addLayout(self.device_texts)
        self.device_subtitle.hide()

        self.refresh_button = QPushButton("\u21BB")
        self.refresh_button.setProperty("flat", True)
        self.refresh_button.setFixedSize(24, 24)
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.setToolTip("刷新设备连接")
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        capsule_layout.addWidget(self.refresh_button)

        self.diagnose_button = QPushButton("\u2699")
        self.diagnose_button.setProperty("flat", True)
        self.diagnose_button.setFixedSize(24, 24)
        self.diagnose_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.diagnose_button.setToolTip("连接诊断")
        self.diagnose_button.clicked.connect(self.diagnoseRequested.emit)
        capsule_layout.addWidget(self.diagnose_button)
        layout.addWidget(capsule)

        # 显著位置常驻提示：免费软件，禁止倒卖
        free_tip = QLabel("免费软件 · 禁止倒卖")
        free_tip.setFont(Type.caption(700))
        free_tip.setStyleSheet(f"color: {RED};")
        free_tip.setToolTip("本软件完全免费，任何形式的出售、捆绑收费、倒卖均属违规行为。")
        layout.addWidget(free_tip)

        credits = QPushButton("\u2764 鸣谢")
        credits.setCursor(Qt.CursorShape.PointingHandCursor)
        credits.setStyleSheet(f"color: #FF2D55;")
        credits.clicked.connect(self.creditsRequested.emit)
        layout.addWidget(credits)

    def show_device(self, connected: bool, name: str = "", detail: str = "") -> None:
        self.dot.setStyleSheet(
            f"background: {'#34C759' if connected else '#FF3B30'}; border-radius: 4px;")
        if connected:
            self.device_title.setText(name)
            self.device_title.setFixedWidth(150)
            self.device_subtitle.setText(detail)
            self.device_subtitle.show()
        else:
            self.device_title.setText("未连接 iPhone（USB）")
            self.device_title.setFixedWidth(150)
            self.device_subtitle.hide()
            self.device_subtitle.setText("")

    def set_busy(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)

    def set_diagnose_enabled(self, enabled: bool) -> None:
        """只有在检测不到设备时才需要点亮诊断入口。"""
        self.diagnose_button.setVisible(bool(enabled))


class ActivityConsole(QWidget):
    """底部可折叠的活动日志抽屉。"""

    # 扫描时默认只显示钱包相关日志；勾选后回传设备的完整原始日志流。
    verboseToggled = Signal(bool)              # noqa: N815

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 4)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(16, 0, 16, 0)
        title = QLabel("活动日志")
        title.setFont(Type.caption(600))
        title.setObjectName("secondary")
        head.addWidget(title)
        head.addStretch(1)
        self.verbose_box = QCheckBox("显示原始日志（量大）", self)
        self.verbose_box.setToolTip(
            "默认只显示钱包相关日志与进度汇总。\n"
            "勾选后会输出设备上的全部原始日志，便于排查，但刷新非常密集。")
        self.verbose_box.toggled.connect(self.verboseToggled)
        head.addWidget(self.verbose_box)
        clear = QPushButton("清空")
        clear.setProperty("link", "true")
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.clicked.connect(self.clear)
        head.addWidget(clear)
        layout.addLayout(head)

        self.editor = QPlainTextEdit(self)
        self.editor.setReadOnly(True)
        self.editor.setFixedHeight(90)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setStyleSheet(
            f"background: {CONTROL_BG}; border: none; "
            f"font-family: 'Cascadia Mono'; font-size: 10px; color: #6E6E73;")
        # 交给 Qt 裁剪：setMaximumBlockCount 是 O(1)，比手工移动 QTextCursor
        # 逐块删除快几个数量级，日志洪水时不会拖垮界面。
        document = self.editor.document()
        if document is not None:
            document.setMaximumBlockCount(MAX_LOG_LINES)
        layout.addWidget(self.editor)

    def clear(self) -> None:
        self.editor.clear()

    def append(self, text: str) -> None:
        self.editor.appendPlainText(text)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.ctx = AppContext(self)
        self.module_error_shown = False
        self._checking = False
        self._last_probe: dict[str, Any] | None = None
        self.setWindowTitle("AirCard")
        self.resize(1120, 780)
        self.setMinimumSize(980, 700)

        root = QWidget(self)
        root.setObjectName("root")
        self.setCentralWidget(root)
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self.header = HeaderBar(root)
        self.header.creditsRequested.connect(self.show_credits)
        self.header.refreshRequested.connect(self.check_device)
        self.header.diagnoseRequested.connect(self.show_diagnostics)
        self.header.tabRequested.connect(self.set_tab)
        # 连接诊断随时可用：此前它只在"启动时没检测到设备"才被启用，
        # 设备一旦正常连接就再也看不到齿轮（真机反馈）。
        self.header.set_diagnose_enabled(True)
        column.addWidget(self.header)
        column.addWidget(self._separator())

        self.stack = QStackedWidget(root)
        self.wallet = WalletPanel(self.ctx, self.stack)
        self.passcode = PasscodePanel(self.ctx, self.stack)
        self.stack.addWidget(self.wallet)
        self.stack.addWidget(self.passcode)
        column.addWidget(self.stack, 1)

        self.console = ActivityConsole(root)
        column.addWidget(self.console)
        self.console.setVisible(False)
        self.console_separator = self._separator()
        column.addWidget(self.console_separator)
        self.console_separator.setVisible(False)

        self._build_bottom_bar()
        column.addWidget(self.bottom_bar)

        self._connect_context()
        self.set_tab("wallet")
        self.check_device()

    # ------------------------------------------------------------------
    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background: #D9D9DE; max-height: 1px; border: none;")
        return line

    def _build_bottom_bar(self) -> None:
        self.bottom_bar = QWidget()
        self.bottom_bar.setStyleSheet(f"background: {CONTROL_BG};")
        layout = QVBoxLayout(self.bottom_bar)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(8)

        self.progress_bar = QProgressBar(self.bottom_bar)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        row = QHBoxLayout()
        row.setSpacing(16)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.status_label = QLabel("就绪")
        self.status_label.setFont(Type.caption(500))
        line.addWidget(self.status_label)
        self.percent_label = QLabel("")
        self.percent_label.setFont(Type.caption(600))
        self.percent_label.setObjectName("secondary")
        line.addWidget(self.percent_label)
        line.addStretch(1)
        texts.addLayout(line)
        self.summary_label = QLabel("")
        self.summary_label.setFont(Type.caption2())
        self.summary_label.setObjectName("secondary")
        texts.addWidget(self.summary_label)
        row.addLayout(texts, 1)

        self.log_button = QPushButton("\u2328 日志 \u25b2")
        self.log_button.setFont(Type.caption())
        self.log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_button.setCheckable(True)
        self.log_button.clicked.connect(self.toggle_console)
        row.addWidget(self.log_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.flash_button = QPushButton("刷入皮肤")
        self.flash_button.setProperty("cta", "green")
        self.flash_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.flash_button.setMinimumWidth(150)
        # 显式内联样式：保证按钮文字在任何构建/系统主题下都清晰可见
        # （覆盖全局 QSS，作为双保险，避免某些环境下出现"白字白底看不见"）。
        self.flash_button.setStyleSheet(
            "QPushButton{color:#FFFFFF;background:#34C759;border:1px solid #34C759;"
            "border-radius:7px;padding:4px 12px;font-size:12px;font-weight:600;}"
            "QPushButton[cta=\"purple\"]{background:#A855F7;border:1px solid #A855F7;}"
            "QPushButton:hover{background:#4FD873;}"
            "QPushButton[cta=\"purple\"]:hover{background:#B96BFA;}"
            "QPushButton:disabled{color:#1F5C32;background:#C9EFD2;"
            "border-color:#C9EFD2;}"
            "QPushButton[cta=\"purple\"]:disabled{color:#3D2A5C;background:#E6D2FB;"
            "border-color:#E6D2FB;}")
        self.flash_button.clicked.connect(self.start_flash)
        row.addWidget(self.flash_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(row)

        credits = QHBoxLayout()
        credits.setContentsMargins(0, 0, 0, 0)
        credits.addStretch(1)
        by = QLabel("作者：")
        by.setFont(Type.caption2())
        by.setObjectName("secondary")
        credits.addWidget(by)
        for text, url in (("@mak5er", "https://github.com/mak5er"),
                          ("@Lumid-Off", "https://github.com/Lumid-Off")):
            link = QLabel(f'<a href="{url}" style="text-decoration: none;">{text}</a>')
            link.setFont(Type.caption2())
            link.setOpenExternalLinks(True)
            credits.addWidget(link)
            if url.endswith("mak5er"):
                sep = QLabel("与")
                sep.setFont(Type.caption2())
                sep.setObjectName("secondary")
                credits.addWidget(sep)
        layout.addLayout(credits)

    # ------------------------------------------------------------------
    def _connect_context(self) -> None:
        self.ctx.logRequested.connect(self.console.append)
        self.ctx.statusRequested.connect(self._on_status)
        self.ctx.progressRequested.connect(self._on_progress)
        self.ctx.errorRequested.connect(self.show_error)
        self.ctx.successRequested.connect(self._on_success)
        self.ctx.deviceChanged.connect(self._on_device_changed)
        self.ctx.targetsChanged.connect(self.passcode.on_targets_changed)
        self.ctx.busyChanged.connect(self._on_busy_changed)

        self.wallet.stateChanged.connect(self.refresh_bottom)
        self.wallet.addManuallyRequested.connect(self.show_add_card)
        self.wallet.scanningChanged.connect(lambda _v: self.refresh_bottom())
        self.console.verboseToggled.connect(self.wallet.set_scan_verbose)
        self.passcode.stateChanged.connect(self.refresh_bottom)

    # ------------------------------------------------------------------
    # 分页
    # ------------------------------------------------------------------
    def set_tab(self, value: str) -> None:
        index = 1 if value == "passcode" else 0
        self.stack.setCurrentIndex(index)
        for button in self.header.tab_group.buttons():
            button.setChecked(button.property("value") == value)
        if value == "passcode" and self.wallet.scanning:
            self.wallet.stop_scanning()
        if "双击侧边按钮" in self.ctx.last_status:
            self.ctx.status("就绪")
        self.refresh_bottom()

    def _active_panel(self):
        return self.passcode if self.stack.currentIndex() == 1 else self.wallet

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------
    def check_device(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.header.set_busy(True)
        self.ctx.status("正在检测已连接设备…")
        worker = DeviceCheckWorker()
        worker.signals.finished.connect(self._on_device_checked)
        submit(worker)

    def _on_device_checked(self, device: Any, error: str) -> None:
        self._checking = False
        self.header.set_busy(False)
        if error:
            self.ctx.status("设备检测失败。")
            self.ctx.log(f"设备检测失败：{error}")
            self._maybe_report_modules(error)
        self.ctx.set_device(device if device else None)
        if not device and not error:
            self._start_environment_probe()

    def _start_environment_probe(self) -> None:
        """未发现设备时，异步跑一遍链路诊断，把可操作建议写进状态栏与日志。"""
        worker = FunctionWorker("device-probe", environment_probe)
        worker.signals.finished.connect(self._on_environment_probe)
        submit(worker)

    def _on_environment_probe(self, token: Any, report: Any) -> None:
        if token != "device-probe" or not isinstance(report, dict):
            return
        self._last_probe = report
        # 齿轮必须先恢复：它此前只在 hint 非空时才启用，一旦诊断链路被拖慢
        # （例如加入耗时探测）按钮就再也不出现。诊断详情任何时候都有用。
        self.header.set_diagnose_enabled(True)
        hint = str(report.get("hint") or "")
        if not hint:
            return
        self.ctx.log(f"连接诊断：{hint}")
        if self.ctx.device is None:
            self.ctx.status(hint)
            self.header.show_device(False, "未检测到 iPhone", "点击右侧 ↻ 重新检测")

    def show_diagnostics(self) -> None:
        """弹出连接诊断详情（含 airlift 通道探测，故比启动诊断慢一点）。"""
        worker = FunctionWorker(
            "device-probe-manual", lambda: environment_probe(include_atc=True))
        worker.signals.finished.connect(self._show_diagnostics_report)
        submit(worker)
        self.ctx.status("正在诊断连接…")

    def _show_diagnostics_report(self, token: Any, report: Any) -> None:
        if token != "device-probe-manual":
            return
        if not isinstance(report, dict):
            QMessageBox.warning(self, "连接诊断", "诊断失败，无法获取环境信息。")
            return
        self._last_probe = report
        serials = report.get("usbmux_serials") or []
        lines = [
            "连接诊断结果",
            "",
            f"Apple 运行库：{'已加载' if report.get('apple_runtime') else '未加载'}",
            f"支持目录：{report.get('support_dir') or '（无）'}",
            f"MobileDevice 枚举到设备数：{report.get('md_device_count')}",
            f"usbmux 服务（端口 27015）：{'可达' if report.get('usbmux_reachable') else '不可达'}",
            f"usbmux 报告的设备：{('、'.join(serials) if serials else '（无）')}",
        ]

        detail = report.get("device_detail")
        if isinstance(detail, dict) and detail:
            lines.extend(["", "设备握手逐步状态（0 表示成功）："])
            if detail.get("udid"):
                lines.append(f"  UDID：{detail['udid']}")
            for label, key in (("AMDeviceConnect", "connect"),
                               ("AMDeviceIsPaired", "paired"),
                               ("AMDevicePair", "pair"),
                               ("AMDeviceValidatePairing", "validate"),
                               ("AMDeviceStartSession", "session")):
                value = detail.get(key)
                if value is None:
                    continue
                lines.append(f"  {label}：{_format_code(value)}")
            if detail.get("name") or detail.get("product"):
                lines.append(
                    f"  设备：{detail.get('name') or '（未读到名称）'} · "
                    f"{detail.get('product') or '（未读到型号）'} · "
                    f"iOS {detail.get('version') or '（未知）'}")
        atc = report.get("atc")
        if isinstance(atc, dict) and atc:
            lines.extend(["", "airlift 同步通道（换卡面 / 读卡面 / 写主题都走它）："])
            lines.append(f"  AirTraffic 连接：{'已建立' if atc.get('connected') else '失败'}")
            lines.append(
                f"  设备是否授予同步许可（SyncAllowed）："
                f"{'是' if atc.get('sync_allowed') else '否'}")
            # UDID 写法很关键：AirTrafficHost 内部按标识查设备，查不到就一条消息都不发
            candidates = atc.get("candidates") or []
            if candidates:
                lines.append(f"  尝试的设备标识：{' → '.join(str(c) for c in candidates)}")
            if atc.get("identifier"):
                lines.append(f"  实际连上的标识：{atc['identifier']}")
            state = atc.get("state")
            if isinstance(state, dict):
                lines.append(f"  ATC 会话号：{state.get('session')}；"
                             f"Grappa 会话：{state.get('grappa') or '（无）'}")
            elif atc.get("session") is not None or atc.get("grappa"):
                lines.append(f"  ATC 会话号：{atc.get('session')}；"
                             f"Grappa 会话：{atc.get('grappa') or '（无）'}")
            messages = atc.get("messages") or []
            lines.append(f"  握手期间收到 {len(messages)} 条消息")
            for item in messages[:12]:
                lines.append(f"    · {str(item)[:160]}")
            for attempt in (atc.get("attempts") or [])[:4]:
                if not isinstance(attempt, dict):
                    continue
                lines.append(f"    标识 {attempt.get('identifier')}："
                             f"连接={'成功' if attempt.get('connected') else '失败'}，"
                             f"消息 {attempt.get('messages')} 条"
                             + (f"，{attempt['error']}" if attempt.get("error") else ""))
            if atc.get("error"):
                lines.append(f"  错误：{atc['error']}")
            asl = atc.get("asl") or []
            if asl:
                lines.append("")
                lines.append(f"  Apple 组件日志（本次探测期间，{len(asl)} 行）：")
                for item in asl[:20]:
                    lines.append(f"    · {item}")
            if not atc.get("sync_allowed"):
                lines.extend([
                    "",
                    "  → 这条通道不通，换卡面 / 读卡面都会失败。常见原因：",
                    "    1. 本机从未用 iTunes / Apple Devices 与该 iPhone 同步过；",
                    "    2. iPhone 已锁屏或未点「信任此电脑」；",
                    "    3. Apple Mobile Device Support 版本过旧；",
                    "    4. MDM / 描述文件限制了同步。",
                ])

        detail = report.get("device_detail")
        if isinstance(detail, dict) and detail.get("exception"):
            lines.append(f"  异常：{detail['exception']}")

        if report.get("usbmux_error"):
            lines.append("")
            lines.append(f"usbmux 错误：{report['usbmux_error']}")
        if report.get("runtime_error"):
            lines.append(f"运行库错误：{report['runtime_error']}")
        lines.extend(["", "建议：", report.get("hint") or "（无）"])
        body = "\n".join(lines)
        # 完整报告同步写进日志面板，便于用户直接从那里复制
        self.ctx.log("连接诊断结果：\n" + body)
        dialog = DiagnosticsDialog("连接诊断", body, self)
        dialog.show()
        dialog.raise_()

    def _maybe_report_modules(self, error: str) -> None:
        if self.module_error_shown:
            return
        low = str(error).lower()
        if "找不到" in str(error) or "loadlibrary" in low or "126" in str(error):
            self.module_error_shown = True
            QMessageBox.warning(
                self, "缺少 Apple 运行库",
                "未能加载 Apple 的 MobileDevice / AirTrafficHost 组件。\n\n"
                "请先在 Windows 上安装 iTunes（或 Apple Devices）与 iCloud，"
                "使以下目录存在：\n"
                "C:\\Program Files\\Common Files\\Apple\\Mobile Device Support")

    def _on_device_changed(self, device: Any) -> None:
        if device:
            name = str(device.get("name") or "iPhone")
            detail = f"{device.get('product', '')} · iOS {device.get('version', '')}"
            self.header.show_device(True, name, detail)
            self.ctx.status(f"已连接到 {name}")
            self.ctx.log(f"设备已连接：{name}（{detail}）")
            self.ctx.apply_device_preferences(device)
        else:
            self.header.show_device(False)
            self.ctx.status("未检测到 iPhone，请通过 USB 连接。")
        self.wallet.on_device_changed()
        self.passcode.on_device_changed()
        self.refresh_bottom()

    # ------------------------------------------------------------------
    # 底部状态
    # ------------------------------------------------------------------
    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)
        # 刷入进行时，让左下角摘要同步反映本次刷入进度（如"正在刷入 1/1 张卡"），
        # 而不是停留在进入刷入前那句"已选中 1/3 张卡片"。
        if self.ctx.busy and self.ctx.op == "flash":
            match = re.match(r"\[(\d+)/(\d+)\]", text)
            if match:
                self.summary_label.setText(
                    f"正在刷入 {match.group(1)}/{match.group(2)} 张卡")

    def _on_progress(self, value: float) -> None:
        percent = int(max(0.0, min(1.0, value)) * 100)
        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")
        visible = self.ctx.busy or percent > 0
        self.progress_bar.setVisible(visible)
        self.percent_label.setVisible(visible)

    def _on_busy_changed(self, busy: bool) -> None:
        # 需求②(v1.9.3)：日志栏默认始终关闭，仅用户手动点击"日志"按钮才展开。
        # 任何操作（刷入 / 读取 / 备份 / 出错）都不再自动点开日志栏。
        self.header.set_busy(busy)
        self.refresh_bottom()

    def refresh_bottom(self) -> None:
        panel = self._active_panel()
        self.flash_button.setText(panel.flash_button_label())
        self.flash_button.setEnabled(panel.flash_enabled())
        accent = "purple" if self.stack.currentIndex() == 1 else "green"
        if self.flash_button.property("cta") != accent:
            self.flash_button.setProperty("cta", accent)
            style = self.flash_button.style()
            if style is not None:
                style.polish(self.flash_button)
        if self.stack.currentIndex() == 1:
            self.summary_label.setText(self.passcode.summary())
        else:
            self.summary_label.setText(self.wallet.selection_summary())

    def toggle_console(self) -> None:
        self.set_console_visible(not self.console.isVisible())

    def set_console_visible(self, visible: bool) -> None:
        self.console.setVisible(visible)
        self.console_separator.setVisible(visible)
        self.log_button.setChecked(visible)
        self.log_button.setText(f"\u2328 日志 {'\u25bc' if visible else '\u25b2'}")

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def start_flash(self) -> None:
        self._active_panel().start_flash()

    def show_error(self, message: str) -> None:
        # 需求②(v1.9.3)：出错也不再自动展开日志栏，保持默认关闭；
        # 用户如需查看诊断信息，手动点击"日志"按钮即可。
        QMessageBox.warning(self, "AirCard", message)

    def _on_success(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def show_credits(self) -> None:
        CreditsDialog(self).exec()

    def show_add_card(self) -> None:
        dialog = AddCardDialog(self)
        if dialog.exec() != AddCardDialog.DialogCode.Accepted:
            return
        count = self.wallet.add_hashes(dialog.value())
        if count:
            self.ctx.status(f"已添加 {count} 张卡片。")
        else:
            self.ctx.status("没有新增的卡片哈希。")

    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        try:
            self.wallet.stop_scanning()
        except Exception:
            pass
        super().closeEvent(event)
