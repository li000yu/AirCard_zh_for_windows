"""Persistence for the saved card hashes (UserDefaults + ~/.aircard_cards.json)."""
from __future__ import annotations

import json
from pathlib import Path

CARDS_STORE_PATH = Path.home() / ".aircard_cards.json"
LEGACY_STORE_PATH = Path.home() / ".lumicards_cards.json"
# 卡片辨识元数据（发现顺序 / 发现时间）单独存一个 sidecar。
# 主文件 `~/.aircard_cards.json` 必须继续保持「纯哈希字符串数组」，
# 否则会破坏与 macOS 原版存档的互通性。
CARDS_META_PATH = Path.home() / ".aircard_card_meta.json"

DUMMY_HASHES = {
    "M6nDwZrkYbFlsodLgCbvyFZQ1cc=",
    "kJL-D0rr-SZhbj2c8nK-OQ9hCMY=",
    "hwAtAmHKYwsQrJbT5cTNDsaxVME=",
}


def _is_dummy(value: str) -> bool:
    return value in DUMMY_HASHES or ("-" in value and len(value) == 36)


def load_saved_cards() -> list[str]:
    loaded: list[str] = []
    for store in (CARDS_STORE_PATH, LEGACY_STORE_PATH):
        if store.is_file():
            try:
                data = json.loads(store.read_text("utf-8"))
                if isinstance(data, list) and data:
                    loaded.extend(str(item) for item in data)
            except Exception:
                pass
    seen: list[str] = []
    for value in loaded:
        if value and value not in seen and not _is_dummy(value):
            seen.append(value)
    return seen


def save_cards(cards: list[str]) -> None:
    try:
        unique = list(dict.fromkeys(cards))
        CARDS_STORE_PATH.write_text(json.dumps(unique, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_card_meta() -> dict[str, dict[str, float]]:
    """读取辨识元数据：{card_hash: {"found_at": epoch, "found_order": int}}。

    读不到或格式不对时返回空字典，绝不影响主流程。
    """
    if not CARDS_META_PATH.is_file():
        return {}
    try:
        data = json.loads(CARDS_META_PATH.read_text("utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    meta: dict[str, dict[str, float]] = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        try:
            meta[str(key)] = {
                "found_at": float(value.get("found_at") or 0.0),
                "found_order": int(value.get("found_order") or 0),
            }
        except (TypeError, ValueError):
            continue
    return meta


def save_card_meta(meta: dict[str, dict[str, float]]) -> None:
    try:
        CARDS_META_PATH.write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        pass


def clear_saved_cards() -> None:
    """清空上次扫描持久化的卡片列表（每次启动都应重置初始状态）。"""
    for store in (CARDS_STORE_PATH, LEGACY_STORE_PATH, CARDS_META_PATH):
        try:
            if store.is_file():
                store.unlink()
        except Exception:  # noqa: BLE001
            pass


def add_card_hash(raw: str, existing: list[str]) -> list[str]:
    """Splits pasted hashes on whitespace, commas or semicolons."""
    import re

    added: list[str] = []
    for part in re.split(r"[ \n\r\t,;]+", raw or ""):
        clean = part.strip().strip(".")
        if 16 <= len(clean) <= 64 and clean not in existing and not _is_dummy(clean):
            existing.append(clean)
            added.append(clean)
    return added
