"""「苹果钱包卡片」面板 - 扫描卡片、指定皮肤、批量刷入。"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..core.imaging import SUPPORTED_IMAGE_FILTER, load_image
from ..core.skinstore import (find_auto_backup, has_auto_backup, has_manual_backup,
                              image_size, last_flash_path, latest_face_backup,
                              load_bytes)
from ..core.storage import (add_card_hash, load_card_meta, load_saved_cards,
                            save_card_meta, save_cards)
from .card_tile import CARD_HEIGHT, CARD_WIDTH, CardTileWidget
from .context import AppContext
from .flow_layout import FlowLayout
from .qt_images import preview_pixmap
from .style import BLUE_INFO, CONTROL_BG, INFO_SOFT, SEPARATOR, Type

_PREVIEW_WIDTH = CARD_WIDTH * 2
_PREVIEW_HEIGHT = CARD_HEIGHT * 2


@dataclass
class CardRecord:
    card_hash: str
    # 需求③：扫描到的卡片默认全部不勾选
    selected: bool = False
    image_path: str | None = None
    preview: QPixmap | None = None
    # 辨识用：发现顺序（1 起）与发现时刻。
    found_order: int = 0
    found_at: float = 0.0
    # 「读取卡面」写入的自动备份文件路径：既作为瓦片预览来源，
    # 也是「恢复原皮」的恢复来源（取最新一份 auto_ 备份）。
    read_path: str | None = None
    # 卡面像素尺寸 (宽, 高)，读取卡面时自动计算并显示
    read_size: tuple[int, int] | None = None
    # 卡包原生素材（strip/logo…）的本地预览路径，仅用于认卡预览，不参与恢复
    artwork_path: str | None = None
    # 需求①：最近一次「成功刷入」的新皮肤预览路径（按卡哈希持久化在备份目录）。
    # 已刷过皮肤的卡优先显示它而非原皮；从未刷过的卡此值为 None，回退显示原皮。
    last_flash_path: str | None = None


class _CardArea(QScrollArea):
    """卡片滚动区：手动同步画布宽度与流式布局高度，确保滚动条范围正确。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.canvas = QWidget(self)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Preferred)
        self.grid = FlowLayout(self.canvas, margin=20, spacing=20)
        self.setWidget(self.canvas)

    def relayout(self) -> None:
        width = max(self.viewport().width() - 2, 200)
        self.canvas.resize(width, max(self.grid.heightForWidth(width), 10))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self.relayout)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.relayout()


