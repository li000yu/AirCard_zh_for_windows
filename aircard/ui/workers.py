"""后台任务：所有耗时操作都跑在工作线程里，通过信号回到 UI 线程。

Qt 的信号跨线程自动排队，ctypes 调用期间会释放 GIL，因此界面始终保持响应。
关闭窗口时可能仍有任务在跑，所以所有 emit 都做了 RuntimeError 保护。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_POOL = QThreadPool.globalInstance()
_POOL.setMaxThreadCount(max(4, (_POOL.maxThreadCount() or 4)))

# 设备统一日志流在 StreamFlags=0x3C / MessageFilter=0xFFFF 下会推送设备上
# **所有进程**的日志，实测可达每秒数千行。逐行 emit 会把 Qt 事件队列打满，
# 界面直接卡死。因此日志按批节流回传：卡片哈希仍然逐行实时分析，绝不丢数据。
_LOG_FLUSH_SECONDS = 0.2      # 回传间隔：硬性上限 = 每秒 5 次
_LOG_MAX_PENDING = 120        # 单批最多行数，超出部分计入"已省略"
_LOG_HEARTBEAT_SECONDS = 5.0  # 周期性进度汇总，避免"看起来没在动"

_KEEP_ALIVE: list[QRunnable] = []


def submit(runnable: QRunnable) -> None:
    """提交任务，并保留一个引用防止 Python 提前回收。"""
    _KEEP_ALIVE.append(runnable)
    if len(_KEEP_ALIVE) > 64:
        del _KEEP_ALIVE[:32]
    _POOL.start(runnable)


def _emit(signal: Any, *args: Any) -> None:
    """发送信号；若接收方已销毁（例如正在退出）则静默忽略。"""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass


class BaseSignals(QObject):
    log = Signal(str)


# ---------------------------------------------------------------------------
# 通用函数任务（图片处理、主题解析、导出打包等）
# ---------------------------------------------------------------------------

class FunctionWorker(QRunnable):
    class Signals(BaseSignals):
        finished = Signal(object, object)   # token, result
        failed = Signal(object, str)        # token, error message

    def __init__(self, token: Any, function: Callable[[], Any]) -> None:
        super().__init__()
        self.signals = FunctionWorker.Signals()
        self._token = token
        self._function = function

    def run(self) -> None:
        try:
            result = self._function()
        except Exception as error:  # noqa: BLE001 - 必须把异常送回 UI 线程
            _emit(self.signals.failed, self._token, str(error))
        else:
            _emit(self.signals.finished, self._token, result)


# ---------------------------------------------------------------------------
# 设备检测
# ---------------------------------------------------------------------------

class DeviceCheckWorker(QRunnable):
    class Signals(BaseSignals):
        finished = Signal(object, str)      # device dict | None, error

    def __init__(self) -> None:
        super().__init__()
        self.signals = DeviceCheckWorker.Signals()

    def run(self) -> None:
        try:
            from ..core.device import get_connected_device

            device = get_connected_device()
        except Exception as error:  # noqa: BLE001
            _emit(self.signals.finished, None, str(error))
            return
        _emit(self.signals.finished, device, "")


# ---------------------------------------------------------------------------
# 实时卡片扫描
# ---------------------------------------------------------------------------

class DeviceScanWorker(QRunnable):
    class Signals(BaseSignals):
        cardFound = Signal(str)             # noqa: N815
        finished = Signal(str)              # 结束原因

    def __init__(self, udid: str, known: set[str]) -> None:
        super().__init__()
        self.signals = DeviceScanWorker.Signals()
        self._udid = udid
        self._known = set(known)
        self._stream: Any = None
        self._stopped = False
        # 默认只回传"钱包相关 + 扫描器"日志；勾上界面的"全部日志"才回传原始流。
        # 该字段只被 UI 线程写、工作线程读（bool 赋值在 CPython 下是原子的）。
        self.verbose = False

    def request_stop(self) -> None:
        self._stopped = True
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001
                pass

    def run(self) -> None:
        stream: Any = None
        reason = ""
        try:
            # 导入必须放在 try 内：任何导入期异常都要能走到 finally 发出 finished，
            # 否则工作线程会静默死亡、界面永远停在“扫描中”。
            from ..core.device import DeviceLogStream
            from ..core.scanner import (extract_hashes, extract_path_hashes,
                                       has_hash_hint, hash_source, is_wallet_line,
                                       resource_hints, should_scan)

            from ..core.device import _SCANNER_PREFIX

            stream = DeviceLogStream(self._udid)
            self._stream = stream
            ok, message = stream.open()
            _emit(self.signals.log, message)
            if not ok:
                reason = message
                return

            pending: list[str] = []
            dropped = 0
            total = 0
            wallet_seen = 0
            started = time.monotonic()
            last_flush = started
            last_beat = started
            # passd 的资源查找结果能直接告诉我们"某张卡有没有合并卡面文件"，
            # 而合并卡面文件不存在 == 这张卡没刷过皮肤 == 备份卡面必然失败。
            customized: set[str] = set()
            missing_hinted = False

            def flush(now: float) -> None:
                nonlocal last_flush
                if not pending:
                    return
                _emit(self.signals.log, "".join(pending))
                pending.clear()
                last_flush = now

            while not self._stopped:
                try:
                    line = stream.read_line()
                except Exception as error:  # noqa: BLE001
                    reason = f"读取日志失败：{error}"
                    break
                if line is None:
                    reason = "设备日志流已结束。"
                    break

                total += 1
                # 展示判定（收紧后只保留真正的钱包进程，噪音不再淹没有效信息）
                interesting = line.startswith(_SCANNER_PREFIX) or is_wallet_line(line)
                if interesting:
                    wallet_seen += 1

                # 卡号提取：路径提示命中 → 只跑路径型正则（快且不会误命中裸
                # base64）；否则沿用原版宽松规则跑全套。这是旧行为的超集，绝不漏卡。
                if has_hash_hint(line):
                    candidates = extract_path_hashes(line)
                elif interesting or should_scan(line):
                    candidates = extract_hashes(line)
                else:
                    candidates = ()
                for candidate in candidates:
                    if candidate in self._known:
                        continue
                    self._known.add(candidate)
                    _emit(self.signals.cardFound, candidate)
                    if hash_source(line) == "token":
                        _emit(self.signals.log,
                              f"{_SCANNER_PREFIX}卡片 {candidate[:12]}… 只匹配到裸 "
                              "base64 令牌（未见卡包路径），可信度较低；\n"
                              "  若它刷入失败，多半是日志里的无关 ID，请忽略或用"
                              "「\U0001F4BE 备份卡面」验证。\n")

                for found_hash, leaf, exists in resource_hints(line):
                    if "cardbackground" not in leaf.lower():
                        continue
                    if exists:
                        customized.add(found_hash)
                    elif not missing_hinted:
                        missing_hinted = True
                        _emit(self.signals.log,
                              f"{_SCANNER_PREFIX}提示：日志里出现了 "
                              "cardBackgroundCombined 查找失败（Result: None）的记录。\n"
                              "  说明至少有一张卡**尚未刷入过皮肤**（磁盘上没有合并卡面文件）。\n"
                              "  这类卡片「\U0001F4BE 备份卡面」必然失败，属正常现象；\n"
                              "  届时软件会询问是否改读「卡包原图」来认卡。\n")

                if interesting or self.verbose:
                    if len(pending) < _LOG_MAX_PENDING:
                        pending.append(line if line.endswith("\n") else line + "\n")
                    else:
                        dropped += 1
                else:
                    dropped += 1

                now = time.monotonic()
                if pending and now - last_flush >= _LOG_FLUSH_SECONDS:
                    flush(now)
                if now - last_beat >= _LOG_HEARTBEAT_SECONDS:
                    elapsed = max(now - started, 0.001)
                    summary = (f"{_SCANNER_PREFIX}已分析 {total} 行设备日志"
                               f"（约 {total / elapsed:.0f} 行/秒），"
                               f"其中钱包相关 {wallet_seen} 行")
                    if dropped:
                        summary += f"，已省略 {dropped} 行无关日志"
                        dropped = 0
                    pending.append(summary + "。\n")
                    last_beat = now

            flush(time.monotonic())
        except Exception as error:  # noqa: BLE001
            reason = f"扫描异常：{error}"
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
            self._stream = None
            _emit(self.signals.finished, reason)


# ---------------------------------------------------------------------------
# 钱包卡面刷入
# ---------------------------------------------------------------------------

class CardFlashWorker(QRunnable):
    class Signals(BaseSignals):
        status = Signal(str)
        progress = Signal(float)
        cardResult = Signal(str, bool)      # noqa: N815
        finished = Signal(bool, str)

    def __init__(self, udid: str, jobs: list[tuple[str, str]],
                 auto_backup: bool = False) -> None:
        """jobs: [(card_hash, image_path), ...]

        auto_backup: 刷入前先为该卡做一次原皮肤备份（已有备份则自动跳过）。
        """
        super().__init__()
        self.signals = CardFlashWorker.Signals()
        self._udid = udid
        self._jobs = jobs
        self._auto_backup = bool(auto_backup)

    def run(self) -> None:
        from ..core.cardflow import flash_card
        from ..core.cardskin import ensure_backup

        total = len(self._jobs)
        if total == 0:
            _emit(self.signals.finished, True, "没有需要刷入的卡片。")
            return

        _emit(self.signals.log, f"开始为 {total} 张卡片应用皮肤…")
        failed = False
        for index, (card_hash, image_path) in enumerate(self._jobs):
            position = index + 1
            _emit(self.signals.status,
                  f"[{position}/{total}] 正在准备 {card_hash[:10]}… 的卡面")
            _emit(self.signals.progress, (index + 0.05) / total)
            _emit(self.signals.log, f"刷入卡片 [{position}/{total}]：{card_hash}")

            # 首次刷入该卡前先备份原皮肤；备份失败只记录，不阻断刷入。
            if self._auto_backup:
                def note(text: str, _p: int = position) -> None:
                    _emit(self.signals.log, f"  [{_p}/{total}] 备份诊断：{text}")

                try:
                    ok, message = ensure_backup(self._udid, card_hash, reporter=note)
                except Exception as error:  # noqa: BLE001
                    ok, message = False, f"原皮肤备份异常：{error}"
                _emit(self.signals.log, f"  原皮肤备份：{message}")
                if not ok:
                    _emit(self.signals.log,
                          "  （备份未成功，刷入继续；之后可在卡片上点「备份卡面」重试）")

            def handler(message: str, step: int, step_total: int,
                        _index: int = index, _pos: int = position) -> None:
                if step_total > 0:
                    _emit(self.signals.progress,
                          min((_index + step / step_total) / total, 1.0))
                _emit(self.signals.status, f"[{_pos}/{total}] {message}")
                _emit(self.signals.log, f"  {message}")

            try:
                ok = flash_card(self._udid, card_hash, image_path,
                                on_message=handler, logger=lambda text: None)
            except Exception as error:  # noqa: BLE001
                _emit(self.signals.log, f"  刷入失败：{error}")
                ok = False
            _emit(self.signals.cardResult, card_hash, ok)
            if not ok:
                _emit(self.signals.log, f"卡片 {card_hash[:12]}… 更新失败。")
                failed = True
                break
            # 需求①(v1.9.3)：成功刷入后把"最近一次刷入的新皮肤"记录下来，
            # 下次打开软件 / 重新扫描时直接在卡片预览里显示（不再回退原皮）。
            try:
                from pathlib import Path as _P
                from ..core import skinstore

                skinstore.save_last_flash(card_hash, _P(image_path).read_bytes())
            except Exception as error:  # noqa: BLE001
                _emit(self.signals.log,
                      f"  （已刷入成功，但记录最近皮肤失败：{card_hash[:12]}…"
                      f"，不影响设备上的皮肤：{error}）")
            _emit(self.signals.progress, (index + 1) / total)

        if failed:
            _emit(self.signals.finished, False,
                  "部分卡片皮肤未能应用，请查看日志后重试。")
        else:
            _emit(self.signals.finished, True, "全部卡片更新完成！")


# ---------------------------------------------------------------------------
# 原卡面备份 / 恢复
# ---------------------------------------------------------------------------

class CardSkinWorker(QRunnable):
    """mode="readface" 只读预览当前卡面 / "backup" 备份原皮 / "restore" 写回
    / "restore_file" 写回用户挑选的历史卡面 / "native" 读卡包原图。"""

    class Signals(BaseSignals):
        status = Signal(str)
        progress = Signal(float)
        # ok, message, card_hash, mode, info(dict)；info 携带额外信息（如卡面尺寸）
        finished = Signal(bool, str, str, str, object)

    def __init__(self, udid: str, card_hash: str, mode: str,
                 file_path: str | None = None) -> None:
        """mode:
        · "readface" 只读预览设备当前卡面（辨认用，不写备份）
        · "backup"   读取并备份原皮到软件备份目录（含历史快照）
        · "restore"  写回软件备份目录里的最新原皮
        · "restore_file" 写回用户手动挑选的历史卡面 PNG（file_path 指定）
        · "native"   读卡包原图（strip/logo 等）
        """
        super().__init__()
        self.signals = CardSkinWorker.Signals()
        self._udid = udid
        self._card_hash = card_hash
        self._file_path = file_path
        if mode in ("restore", "native", "readface", "backup", "restore_file"):
            self._mode = mode
        else:
            self._mode = "backup"

    def run(self) -> None:
        label = {"restore": "恢复原皮肤",
                 "native": "读取卡包原图",
                 "readface": "读取卡面",
                 "backup": "备份卡面",
                 "restore_file": "恢复历史卡面"}[self._mode]

        info: dict = {}
        size: tuple[int, int] | None = None

        def note(text: str) -> None:
            """airlift 逐步诊断信息实时送到日志抽屉。"""
            _emit(self.signals.log, f"  · {text}")

        try:
            if self._mode == "restore":
                from ..core.cardskin import restore_original_skin

                ok, message = restore_original_skin(
                    self._udid, self._card_hash, reporter=note)
            elif self._mode == "restore_file":
                from ..core.cardskin import restore_from_file

                ok, message = restore_from_file(
                    self._udid, self._card_hash, self._file_path or "",
                    reporter=note)
            elif self._mode == "native":
                from ..core import skinstore
                from ..core.cardskin import build_preview, read_native_artwork

                _emit(self.signals.status,
                      f"正在{label}：{self._card_hash[:12]}…")
                _emit(self.signals.progress, 0.15)
                result = read_native_artwork(self._udid, self._card_hash,
                                             reporter=note)
                if not result:
                    ok, message = False, (
                        "没能读到卡包里的图片素材。\n\n"
                        "已尝试 strip / logo / thumbnail / background / footer / icon "
                        "等多种常见素材名，设备上都没有返回数据，无法生成预览。")
                else:
                    leaf, data = result
                    _emit(self.signals.progress, 0.8)
                    if skinstore.save_artwork(self._card_hash, leaf, data,
                                              build_preview(data)):
                        ok = True
                        message = (f"已读回卡包原图：{leaf}（{len(data) / 1024:.0f} KB）")
                    else:
                        ok, message = False, "读到了素材，但写入本地缓存失败。"
            elif self._mode == "readface":
                from ..core import skinstore
                from ..core.cardskin import (build_preview, image_size,
                                            read_original_skin)

                _emit(self.signals.status,
                      f"正在{label}：{self._card_hash[:12]}…")
                _emit(self.signals.progress, 0.15)
                data = read_original_skin(self._udid, self._card_hash,
                                          reporter=note)
                if not data:
                    ok, message = False, (
                        "读取卡面失败：设备没有返回卡面数据。\n"
                        "如提示「同步未授权」，请先在 iTunes / Apple Devices "
                        "里与该 iPhone 同步一次。")
                else:
                    preview = build_preview(data)
                    size = image_size(data)
                    info["size"] = size
                    # 每次读取都写一份带时间戳的自动备份（auto_<时间戳>_<哈希>.png），
                    # 「恢复原皮」即恢复最新一份自动备份；与手动备份各存各的、互不冲突。
                    saved = skinstore.save_auto_backup(self._card_hash, data)
                    if saved is not None:
                        ok = True
                        size_text = (f"{size[0]} × {size[1]} px" if size else "未知尺寸")
                        message = (f"已读取当前卡面（{len(data) / 1024:.0f} KB，"
                                   f"{size_text}），用于辨认，并已自动备份到本机。")
                    else:
                        ok, message = False, "已读到卡面，但写入本地自动备份失败。"
            else:
                from ..core.cardskin import backup_original_skin

                _emit(self.signals.status, f"正在{label}：{self._card_hash[:12]}…")
                _emit(self.signals.progress, 0.15)
                ok, message = backup_original_skin(
                    self._udid, self._card_hash, force=True, reporter=note)
        except Exception as error:  # noqa: BLE001
            ok, message = False, f"{label}失败：{error}"
        _emit(self.signals.progress, 1.0 if ok else 0.0)
        _emit(self.signals.finished, ok, message, self._card_hash, self._mode, info)


class BackupWorker(QRunnable):
    """刷入前批量备份多张卡的原皮肤（每张卡各自独立，失败不中断其余）。"""

    class Signals(BaseSignals):
        cardProgress = Signal(int, int, str)        # noqa: N815  第几张, 总数, 哈希
        finished = Signal(bool, str, list)          # 全部成功?, 汇总信息, 失败哈希列表

    def __init__(self, udid: str, card_hashes: list[str]) -> None:
        super().__init__()
        self.signals = BackupWorker.Signals()
        self._udid = udid
        self._hashes = list(card_hashes)

    def run(self) -> None:
        from ..core.cardskin import ensure_backup

        total = len(self._hashes)
        failed: list[str] = []
        messages: list[str] = []
        for index, card_hash in enumerate(self._hashes):
            _emit(self.signals.cardProgress, index + 1, total, card_hash)

            def note(text: str) -> None:
                _emit(self.signals.log, f"  · {text}")

            try:
                ok, message = ensure_backup(self._udid, card_hash, reporter=note)
            except Exception as error:  # noqa: BLE001
                ok, message = False, f"备份异常：{error}"
            short = f"{card_hash[:10]}…"
            first_line = message.splitlines()[0] if message else "未知原因"
            _emit(self.signals.log, f"备份 {short}：{first_line}")
            if not ok:
                failed.append(card_hash)
                messages.append(f"{short} {first_line}")
        if failed:
            summary = (f"有 {len(failed)} 张卡未能完成备份：\n"
                       + "\n".join(messages))
            _emit(self.signals.finished, False, summary, failed)
        else:
            _emit(self.signals.finished, True,
                  f"全部 {total} 张卡的原皮肤备份完成。", [])


# ---------------------------------------------------------------------------
# 密码主题解析 / 刷入
# ---------------------------------------------------------------------------

class ThemeInspectWorker(QRunnable):
    class Signals(BaseSignals):
        finished = Signal(object, str)      # info dict | None, error

    def __init__(self, path: str) -> None:
        super().__init__()
        self.signals = ThemeInspectWorker.Signals()
        self._path = path

    def run(self) -> None:
        try:
            from ..core.passthm import inspect_passthm

            info = inspect_passthm(self._path)
        except Exception as error:  # noqa: BLE001
            _emit(self.signals.finished, None, str(error))
        else:
            _emit(self.signals.finished, info, "")


class PasscodeFlashWorker(QRunnable):
    class Signals(BaseSignals):
        status = Signal(str)
        progress = Signal(float)
        finished = Signal(bool, str)

    def __init__(self, udid: str, theme_path: str, version: str,
                 language: str, bold: str) -> None:
        super().__init__()
        self.signals = PasscodeFlashWorker.Signals()
        self._udid = udid
        self._theme_path = theme_path
        self._version = version
        self._language = language
        self._bold = bold

    def run(self) -> None:
        from ..core.passthm import flash_passthm

        def handler(message: str, step: int, total: int, _extra: Any = None) -> None:
            if total > 0:
                _emit(self.signals.progress, min(step / total, 1.0))
            _emit(self.signals.status, message)
            _emit(self.signals.log, f"  {message}")

        try:
            ok = flash_passthm(
                self._udid, self._theme_path, self._version,
                self._language, self._bold,
                on_message=handler, logger=lambda text: None)
        except Exception as error:  # noqa: BLE001
            _emit(self.signals.finished, False, str(error))
            return
        if ok:
            _emit(self.signals.finished, True, "密码主题已成功应用！")
        else:
            _emit(self.signals.finished, False,
                  "部分素材写入失败，请查看日志后重试。")
