"""读取 / 备份 / 恢复 iPhone 上的原始卡面（皮肤）。

原版 macOS 的 AirCard 只能"写"卡面，不能"读"卡面，也没有备份与恢复。
本模块基于 airlift 的 `read_file` 原语补齐这三个能力：

1. 「备份卡面」——把设备上的 `cardBackgroundCombined@3x.png` 读回本地，
   界面上直接显示这张卡**真实的卡面**（看到什么卡面就是哪张卡）；
2. 「备份原皮肤」——读取即备份，存进 `~/.aircard_backups/`；
   在首次刷入自定义皮肤前会提醒用户，保证随时能退回出厂卡面。
3. 「恢复原皮肤」——把备份写回设备并清掉钱包渲染缓存。

风险说明（务必了解）：
`read_file` 走的是 airlift 的**移动语义** —— 设备会把目标文件从
`…/<哈希>.pkpass/` 移动到 `Media/recovered/…`，本模块用 AFC 读回后
**立即写回原位**。理论上存在写回失败的可能，此时该卡片会暂时显示为空卡面，
而文件仍留在 `Media/recovered/`；遇到这种情况直接在界面上点「恢复原皮」
（我们手里已有本地备份字节）即可修复。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from .airlift import (channel_dead, invalidate_cache, read_file, report,
                      write_file, write_files_batch)
from .card_assets import build_card_assets
from .skinstore import (clear_last_flash, find_auto_backup, has_auto_backup,
                        load_bytes, save_auto_backup, save_last_flash,
                        save_manual_backup)

PKPASS_ROOT = "/var/mobile/Library/Passes/Cards"
SOURCE_LEAF = "cardBackgroundCombined@3x.png"
PREVIEW_SIZE = (580, 366)


def pkpass_dir(card_hash: str) -> str:
    return f"{PKPASS_ROOT}/{card_hash}.pkpass"


def build_preview(png_bytes: bytes) -> bytes | None:
    """生成界面用的缩略图；原图通常 1536×969，直接交给 Qt 重绘太重。"""
    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            image.load()
            fitted = ImageOps.fit(image.convert("RGBA"), PREVIEW_SIZE,
                                  method=Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            fitted.save(buffer, format="PNG")
            return buffer.getvalue()
    except Exception:  # noqa: BLE001
        return None


def image_size(png_bytes: bytes) -> tuple[int, int] | None:
    """返回卡面像素尺寸 (宽, 高)；失败返回 None。用于界面展示参考。"""
    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            return image.size
    except Exception:  # noqa: BLE001
        return None


# ⚠️ 重要教训（真机实测，勿重犯）：
# 曾经假设"退出码 3 + 目标校验未通过 + 未收到 SyncAllowed"是**文件不存在**的
# 特征。这是错的 —— 设备上确定存在的 `logo@2x.png`（passd 日志给出了完整路径）
# 读取时报的错与不存在的文件**完全一致**。
# 真实含义：这是 **airlift 同步通道没打通**（设备未授予 SyncAllowed）的通用签名，
# 与文件是否存在无关。因此绝不能仅凭这个签名断言"文件不存在"。
_MISSING_HINTS = ("未收到 SyncAllowed", "目标校验未通过", "退出码 3")

# 卡包自带的原生素材。无论这张卡刷没刷过皮肤，它们都在 pkpass 里
# （真机日志已证：passd 能查到 `…/<hash>.pkpass/strip@2x.png`、`logo@2x.png`）。
# 按"最像卡面"的顺序排列，读到第一个存在的就收工。
NATIVE_ARTWORK_CANDIDATES = (
    "strip@3x.png", "strip@2x.png", "strip.png",
    "logo@3x.png", "logo@2x.png", "logo.png",
    "thumbnail@3x.png", "thumbnail@2x.png", "thumbnail.png",
    "background@3x.png", "background@2x.png", "background.png",
    "footer@2x.png", "icon@2x.png",
)


def looks_like_missing(notes: str) -> bool:
    """诊断信息是否呈现"同步未成功"的通用签名。

    注意：这**不代表**文件不存在 —— 实测中确实存在的文件也报同样的错。
    它只能说明 airlift 的 AirTraffic 同步这一步没成功。
    """
    return sum(1 for hint in _MISSING_HINTS if hint in notes) >= 2


def explain_read_failure(notes: str, known_missing: bool = False) -> str:
    """把 read_file 的失败翻译成用户能看懂、且**不会误判**的原因。"""
    head = ("读取失败：设备没有授予同步许可，airlift 通道未打通。\n\n"
            "这不是卡片的问题 —— 换卡面、读卡面、写主题全都走这条通道，\n"
            "现在它们都会以同样的方式失败。")
    causes = ("\n\n常见原因（按可能性排序）：\n"
              "  1. 这台电脑从未用 iTunes / Apple Devices 与这台 iPhone 同步过；\n"
              "  2. iPhone 已锁屏，或没有点「信任此电脑」；\n"
              "  3. Windows 上的 Apple Mobile Device Support 版本过旧；\n"
              "  4. 设备装有 MDM / 描述文件，限制了同步。\n\n"
              "建议：先在 iTunes（或 Apple Devices）里手动同步一次，"
              "确保能正常识别并备份，再回到本软件重试。\n"
              "若以上都已排除，请点右上角 ⚙ 打开「连接诊断」，"
              "把完整报告复制出来反馈 —— 报告里附了 Apple 组件自己写的日志。")
    if known_missing:
        tail = ("\n\n另：日志显示这张卡没有合并卡面文件（从未刷过皮肤），\n"
                "即使通道正常也读不到 —— 这张卡没有可备份的原卡面。")
    else:
        tail = ("\n\n补充：从未刷过皮肤的卡本身就没有合并卡面文件\n"
                "（`cardBackgroundCombined@3x.png` 是刷入时才生成的），\n"
                "那种情况即使通道正常也会读不到，属正常现象。")
    detail = f"\n\n诊断信息：\n{notes}" if notes else ""
    return head + causes + tail + detail


def read_native_artwork(udid: str, card_hash: str,
                        reporter: Callable[[str], None] | None = None
                        ) -> tuple[str, bytes] | None:
    """读取卡包内的原生图片素材，用于认出这张是哪张卡。

    与 read_original_skin 的区别：
    · read_original_skin 读的是"当前卡面"，只有刷过皮肤的卡才有；
    · 本函数读的是 pkpass 自带的 strip / logo 等，任何卡都有。

    返回 (文件名, 字节内容)；全部候选都不存在则返回 None。
    风险与 read_original_skin 相同（airlift 移动语义），但只用于显示预览，
    **不会**写进备份仓库——恢复原皮时写回的仍是 cardBackgroundCombined。
    """
    directory = pkpass_dir(card_hash)
    tried = 0
    for leaf in NATIVE_ARTWORK_CANDIDATES:
        try:
            data = read_file(udid, directory, leaf, retries=1, reporter=reporter)
        except Exception:  # noqa: BLE001
            data = None
        tried += 1
        if data:
            return leaf, data
        # 通道压根没打通时，剩下十几个候选只会是同样的结果 —— 每个都要干等
        # 一轮握手超时，不短路用户得白等好几分钟。
        if channel_dead():
            report(reporter,
                   f"已试 {tried} 个卡包资源，airlift 通道始终未打通，"
                   "停止继续尝试（换卡面 / 读卡面 / 写主题都走这条通道）。")
            break
    return None


def read_original_skin(udid: str, card_hash: str, retries: int = 2,
                       reporter: Callable[[str], None] | None = None) -> bytes | None:
    """读取设备上这张卡当前的卡面，失败返回 None（设备文件不会被改变）。"""
    try:
        return read_file(udid, pkpass_dir(card_hash), SOURCE_LEAF,
                         retries=retries, reporter=reporter)
    except Exception:  # noqa: BLE001
        return None


def backup_original_skin(udid: str, card_hash: str, force: bool = False,
                         reporter: Callable[[str], None] | None = None,
                         known_missing: bool = False) -> tuple[bool, str]:
    """读取设备卡面并存为**手动备份**（时间戳化，不覆盖既有备份）。

    返回 (是否成功, 说明文字)。force 参数保留以兼容调用方，手动备份每次都生成
    新的时间戳文件，因此不再用它来跳过。
    """
    notes: list[str] = []

    def note(text: str) -> None:
        notes.append(text)
        if reporter is not None:
            try:
                reporter(text)
            except Exception:  # noqa: BLE001
                pass

    data = read_original_skin(udid, card_hash, reporter=note)
    if not data:
        detail = "\n".join(notes)
        if looks_like_missing(detail):
            return False, explain_read_failure(detail, known_missing)
        return False, ("备份卡面失败：设备没有返回卡面数据。\n"
                       "卡包文件未被改动（同步未生效时不会移动文件），可稍后重试。"
                       + (f"\n\n诊断信息：\n{detail}" if detail else ""))
    path = save_manual_backup(card_hash, data)
    if path is None:
        return False, "已读到卡面，但写入本地备份失败，请检查磁盘权限。"
    return True, f"原皮肤已备份（{len(data) / 1024:.0f} KB）：{Path(path).name}"


def ensure_backup(udid: str, card_hash: str,
                  reporter: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """刷入前的**自动备份**：已有自动备份则跳过，否则读取设备并写一份自动备份。"""
    if has_auto_backup(card_hash):
        return True, "本机已有该卡的自动备份，跳过读取。"
    notes: list[str] = []

    def note(text: str) -> None:
        notes.append(text)
        if reporter is not None:
            try:
                reporter(text)
            except Exception:  # noqa: BLE001
                pass

    data = read_original_skin(udid, card_hash, reporter=note)
    if not data:
        detail = "\n".join(notes)
        if looks_like_missing(detail):
            return False, explain_read_failure(detail, known_missing=False)
        return False, ("刷入前自动备份失败：设备没有返回卡面数据。\n"
                       "卡包文件未被改动，可稍后重试。" + (f"\n\n诊断信息：\n{detail}" if detail else ""))
    path = save_auto_backup(card_hash, data)
    if path is None:
        return False, "已读到卡面，但写入自动备份失败，请检查磁盘权限。"
    return True, f"刷入前已自动备份当前卡面（{len(data) / 1024:.0f} KB）。"


def restore_original_skin(udid: str, card_hash: str,
                          reporter: Callable[[str], None] | None = None
                          ) -> tuple[bool, str]:
    """把**最新自动备份**的卡面写回设备，并清除钱包渲染缓存。"""
    path = find_auto_backup(card_hash)
    data = load_bytes(path)
    if not data:
        return False, ("本机没有这张卡的自动备份，请先点「读取卡面」或「备份卡面」，"
                      "再点「恢复原皮」。")

    notes: list[str] = []

    def note(text: str) -> None:
        notes.append(text)
        if reporter is not None:
            try:
                reporter(text)
            except Exception:  # noqa: BLE001
                pass

    directory = pkpass_dir(card_hash)
    assets = build_card_assets(data)
    try:
        ok = write_files_batch(udid, directory, assets, reporter=note)
    except Exception as error:  # noqa: BLE001
        note(f"批量写入异常：{error!r}")
        ok = False
    if not ok:
        # 与 flash_card 一致：批量失败时降级为逐个写入。
        ok = True
        for name, payload in assets:
            try:
                ok = write_file(udid, directory, name, payload, reporter=note) and ok
            except Exception as error:  # noqa: BLE001
                note(f"写入 {name} 异常：{error!r}")
                ok = False
    if not ok:
        detail = "\n".join(notes)
        return False, ("写回原皮肤失败，请查看日志后重试。"
                       + (f"\n\n诊断信息：\n{detail}" if detail else ""))
    try:
        invalidate_cache(udid, card_hash)
    except Exception:  # noqa: BLE001
        pass
    # 需求①(v1.9.3)：已恢复到出厂/原卡面，清除"最近一次刷入的新皮肤"记录，
    # 让下次预览回退显示原皮（而非已恢复的旧新皮肤）。
    try:
        clear_last_flash(card_hash)
    except Exception:  # noqa: BLE001
        pass
    return True, "已恢复到自动备份的卡面，请在 iPhone 上强制关闭「钱包」App 后查看。"


def restore_from_file(udid: str, card_hash: str, png_path: str,
                      reporter: Callable[[str], None] | None = None
                      ) -> tuple[bool, str]:
    """把用户手动挑选的任意历史卡面 PNG 写回设备并清缓存。

    与 restore_original_skin 的区别：源不是本机备份仓库，而是用户在
    「从历史备份恢复」里手动浏览挑选的文件（可来自历史快照目录、也可来自
    其它任何位置）。仅做最小校验：文件须存在且能被 Pillow 解析为图片。
    """
    from pathlib import Path

    path = Path(png_path)
    if not path.is_file():
        return False, "未找到所选的卡面文件，请重新选择。"
    try:
        data = path.read_bytes()
    except Exception as error:  # noqa: BLE001
        return False, f"读取所选文件失败：{error}"
    if not data:
        return False, "所选文件为空，无法用于恢复。"
    # 最小校验：确认是能解析的图片，避免把乱码写进设备。
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except Exception:  # noqa: BLE001
        return False, "所选文件不是有效的图片，无法用于恢复。"

    notes: list[str] = []

    def note(text: str) -> None:
        notes.append(text)
        if reporter is not None:
            try:
                reporter(text)
            except Exception:  # noqa: BLE001
                pass

    directory = pkpass_dir(card_hash)
    assets = build_card_assets(data)
    try:
        ok = write_files_batch(udid, directory, assets, reporter=note)
    except Exception as error:  # noqa: BLE001
        note(f"批量写入异常：{error!r}")
        ok = False
    if not ok:
        ok = True
        for name, payload in assets:
            try:
                ok = write_file(udid, directory, name, payload, reporter=note) and ok
            except Exception as error:  # noqa: BLE001
                note(f"写入 {name} 异常：{error!r}")
                ok = False
    if not ok:
        detail = "\n".join(notes)
        return False, ("写回卡面失败，请查看日志后重试。"
                       + (f"\n\n诊断信息：\n{detail}" if detail else ""))
    try:
        invalidate_cache(udid, card_hash)
    except Exception:  # noqa: BLE001
        pass
    # 需求①(v1.9.3)：从历史卡面恢复成功后，把该卡"最近一次刷入的新皮肤"更新为
    # 刚写回卡面的这份历史卡面（它现在就是设备上的当前卡面）。
    try:
        save_last_flash(card_hash, data)
    except Exception:  # noqa: BLE001
        pass
    return True, "已从所选历史卡面恢复，请在 iPhone 上强制关闭「钱包」App 后查看。"