class WalletPanel(QWidget):
    """钱包卡片工作区（工具条 + 扫描提示条 + 卡片网格 / 空状态）。"""

    addManuallyRequested = Signal()   # noqa: N815
    stateChanged = Signal()           # noqa: N815
    scanningChanged = Signal(bool)    # noqa: N815

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._records: list[CardRecord] = []
        self._tiles: dict[str, CardTileWidget] = {}
        self._scanning = False
        self._scan_worker = None
        self._scan_verbose = False
        self._next_order = 1
        self._pending_flash_jobs: list[tuple[str, str]] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._separator())

        self.banner = self._build_banner()
        root.addWidget(self.banner)
        self.banner_separator = self._separator()
        root.addWidget(self.banner_separator)

        self.area = _CardArea(self)
        self.canvas = self.area.canvas
        self.grid = self.area.grid
        root.addWidget(self.area, 1)

        self.empty_host = self._build_empty_state()
        root.addWidget(self.empty_host, 1)

        # 每次启动重置为初始状态：不加载、不保留上次扫描检测到的卡片列表。
        self._reset_on_launch()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background: {SEPARATOR}; max-height: 1px; border: none;")
        return line

    def _build_toolbar(self) -> QWidget:
        host = QWidget(self)
        host.setFixedHeight(48)
        layout = QHBoxLayout(host)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        self.scan_button = QPushButton("扫描卡片", host)
        self.scan_button.setProperty("cta", "true")
        self.scan_button.setMinimumWidth(116)
        self.scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_button.clicked.connect(self.toggle_scanning)
        layout.addWidget(self.scan_button)

        self.add_button = QPushButton("手动添加", host)
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.addManuallyRequested.emit)
        layout.addWidget(self.add_button)

        self.bulk_button = QPushButton("批量设置皮肤…", host)
        self.bulk_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bulk_button.clicked.connect(self._pick_bulk_image)
        layout.addWidget(self.bulk_button)

        self.online_button = QPushButton("在线制作卡面", host)
        self.online_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.online_button.setToolTip(
            "在系统默认浏览器中打开在线卡面制作工具，\n"
            "设计好卡面后下载图片，再回到本软件刷入。\n"
            "https://tonychenn.cn/tools/carddesign/index.html")
        self.online_button.clicked.connect(self._open_online_designer)
        layout.addWidget(self.online_button)

        self.open_backup_button = QPushButton("打开备份目录", host)
        self.open_backup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_backup_button.setToolTip(
            "打开本机存放卡面备份的文件夹（aircard_backups）。\n"
            "自动备份：auto_<年月日时分秒>_<哈希>.png\n"
            "手动备份：<年月日时分秒>_<哈希>.png")
        self.open_backup_button.clicked.connect(self._open_backup_dir)
        layout.addWidget(self.open_backup_button)

        layout.addStretch(1)

        self._group_widgets: list[QWidget] = []

        def link(text: str, danger: bool = False) -> QPushButton:
            button = QPushButton(text, host)
            button.setProperty("link", "danger" if danger else "true")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            return button

        def dot() -> QLabel:
            label = QLabel("·", host)
            label.setObjectName("secondary")
            self._group_widgets.append(label)
            return label

        self.select_all_button = link("全选")
        self.select_all_button.clicked.connect(lambda: self._set_all_selected(True))
        layout.addWidget(self.select_all_button)

        layout.addWidget(dot())

        self.deselect_all_button = link("取消全选")
        self.deselect_all_button.clicked.connect(lambda: self._set_all_selected(False))
        layout.addWidget(self.deselect_all_button)

        layout.addWidget(dot())

        self.clear_all_button = link("清空列表", danger=True)
        self.clear_all_button.clicked.connect(self.clear_all)
        layout.addWidget(self.clear_all_button)

        self._group_widgets.extend((self.bulk_button, self.select_all_button,
                                    self.deselect_all_button, self.clear_all_button))
        return host

    def _build_banner(self) -> QWidget:
        host = QWidget(self)
        host.setStyleSheet(f"background: {INFO_SOFT};")
        layout = QHBoxLayout(host)
        layout.setContentsMargins(20, 8, 20, 8)
        layout.setSpacing(12)

        icon = QLabel("\U0001F4F1", host)
        icon.setStyleSheet(f"font-size: 20px; color: {BLUE_INFO};")
        layout.addWidget(icon)

        text_host = QWidget(host)
        column = QVBoxLayout(text_host)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        title = QLabel("实时扫描已启动", text_host)
        title.setFont(Type.caption(700))
        title.setStyleSheet(f"color: {BLUE_INFO};")
        column.addWidget(title)
        hint = QLabel("请双击侧边按钮（Apple Pay），完成 Face ID 验证后轻点你的卡片。",
                      text_host)
        hint.setFont(Type.caption2())
        hint.setObjectName("secondary")
        column.addWidget(hint)
        tip = QLabel(
            "提示：刚连上时会先<b>回放设备里缓存的历史日志</b>（可达数十万行），"
            "需要一点时间，期间界面不会卡住；卡片常被一次性发现多张、时间戳相同，属正常现象。"
            "想让<b>新</b>的卡片被识别：在 iPhone 上强制关闭「钱包」App → 重新打开并完成 "
            "Face ID → <b>重新开始一次扫描</b>（重新回放历史日志才能捞到新卡）。"
            "分不清对应关系时，点卡片上的「\U0001F50D 读取卡面」直接看真实卡面；"
            "若要保留原皮以便以后恢复，再点「\U0001F4BE 备份卡面」。",
            text_host)
        tip.setFont(Type.caption2())
        tip.setObjectName("secondary")
        tip.setWordWrap(True)
        tip.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(tip)
        layout.addWidget(text_host, 1)

        done = QPushButton("完成", host)
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.clicked.connect(self.stop_scanning)
        layout.addWidget(done)
        host.setVisible(False)
        return host

    def _build_empty_state(self) -> QWidget:
        host = QWidget(self)
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        card = QWidget(host)
        card.setMinimumWidth(520)
        column = QVBoxLayout(card)
        column.setContentsMargins(20, 30, 20, 40)
        column.setSpacing(18)

        icon = QLabel("\U0001F4B3", card)
        icon.setStyleSheet(f"font-size: 54px; color: {BLUE_INFO};")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(icon)

        title = QLabel("尚未检测到卡片", card)
        title.setFont(Type.title3_bold())
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(title)

        steps_card = QFrame(card)
        # 作用域选择器：无选择器的样式会套到每个子控件（步骤文字被逐行画框）。
        steps_card.setObjectName("stepsCard")
        steps_card.setStyleSheet(
            "QFrame#stepsCard { background: #FFFFFF; border: 1px solid #E2E2E7; "
            "border-radius: 12px; }")
        steps = QVBoxLayout(steps_card)
        steps.setContentsMargins(20, 20, 20, 20)
        steps.setSpacing(10)
        for number, text in enumerate((
                "点击上方工具条中的「扫描卡片」。",
                "在 iPhone 上<b>双击侧边按钮</b>（Apple Pay），通过 <b>Face ID</b> "
                "验证后<b>轻点你的卡片</b>。",
                "你的卡片会被立即识别出来！",
        ), 1):
            row = QHBoxLayout()
            row.setSpacing(10)
            badge = QLabel(f"{number}.", steps_card)
            badge.setFont(Type.subheadline(700))
            badge.setStyleSheet(f"color: {BLUE_INFO};")
            row.addWidget(badge)
            line = QLabel(text, steps_card)
            line.setFont(Type.subheadline())
            line.setObjectName("secondary")
            line.setWordWrap(True)
            line.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(line, 1)
            steps.addLayout(row)
        column.addWidget(steps_card)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.start_scan_button = QPushButton("开始扫描", card)
        self.start_scan_button.setProperty("cta", "true")
        self.start_scan_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_scan_button.clicked.connect(self.start_scanning)
        actions.addWidget(self.start_scan_button)
        empty_add = QPushButton("手动添加哈希", card)
        empty_add.setCursor(Qt.CursorShape.PointingHandCursor)
        empty_add.clicked.connect(self.addManuallyRequested.emit)
        actions.addWidget(empty_add)
        column.addLayout(actions)

        layout.addWidget(card)
        layout.addStretch(1)
        return host

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    @staticmethod
    def _backup_preview_path(card_hash: str) -> str | None:
        """该卡最新一份自动备份的路径（可在界面显示的图片）；无则 None。"""
        try:
            path = find_auto_backup(card_hash)
            return str(path) if path is not None and path.is_file() else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _read_preview_path(card_hash: str) -> str | None:
        """「读取卡面」预览的来源 = 最新一份自动备份；没读过则返回 None。"""
        return WalletPanel._backup_preview_path(card_hash)

    @staticmethod
    def _artwork_preview_path(card_hash: str) -> str | None:
        """卡包原图（认卡用）的本地路径；没读过则返回 None。"""
        try:
            from ..core.skinstore import artwork_path

            path = artwork_path(card_hash)
            return str(path) if path is not None and path.is_file() else None
        except Exception:  # noqa: BLE001
            return None

    def _reset_on_launch(self) -> None:
        """每次启动都把卡片列表重置为初始空状态，不加载/不保留上次扫描结果。"""
        # 清掉上次扫描落盘的卡片列表，确保「不保留」旧数据。
        try:
            from ..core.storage import clear_saved_cards

            clear_saved_cards()
        except Exception:  # noqa: BLE001
            pass
        self._records = []
        self._next_order = 1
        self.ctx.log("已重置为初始状态（未加载上次的卡片列表）。")
        self._rebuild_tiles()
        self._update_scan_ui()

    def _persist(self) -> None:
        try:
            save_cards([record.card_hash for record in self._records])
        except Exception:
            pass
        try:
            save_card_meta({
                record.card_hash: {
                    "found_at": record.found_at,
                    "found_order": record.found_order,
                }
                for record in self._records
            })
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 卡片网格
    # ------------------------------------------------------------------
    def _rebuild_tiles(self) -> None:
        while self.grid.count():
            self.grid.takeAt(0)
        for tile in self._tiles.values():
            tile.setParent(None)
            tile.deleteLater()
        self._tiles.clear()

        newest = max((r.found_at for r in self._records), default=0.0)
        for index, record in enumerate(self._records):
            tile = CardTileWidget(record.card_hash, index, self.canvas)
            tile.set_found_info(record.found_order, record.found_at,
                                is_newest=bool(record.found_at
                                               and record.found_at >= newest))
            tile.pickRequested.connect(
                lambda _hash=record.card_hash: self._pick_image(_hash))
            tile.clearRequested.connect(
                lambda _hash=record.card_hash: self.clear_skin(_hash))
            tile.deleteRequested.connect(
                lambda _hash=record.card_hash: self.delete_card(_hash))
            tile.backupRequested.connect(
                lambda _hash=record.card_hash: self.backup_card_skin(_hash))
            tile.readRequested.connect(
                lambda _hash=record.card_hash: self.read_card_face(_hash))
            tile.restoreRequested.connect(
                lambda _hash=record.card_hash: self.restore_card_skin(_hash))
            tile.restoreHistoryRequested.connect(
                lambda _hash=record.card_hash: self.restore_from_history(_hash))
            tile.imageDropped.connect(
                lambda path, _hash=record.card_hash: self.set_skin(_hash, path))
            tile.selectionChanged.connect(
                lambda checked, _r=record: self._on_selection(_r, checked))
            tile.set_selected(record.selected)
            # 扫描/刷新某卡时统一套用：备份预览 + 尺寸 + 两个恢复按钮可用态（需求①/③）
            self._apply_backup_preview(record, tile)
            self.grid.addWidget(tile)
            # 必须显式 show()：新建子控件默认处于 hidden 状态，
            # FlowLayout 计算 heightForWidth 时会跳过隐藏项，导致画布高度塌陷为 0。
            tile.show()
            self._tiles[record.card_hash] = tile

        self.area.relayout()
        self._apply_page_state()

    def _apply_page_state(self) -> None:
        empty = not self._records
        self.empty_host.setVisible(empty)
        self.area.setVisible(not empty)
        for widget in self._group_widgets:
            widget.setVisible(not empty)

    def _record(self, card_hash: str) -> CardRecord | None:
        for record in self._records:
            if record.card_hash == card_hash:
                return record
        return None

    def add_card(self, card_hash: str, persist: bool = True) -> bool:
        if self._record(card_hash) is not None:
            return False
        record = CardRecord(card_hash=card_hash)
        record.found_order = self._next_order
        record.found_at = time.time()
        self._next_order += 1
        self._records.append(record)
        self._rebuild_tiles()
        if persist:
            self._persist()
        return True

    def add_hashes(self, raw: str) -> int:
        try:
            candidates = add_card_hash(raw, [r.card_hash for r in self._records])
        except Exception:
            candidates = []
        count = 0
        for value in candidates:
            if self.add_card(value, persist=False):
                count += 1
                self.ctx.log(f"已添加卡片：{value}")
        if count:
            self._persist()
        return count

    def delete_card(self, card_hash: str) -> None:
        record = self._record(card_hash)
        if record is None:
            return
        self._records = [item for item in self._records
                         if item.card_hash != card_hash]
        tile = self._tiles.pop(card_hash, None)
        if tile is not None:
            tile.setParent(None)
            tile.deleteLater()
        self._refresh_indices()
        self._persist()
        self.ctx.log(f"已移除卡片：{card_hash}")
        self._apply_page_state()
        self.stateChanged.emit()

    def clear_all(self) -> None:
        if not self._records:
            return
        self._records.clear()
        self._rebuild_tiles()
        self._persist()
        self.ctx.log("已清空全部卡片。")
        self.stateChanged.emit()

    def _refresh_indices(self) -> None:
        for index, record in enumerate(self._records):
            tile = self._tiles.get(record.card_hash)
            if tile is not None:
                tile.set_index(index)

    def _on_selection(self, record: CardRecord, checked: bool) -> None:
        record.selected = checked
        self.stateChanged.emit()

    def _set_all_selected(self, value: bool) -> None:
        for record in self._records:
            record.selected = value
            tile = self._tiles.get(record.card_hash)
            if tile is not None:
                tile.set_selected(value)
        self.stateChanged.emit()

    # ------------------------------------------------------------------
    # 皮肤
    # ------------------------------------------------------------------
    def set_skin(self, card_hash: str, path: str) -> None:
        record = self._record(card_hash)
        if record is None:
            return
        if load_image(path) is None:
            self.ctx.error("无法读取该图片，请选择常见格式（PNG / JPEG / HEIC / WebP）。")
            return
        preview = preview_pixmap(path, _PREVIEW_WIDTH, _PREVIEW_HEIGHT)
        if preview is None:
            self.ctx.error("无法读取该图片，请选择常见格式（PNG / JPEG / HEIC / WebP）。")
            return
        record.image_path = path
        record.preview = preview
        record.selected = True
        tile = self._tiles.get(card_hash)
        if tile is not None:
            tile.set_skin(preview)
            tile.set_selected(True)
        self.ctx.log(f"已为卡片 {card_hash[:12]}… 指定皮肤。")
        self.stateChanged.emit()

    def clear_skin(self, card_hash: str) -> None:
        record = self._record(card_hash)
        if record is None:
            return
        record.image_path = None
        record.preview = None
        tile = self._tiles.get(card_hash)
        if tile is not None:
            tile.set_skin(None)
        self.ctx.log(f"已清除 {card_hash[:12]}… 的皮肤。")
        self.stateChanged.emit()

    def _pick_image(self, card_hash: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择卡片皮肤（{card_hash[:12]}…）", "", SUPPORTED_IMAGE_FILTER)
        if path:
            self.set_skin(card_hash, path)

    def _pick_bulk_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "为所有选中卡片指定同一张皮肤", "", SUPPORTED_IMAGE_FILTER)
        if not path:
            return
        targets = [record.card_hash for record in self._records if record.selected]
        if not targets:
            self.ctx.error("请先勾选至少一张卡片。")
            return
        for card_hash in targets:
            self.set_skin(card_hash, path)

    def _open_online_designer(self) -> None:
        """调用系统默认浏览器打开在线卡面制作工具。"""
        url = QUrl("https://tonychenn.cn/tools/carddesign/index.html")
        if not QDesktopServices.openUrl(url):
            self.ctx.error(
                "无法打开系统默认浏览器，请手动访问：\n"
                "https://tonychenn.cn/tools/carddesign/index.html")

    def _open_backup_dir(self) -> None:
        """需求④：直接打开本机备份文件夹 aircard_backups（不存在则自动创建）。"""
        from ..core import skinstore

        try:
            root = skinstore.ensure_root()
        except Exception as error:  # noqa: BLE001
            self.ctx.error(f"无法创建备份目录：{error}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))):
            self.ctx.error(f"无法打开备份目录：\n{root}")

    # ------------------------------------------------------------------
    # 实时扫描
    # ------------------------------------------------------------------
    @property
    def scanning(self) -> bool:
        return self._scanning

    def toggle_scanning(self) -> None:
        self.stop_scanning() if self._scanning else self.start_scanning()

    def set_scan_verbose(self, verbose: bool) -> None:
        """日志抽屉上的"显示原始日志"开关，实时作用到正在运行的扫描线程。"""
        self._scan_verbose = bool(verbose)
        worker = self._scan_worker
        if worker is not None:
            try:
                worker.verbose = self._scan_verbose
            except Exception:
                pass

    def start_scanning(self) -> None:
        if self._scanning:
            return
        udid = self.ctx.udid()
        if not udid:
            self.ctx.error("未检测到 iPhone，请通过 USB 连接后再扫描。")
            return
        from .workers import DeviceScanWorker, submit

        worker = DeviceScanWorker(udid,
                                  {record.card_hash for record in self._records})
        worker.verbose = self._scan_verbose
        worker.signals.log.connect(self.ctx.log)
        worker.signals.cardFound.connect(self._on_card_found)
        worker.signals.finished.connect(self._on_scan_finished)
        self._scan_worker = worker
        self._scanning = True
        submit(worker)
        self._update_scan_ui()
        self.ctx.status("请双击侧边按钮，通过 Face ID 后轻点卡片…")
        self.ctx.log("已开始监听设备日志，正在等待钱包卡片…")
        self.ctx.log("  首次连接会先回放设备内存中缓存的历史日志（可能几十万行），"
                     "需要一点时间；卡片哈希仍在实时分析，不会漏。")
        self.ctx.log("  最直观的认卡方式：卡片出现后点它上面的「\U0001F50D 读取卡面」，"
                     "直接显示它在 iPhone 上的真实卡面（只读，不改原皮）。")

    def stop_scanning(self) -> None:
        if not self._scanning:
            return
        worker = self._scan_worker
        if worker is not None:
            try:
                worker.request_stop()
            except Exception:
                pass
        self._finish_scan("")
        self._persist()
        self.ctx.log(f"扫描已停止，当前共 {len(self._records)} 张卡片。")

    def _finish_scan(self, reason: str) -> None:
        self._scanning = False
        self._scan_worker = None
        self._update_scan_ui()
        if reason:
            self.ctx.log(reason)
        if "双击侧边按钮" in getattr(self.ctx, "last_status", ""):
            self.ctx.status("就绪")

    def _update_scan_ui(self) -> None:
        running = self._scanning
        self.scan_button.setText("停止扫描" if running else "扫描卡片")
        self.scan_button.setProperty("cta", "danger" if running else "true")
        for widget in (self.scan_button,):
            style = widget.style()
            if style is not None:
                style.polish(widget)
            widget.update()
        enabled = running or self.ctx.connected()
        self.scan_button.setEnabled(enabled)
        self.start_scan_button.setEnabled(enabled)
        self.banner.setVisible(running)
        self.banner_separator.setVisible(running)
        self.scanningChanged.emit(running)

    def _on_card_found(self, card_hash: str) -> None:
        if self.add_card(card_hash):
            record = self._record(card_hash)
            order = record.found_order if record else len(self._records)
            stamp = time.strftime("%H:%M:%S", time.localtime(
                record.found_at if record else time.time()))
            self.ctx.log(f"发现卡片 #{order}（{stamp}）：{card_hash}")
            self.ctx.log("  提示：不确定对应哪张卡时，点它上面的「\U0001F50D 读取卡面」，"
                         "直接显示它在 iPhone 上的真实卡面（只读预览，不改原皮）。")
            self._persist()

    def _on_scan_finished(self, reason: str) -> None:
        was_running = self._scanning
        self._scanning = False
        self._scan_worker = None
        self._update_scan_ui()
        if reason:
            self.ctx.log(reason)
        if was_running:
            self.ctx.status("卡片扫描已结束，需要识别新卡片时重新开始一次扫描即可。")
            self.ctx.log(f"日志监听已退出，当前共 {len(self._records)} 张卡片。")
            self.ctx.log("  如果换卡后想识别新的卡片：先在 iPhone 上强制关闭「钱包」App "
                         "再重新打开并完成 Face ID，然后重新开始扫描。")
            self._persist()

    # ------------------------------------------------------------------
    # 对外查询 / 刷入
    # ------------------------------------------------------------------
    @property
    def cards(self) -> list[CardRecord]:
        return list(self._records)

    def selected_count(self) -> int:
        return sum(1 for record in self._records if record.selected)

    def ready_jobs(self) -> list[tuple[str, str]]:
        return [(record.card_hash, record.image_path)
                for record in self._records
                if record.selected and record.image_path]

    def ready_count(self) -> int:
        return len(self.ready_jobs())

    def selection_summary(self) -> str:
        if not self._records:
            return ""
        return (f"已选中 {self.selected_count()}/{len(self._records)} 张卡片 · "
                f"{self.ready_count()} 张待刷入")

    # ------------------------------------------------------------------
    # 原卡面：备份 / 恢复
    # ------------------------------------------------------------------
    def backup_card_skin(self, card_hash: str) -> None:
        """把 iPhone 上该卡当前的卡面读取并备份到本机（用户主动触发）。"""
        if self._record(card_hash) is None:
            return
        if not self.ctx.udid():
            self.ctx.error("未检测到 iPhone，请通过 USB 连接。")
            return
        self._ensure_scan_stopped()

        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, "备份卡面",
            "即将从 iPhone 读取这张卡的当前卡面，并备份到本机。\n\n"
            "底层走 airlift 通道：卡面文件会被临时移动后立即写回原位，\n"
            "正常情况下手机上看不出任何变化；万一写回失败，卡面会暂时显示为空，\n"
            "届时点「恢复原皮」即可用本机备份修复。\n\n"
            "提示：如果这张卡从未刷过皮肤，设备上没有合并卡面文件，\n"
            "备份会失败 —— 届时会提示你改用「卡包原图」来认卡。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_skin_job(card_hash, "backup")

    def restore_card_skin(self, card_hash: str) -> None:
        """把本机最新一份自动备份的卡面写回 iPhone。

        严格按需求：只有存在自动备份（auto_ 文件）时按钮才可用、才可恢复。
        """
        record = self._record(card_hash)
        if record is None:
            return
        if not has_auto_backup(card_hash):
            self.ctx.error(
                "本机还没有这张卡的自动备份。\n\n"
                "请先点「\U0001F50D 读取卡面」或「\U0001F4BE 备份卡面」生成备份，"
                "再点「恢复原皮」。")
            return
        if not self.ctx.udid():
            self.ctx.error("未检测到 iPhone，请通过 USB 连接。")
            return
        self._ensure_scan_stopped()
        self._run_skin_job(card_hash, "restore")

    def read_card_face(self, card_hash: str) -> None:
        """读取这张卡在 iPhone 上“当前”的卡面，显示在瓦片上用于辨认。

        同时会：① 写入一份带时间戳的自动备份（auto_<时间戳>_<哈希>.png），
        使「恢复原皮」按钮启用；② 自动计算卡面像素尺寸并显示在卡片下方。
        自动备份与手动「备份卡面」（<时间戳>_<哈希>.png）各存各的文件，
        互不冲突。同样走 airlift 的“移动 → 读回 → 写回”流程，风险与备份相同。
        """
        if self._record(card_hash) is None:
            return
        if not self.ctx.udid():
            self.ctx.error("未检测到 iPhone，请通过 USB 连接。")
            return
        self._ensure_scan_stopped()
        self._run_skin_job(card_hash, "readface")

    def restore_from_history(self, card_hash: str) -> None:
        """手动浏览并挑选一份手动备份卡面 PNG 写回这张卡。"""
        record = self._record(card_hash)
        if record is None:
            return
        if not self.ctx.udid():
            self.ctx.error("未检测到 iPhone，请通过 USB 连接。")
            return
        from PySide6.QtWidgets import QFileDialog

        from ..core import skinstore

        default_dir = str(skinstore.ensure_root())
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择要恢复的备份卡面（{card_hash[:12]}…）", default_dir,
            "PNG 图片 (*.png);;所有文件 (*.*)")
        if not path:
            return
        self._ensure_scan_stopped()
        self._run_skin_job(card_hash, "restore_file", file_path=path)

    def _run_skin_job(self, card_hash: str, mode: str,
                     file_path: str | None = None) -> None:
        from .workers import CardSkinWorker, submit

        udid = self.ctx.udid()
        # 记录操作类型：读取/备份/恢复卡面属于"卡面预览"类操作，不应自动展开日志栏。
        self.ctx.set_op(mode)
        self.ctx.set_busy(True)
        self.ctx.progress(0.0)
        worker = CardSkinWorker(udid, card_hash, mode, file_path=file_path)
        worker.signals.log.connect(self.ctx.log)
        worker.signals.status.connect(self.ctx.status)
        worker.signals.progress.connect(self.ctx.progress)
        worker.signals.finished.connect(self._on_skin_job_finished)
        submit(worker)
        self.ctx.log({
            "restore": "正在把备份的原皮肤写回设备…",
            "restore_file": "正在把所选的历史卡面写回设备…",
            "native": "正在读取卡包内的原图素材…",
            "readface": "正在读取设备当前卡面用于辨认…",
            "backup": "正在读取设备卡面并备份到本地…",
        }.get(mode, "正在读取设备卡面并备份到本地…"))
        self.stateChanged.emit()

    def _on_skin_job_finished(self, ok: bool, message: str,
                              card_hash: str, mode: str,
                              info: dict | None = None) -> None:
        """任何卡面任务（读取 / 备份 / 恢复）结束后的统一收口。

        info: 额外信息字典，readface 成功时携带 {"size": (宽, 高)}。
        """
        info = info or {}
        self.ctx.set_busy(False)
        self.ctx.set_op("")
        self.ctx.progress(1.0 if ok else 0.0)
        record = self._record(card_hash)
        tile = self._tiles.get(card_hash)
        if ok and mode == "backup":
            self.ctx.status("已备份原卡面。")
            self.ctx.success(
                "已从 iPhone 读取这张卡的真实卡面，并保存为一份手动备份。\n\n"
                "卡片上现在显示的就是它原本的样子 —— 看到什么卡面就是哪张卡。\n"
                "可在「从历史恢复」里挑选任意一次手动备份。", "备份卡面成功")
        elif ok and mode == "readface":
            if record is not None:
                # 读取即写入自动备份文件，用它作为预览来源（取最新一份真实卡面备份）
                face_path, _ = latest_face_backup(card_hash)
                record.read_path = face_path
                if (tile is not None and record.read_path
                        and record.preview is None):
                    preview = preview_pixmap(record.read_path,
                                             _PREVIEW_WIDTH, _PREVIEW_HEIGHT)
                    if preview is not None:
                        tile.set_skin(preview, ready=False)
                # 需求⑤：展示卡面尺寸
                record.read_size = info.get("size") or None
                if tile is not None:
                    tile.set_size(record.read_size)
            self.ctx.status("已读取当前卡面。")
            size_text = ""
            if record is not None and record.read_size:
                size_text = (f"\n\n卡面尺寸：{record.read_size[0]} × "
                             f"{record.read_size[1]} px（已显示在卡片下方）。")
            self.ctx.success(
                "已读取这张卡当前在 iPhone 上的卡面并显示出来，"
                "可据此辨认它是哪张卡。" + size_text + "\n\n"
                "同时已写入一份自动备份到本机备份目录，"
                "「恢复原皮」按钮现已启用。",
                "读取卡面成功")
        elif ok and mode == "restore_file":
            self.ctx.status("已从所选历史卡面恢复。")
            self.ctx.success(f"{message}\n\n请强制关闭「钱包」App 后重新打开查看。",
                             "历史卡面恢复完成")
        elif ok and mode == "native":
            if record is not None:
                record.artwork_path = self._artwork_preview_path(card_hash)
                if (tile is not None and record.artwork_path
                        and record.preview is None):
                    artwork = preview_pixmap(record.artwork_path,
                                             _PREVIEW_WIDTH, _PREVIEW_HEIGHT)
                    if artwork is not None:
                        tile.set_skin(artwork, ready=False)
            self.ctx.status("已读取卡包原图。")
            leaf = ""
            try:
                from ..core.skinstore import artwork_leaf

                leaf = artwork_leaf(card_hash)
            except Exception:  # noqa: BLE001
                pass
            self.ctx.success(
                f"{message}\n\n卡片上现在显示的是卡包里的原生素材"
                + (f"（{leaf}）" if leaf else "")
                + "，据此即可认出是哪张卡。\n\n"
                  "注意：这不是合并卡面文件，因此不能用于「恢复原皮」。",
                "读取卡包原图成功")
        elif ok:
            self.ctx.status("已恢复原皮肤。")
            self.ctx.success(f"{message}\n\n请强制关闭「钱包」App 后重新打开查看。",
                             "恢复原皮肤完成")
        else:
            self.ctx.status("操作失败。")
            self.ctx.error(f"{message}\n\n请查看日志后重试。")
            # 只有在"同步通道看起来是通的、只是这张卡没有合并卡面文件"的情况下
            # 才提议改读卡包原图。若失败签名是"未收到 SyncAllowed"，说明整条
            # airlift 通道没打通，再试 14 个候选也只是白白浪费几十秒。
            if mode in ("backup", "readface"):
                try:
                    from ..core.cardskin import looks_like_missing

                    offer = not looks_like_missing(message)
                except Exception:  # noqa: BLE001
                    offer = False
                if offer:
                    self._offer_native_artwork(card_hash)
                else:
                    self.ctx.log(
                        "airlift 同步通道未打通，已跳过「读取卡包原图」"
                        "（换卡面、读卡面、写主题都走这条通道，需先修好它）。")
        # 任何任务结束后都按本机备份目录重新计算两张恢复按钮的可用状态
        self._refresh_backup_states()
        self.stateChanged.emit()

    def _offer_native_artwork(self, card_hash: str) -> None:
        """合并卡面读不到时，询问是否改读卡包原图来认出这张卡。"""
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.question(
            self, "改用卡包原图？",
            "这张卡似乎还没刷过皮肤，因此设备上没有合并卡面文件。\n\n"
            "可以改为读取卡包自带的原图（strip / logo 等素材）来认出它 —— "
            "这些素材任何卡都有。\n\n"
            "注意：这同样要走一次 airlift 的「移动 → 读回 → 写回」流程，"
            "风险与「备份卡面」相同；\n读到后只用于界面显示，不会当作原皮肤备份。\n\n"
            "是否尝试？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._ensure_scan_stopped()
            self._run_skin_job(card_hash, "native")

    def _apply_backup_preview(self, record: CardRecord, tile: "CardTileWidget") -> None:
        """扫描到 / 刷新某卡时统一刷新该卡瓦片的「卡面预览 / 尺寸 / 恢复按钮」。

        需求①：若本机已有真实卡面备份（自动或手动，取时间最新者），直接在卡片的
        「更换皮肤」处显示该卡面预览，并标注其像素尺寸。
        需求③：native_ 卡包原图被视同「手动备份」，因此「从历史恢复」按钮也会随之
        点亮（native_ 文件不参与「恢复原皮」，那需要真实合并卡面）。
        需求①(v1.9.3)：若某卡曾「成功刷入」过新皮肤，本机备份目录里会留存其最近
        一次刷入的新皮肤（lastflash_<哈希>.png）。该卡无论何时被再次检测到，都应
        在预览里显示**最近一次刷入的新皮肤**，而非原皮；只有从未刷过皮肤的卡才
        回退显示原皮（真实卡面备份 / 卡包原图 / 空）。

        预览优先级：用户指定待刷入的新皮肤 > 最近一次刷入的新皮肤(lastflash) >
        最新真实卡面备份（自动/手动） > 最新一次卡包原图备份（native_）。
        尺寸标签只在展示「备份卡面 / 卡包原图」时显示；显示待刷入或已刷入的新皮肤时不标。
        """
        # 最近一次成功刷入的新皮肤（按卡哈希持久化在本机备份目录）
        last_flash = record.last_flash_path or last_flash_path(record.card_hash)
        record.last_flash_path = last_flash

        # 最新一份真实卡面备份（自动或手动，取时间最新）；native_ 不计入。
        face_path, face_size = latest_face_backup(record.card_hash)
        record.read_path = face_path
        record.read_size = face_size

        if record.preview is not None:
            # 用户已指定待刷入的新皮肤：直接显示，不覆盖、不标备份尺寸
            tile.set_skin(record.preview, ready=True)
            tile.set_size(None)
        elif last_flash:
            # 需求①(v1.9.3)：该卡刷过皮肤，且这是最近一次刷入的新皮肤，
            # 直接显示它（不是原皮），不标尺寸。
            preview = preview_pixmap(last_flash, _PREVIEW_WIDTH, _PREVIEW_HEIGHT)
            if preview is not None:
                tile.set_skin(preview, ready=False)
            tile.set_size(None)
        elif record.read_path:
            preview = preview_pixmap(record.read_path,
                                     _PREVIEW_WIDTH, _PREVIEW_HEIGHT)
            if preview is not None:
                tile.set_skin(preview, ready=False)
            tile.set_size(record.read_size)
        elif record.artwork_path or self._artwork_preview_path(record.card_hash):
            # 需求②(v1.9.1)：native_ 卡包原图备份记录同样要在卡片上识别展示 ——
            # 不仅限本次会话读到的（record.artwork_path），也包含本机备份目录里
            # 已有的 native_<哈希>_* 文件（重启软件 / 重新扫描后依然可见）。
            artwork_path = record.artwork_path or self._artwork_preview_path(
                record.card_hash)
            record.artwork_path = artwork_path
            artwork = preview_pixmap(artwork_path,
                                     _PREVIEW_WIDTH, _PREVIEW_HEIGHT)
            if artwork is not None:
                tile.set_skin(artwork, ready=False)
            data = load_bytes(artwork_path)
            tile.set_size(image_size(data) if data else None)
        else:
            tile.set_skin(None)
            tile.set_size(None)

        # 按钮可用状态：自动备份 → 恢复原皮；手动/卡包原图备份 → 从历史恢复
        tile.set_backup_states(has_auto_backup(record.card_hash),
                               has_manual_backup(record.card_hash))

    def _refresh_backup_states(self) -> None:
        """需求②/③：重新扫描 aircard_backups，按哈希匹配刷新每张卡的预览与按钮态。

        存在自动备份（auto_<时间戳>_<哈希>.png）→「恢复原皮」启用；
        存在手动备份（<时间戳>_<哈希>.png）或卡包原图（native_<哈希>_*）→「从历史恢复」启用；
        均无匹配备份                                 → 两个按钮置灰禁用。
        """
        for record in self._records:
            tile = self._tiles.get(record.card_hash)
            if tile is None:
                continue
            self._apply_backup_preview(record, tile)

    def on_device_changed(self) -> None:
        self._update_scan_ui()
        self.stateChanged.emit()

    def flash_button_label(self) -> str:
        if self.ctx.busy:
            # 与左侧状态条保持一致：根据当前操作类型显示对应文案，
            # 而不是一律显示"正在刷入卡片…"（读取/备份时那样会误导用户）。
            return {
                "readface": "正在读取卡面…",
                "backup": "正在备份卡面…",
                "backup_pre": "正在备份原卡面…",
                "restore": "正在恢复原皮…",
                "restore_file": "正在恢复卡面…",
                "native": "正在读取原图…",
                "flash": "正在刷入皮肤…",
            }.get(self.ctx.op, "正在处理…")
        count = self.ready_count()
        return f"刷入皮肤（{count} 张卡）" if count else "刷入皮肤"

    def _ensure_scan_stopped(self) -> None:
        """任何读/写设备的操作前都必须先停掉日志扫描。

        实测：扫描全速运行时（≈4800 行/秒）设备同步会被拖垮，卡面写入直接失败。
        airlift 的 Books 快照与 ATC 同步需要独占设备通道，因此这里强制先停扫描。
        """
        if not self._scanning:
            return
        self.ctx.log("正在操作设备：已自动停止日志扫描（避免与设备同步争用 USB 通道）。")
        self.stop_scanning()

    def flash_enabled(self) -> bool:
        return (not self.ctx.busy) and self.ctx.connected() and self.ready_count() > 0

    # ------------------------------------------------------------------
    # 刷入（刷入前询问是否先备份未备份的卡）
    # ------------------------------------------------------------------
    def start_flash(self) -> None:
        jobs = self.ready_jobs()
        if not jobs:
            self.ctx.error("请先为至少一张选中的卡片指定皮肤。")
            return
        udid = self.ctx.udid()
        if not udid:
            self.ctx.error("未检测到 iPhone，请通过 USB 连接。")
            return
        self._ensure_scan_stopped()

        # 刷入前检查：还有哪些选中卡片没有原皮肤备份 → 先询问用户是否备份。
        # （已备份的卡不弹窗，直接进入刷入。）
        missing = [card_hash for card_hash, _path in jobs
                   if not self._has_backup(card_hash)]
        if missing and not self._confirm_backup_before_flash(udid, jobs, missing):
            return
        self._flash_jobs(udid, jobs)

    @staticmethod
    def _has_backup(card_hash: str) -> bool:
        """本机是否已存在该卡的备份（自动或手动任一即可）。

        用于「刷入前提醒备份」的判断：只要有任意一份备份就不必再自动备份。
        注意这与恢复按钮的判定不同 —— 按钮是严格分开的（auto→恢复原皮，
        manual→从历史恢复）。
        """
        try:
            return has_auto_backup(card_hash) or has_manual_backup(card_hash)
        except Exception:  # noqa: BLE001
            return False

    def _confirm_backup_before_flash(self, udid: str,
                                     jobs: list[tuple[str, str]],
                                     missing: list[str]) -> bool:
        """告知未备份并询问是否先备份。

        返回 True  → 跳过备份，直接刷入；
        返回 False → 备份流程已启动（刷入延后到备份完成后）或用户中止。
        """
        from PySide6.QtWidgets import QMessageBox

        names = "\n".join(f"· 卡片 {value[:12]}…" for value in missing[:8])
        if len(missing) > 8:
            names += f"\n· …等共 {len(missing)} 张"
        answer = QMessageBox.question(
            self, "刷入前备份提醒",
            "以下选中的卡片还没有备份原卡面：\n\n" + names + "\n\n"
            "刷入新皮肤会覆盖现有卡面，没有备份就无法再恢复到现在的样子。\n\n"
            "是否先备份这些卡？（备份成功后会自动继续刷入）\n"
            "选择「No」则跳过备份，直接刷入。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return True
        from .workers import BackupWorker, submit

        self._pending_flash_jobs = jobs
        self.ctx.set_op("backup_pre")  # 刷入前的自动备份，同样不自动展开日志栏
        self.ctx.set_busy(True)
        self.ctx.progress(0.0)
        worker = BackupWorker(udid, missing)
        worker.signals.log.connect(self.ctx.log)
        worker.signals.cardProgress.connect(self._on_backup_progress)
        worker.signals.finished.connect(self._on_pre_flash_backup_finished)
        submit(worker)
        self.ctx.status("正在备份原卡面…")
        self.ctx.log(f"刷入前备份：正在为 {len(missing)} 张卡备份原卡面…")
        self.stateChanged.emit()
        return False

    def _on_backup_progress(self, done: int, total: int, card_hash: str) -> None:
        self.ctx.status(f"正在备份原卡面（{done}/{total}）：{card_hash[:12]}…")
        self.ctx.progress(done / max(1, total))

    def _on_pre_flash_backup_finished(self, ok: bool, message: str,
                                      failed: list) -> None:
        self.ctx.set_busy(False)
        self.ctx.set_op("")
        self.ctx.progress(1.0 if ok else 0.0)
        self._refresh_backup_states()
        jobs = self._pending_flash_jobs or []
        self._pending_flash_jobs = None
        if ok:
            self.ctx.log(f"刷入前备份完成：{message}")
            self._flash_jobs(self.ctx.udid(), jobs)
            return
        # 有备份失败的卡 → 明确反馈，并让用户决定是否仍要刷入。
        from PySide6.QtWidgets import QMessageBox

        self.ctx.error(f"备份未全部完成。\n\n{message}")
        answer = QMessageBox.question(
            self, "备份未全部完成",
            message + "\n\n"
            "说明：从未刷过皮肤的卡本来就没有合并卡面文件，备份失败属正常现象；\n"
            "但若失败原因是「同步许可」，请先点右上角 ⚙ 打开「连接诊断」排查。\n\n"
            "没有备份的卡刷入后无法恢复原样。是否仍然继续刷入？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._flash_jobs(self.ctx.udid(), jobs)
        else:
            self.ctx.status("已取消刷入。")
            self.ctx.log("用户在备份失败后取消了本次刷入。")

    def _flash_jobs(self, udid: str, jobs: list[tuple[str, str]]) -> None:
        from .workers import CardFlashWorker, submit

        self.ctx.set_op("flash")
        self.ctx.set_busy(True)
        self.ctx.progress(0.0)
        worker = CardFlashWorker(udid, jobs, auto_backup=False)
        worker.signals.log.connect(self.ctx.log)
        worker.signals.status.connect(self.ctx.status)
        worker.signals.progress.connect(self.ctx.progress)
        worker.signals.finished.connect(self._on_flash_finished)
        submit(worker)
        self.stateChanged.emit()

    def _on_flash_finished(self, ok: bool, message: str) -> None:
        self.ctx.set_busy(False)
        self.ctx.set_op("")
        self.ctx.progress(1.0 if ok else 0.0)
        self._refresh_backup_states()
        if ok:
            self.ctx.status("完成！所有卡片已更新。")
            self.ctx.success("皮肤已成功应用到所有选中卡片！\n\n"
                             "请在 iPhone 上强制关闭「钱包」App（或重启设备）以查看新卡面。")
        else:
            self.ctx.status("卡片皮肤刷入失败。")
            self.ctx.error(f"{message}\n\n请查看日志后重试。")
        self.stateChanged.emit()
