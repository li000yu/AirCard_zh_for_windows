"""共享应用状态：设备信息、刷入目标与全局 UI 回调。

原版里这些内容都挂在 SwiftUI 的 `AppViewModel` 上。这里抽成一个 QObject，
由主窗口持有，各面板通过它读写状态，避免互相引用造成循环依赖。
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

DEFAULT_VERSION = "TelephonyUI-10"
DEFAULT_LANGUAGE = "all"
DEFAULT_BOLD = "both"


class AppContext(QObject):
    logRequested = Signal(str)                 # noqa: N815
    statusRequested = Signal(str)              # noqa: N815
    progressRequested = Signal(float)          # noqa: N815
    errorRequested = Signal(str)               # noqa: N815
    successRequested = Signal(str, str)        # noqa: N815
    deviceChanged = Signal(object)             # noqa: N815
    targetsChanged = Signal()                  # noqa: N815
    busyChanged = Signal(bool)                 # noqa: N815

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._device: dict[str, Any] | None = None
        self._busy = False
        # 当前正在进行的操作类型（readface / backup / restore / native /
        # restore_file / backup_pre / flash / ""）。用于区分"读取/备份卡面"与
        # "刷入皮肤"，从而决定日志栏是否自动展开、底部按钮显示什么文案。
        self._op = ""
        self.last_status = "就绪"
        self.target_version = DEFAULT_VERSION
        self.target_language = DEFAULT_LANGUAGE
        self.target_bold = DEFAULT_BOLD

    # -- 设备 --------------------------------------------------------
    @property
    def device(self) -> dict[str, Any] | None:
        return self._device

    def set_device(self, device: dict[str, Any] | None) -> None:
        self._device = device
        self.deviceChanged.emit(device)

    def udid(self) -> str:
        if not self._device:
            return ""
        return str(self._device.get("udid") or "")

    def connected(self) -> bool:
        return bool(self.udid())

    def device_label(self) -> str:
        if not self._device:
            return "未连接 iPhone（USB）"
        name = self._device.get("name") or "iPhone"
        product = self._device.get("product") or ""
        version = self._device.get("version") or ""
        return f"{name} · {product} · iOS {version}"

    # -- 忙碌状态 ----------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, value: bool) -> None:
        if self._busy == value:
            return
        self._busy = value
        if not value:
            # 操作结束：清空操作类型，避免残留影响下一次状态判断。
            self._op = ""
        self.busyChanged.emit(value)

    # -- 当前操作类型 -------------------------------------------------
    @property
    def op(self) -> str:
        return self._op

    def set_op(self, op: str) -> None:
        """记录当前正在进行的操作（见 __init__ 注释）。不影响 busy 信号。"""
        self._op = op or ""

    # -- 刷入目标 ----------------------------------------------------
    def target_summary(self) -> str:
        return f"{self.target_version} · {self.target_language.upper()} · {self.target_bold}"

    def set_targets(self, version: str | None = None,
                    language: str | None = None,
                    bold: str | None = None) -> None:
        if version is not None:
            self.target_version = version
        if language is not None:
            self.target_language = language
        if bold is not None:
            self.target_bold = bold
        self.targetsChanged.emit()

    def apply_device_preferences(self, device: dict[str, Any]) -> None:
        """等价于原版 `applyDevicePreferences(from:)`。"""
        version = str(device.get("version") or "")
        major_text = version.split(".")[0] if version else ""
        try:
            major = int(major_text)
        except ValueError:
            major = 0
        if major >= 18:
            resolved = "TelephonyUI-10"
        elif major >= 16:
            resolved = "TelephonyUI-9"
        else:
            resolved = "TelephonyUI-8"

        language = DEFAULT_LANGUAGE
        raw_language = str(device.get("language") or "").split("-")[0].lower()
        from ..core.passthm import language_codes
        if raw_language and raw_language in language_codes():
            language = raw_language

        bold = str(device.get("bold_text") or "").lower() == "true"
        self.set_targets(resolved, language, "bold" if bold else "regular")
        self.log(
            "  \u26a1 已按 iPhone 自动配置目标："
            f"{resolved}，语言：{language}，字重：{'bold' if bold else 'regular'}")

    # -- UI 回调 -----------------------------------------------------
    def log(self, text: str) -> None:
        self.logRequested.emit(text)

    def status(self, text: str) -> None:
        self.last_status = text
        self.statusRequested.emit(text)

    def progress(self, value: float) -> None:
        self.progressRequested.emit(max(0.0, min(1.0, float(value))))

    def error(self, text: str) -> None:
        self.errorRequested.emit(text)

    def success(self, message: str, title: str = "成功！") -> None:
        self.successRequested.emit(title, message)
