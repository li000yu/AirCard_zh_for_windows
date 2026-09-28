"""Wallet card flashing flow - port of `aircard_backend.py --flash`."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .airlift import CACHE_FILES, remove_files, write_file, write_files_batch
from .card_assets import build_card_assets
from .imaging import prepare_card_bytes

MessageCB = Callable[[str, int, int], None]


def flash_card(
    udid: str,
    card_hash: str,
    image_path: str,
    on_message: MessageCB | None = None,
    logger: Callable[[str], None] | None = None,
) -> bool:
    """Writes the artwork for one pass and invalidates Wallet's rendered faces."""
    source = Path(image_path)
    if not source.is_file():
        raise FileNotFoundError(f"找不到图片文件：{image_path}")

    def report(message: str, step: int = 0, total: int = 0) -> None:
        if on_message is not None:
            on_message(message, step, total)
        if logger is not None:
            logger(message)

    def diagnose(message: str) -> None:
        """airlift 内部失败细节——只写日志，不占用进度/状态条。"""
        text = f"  · {message}"
        if logger is not None:
            logger(text)
        elif on_message is not None:
            on_message(text, 0, 0)

    payload = source.read_bytes()
    if source.suffix.lower() != ".png":
        try:
            payload = prepare_card_bytes(str(source))
            report("图片已按钱包尺寸优化完成。", 0, 4)
        except Exception as error:
            report(f"图片处理失败：{error}", 0, 4)
            return False

    assets = build_card_assets(payload)
    pkpass_dir = f"/var/mobile/Library/Passes/Cards/{card_hash}.pkpass"

    total_steps = 4
    step = 1
    report(f"正在写入 {len(assets)} 个卡面资源（批量快速写入）…", step, total_steps)

    ok = write_files_batch(udid, pkpass_dir, assets, reporter=diagnose)
    if not ok:
        report("批量写入失败，正在降级为逐个写入…", step, total_steps)
        ok = True
        for asset, data in assets:
            if not write_file(udid, pkpass_dir, asset, data, reporter=diagnose):
                ok = False
    if not ok:
        report(f"卡面资源写入失败，{card_hash[:12]}… 未更新。", step, total_steps)
        diagnose(f"目标路径：{pkpass_dir}")
        diagnose("常见原因：① 扫描仍在运行，与设备同步争用 USB 通道；"
                 "② 该哈希不是真实卡包（目标目录不存在）；"
                 "③ iPhone 已锁屏、未信任本机，或数据线/接口不稳。")
        return False

    for extension in (".cache", ".pkcache"):
        step += 1
        cache_dir = f"/var/mobile/Library/Passes/Cards/{card_hash}{extension}"
        report(f"正在清除钱包缓存（{extension}）…", step, total_steps)
        try:
            cleared = remove_files(udid, cache_dir, list(CACHE_FILES),
                                   reporter=diagnose)
        except Exception as error:  # noqa: BLE001
            diagnose(f"缓存清理异常：{error!r}")
            cleared = False
        if not cleared:
            report(f"无法清除钱包缓存（{extension}），该卡片未被标记为已更新。",
                   step, total_steps)
            return False

    step += 1
    report(f"卡片 {card_hash[:12]}… 更新成功！", step, total_steps)
    # Give the device a moment to settle before the next card.
    time.sleep(0.2)
    return True
