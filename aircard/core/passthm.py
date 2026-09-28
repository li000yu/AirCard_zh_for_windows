""".passthm handling - inspection, flashing and authoring (all in Chinese).

Ported from `aircard_backend.py` plus the Swift `PasscodeThemeExporter`.
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .airlift import write_file, write_files_batch
from .imaging import KEYPAD_SUBTEXTS, SUPPORTED_LOCALES, to_png_bytes

ProgressCB = Callable[[str, int, int, str | None], None]

CYRILLIC_SUBTEXTS_RU = {
    "2": "А Б В Г", "3": "Д Е Ж З", "4": "И Й К Л", "5": "М Н О П",
    "6": "Р С Т У", "7": "Ф Х Ц Ч", "8": "Ш Щ Ъ Ы", "9": "Ь Э Ю Я",
}

CYRILLIC_SUBTEXTS_UK = {
    "2": "А Б В Г", "3": "Д Е Ж З", "4": "І Ї Й К", "5": "Л М Н О",
    "6": "П Р С Т", "7": "У Ф Х Ц", "8": "Ч Ш Щ Ь", "9": "Ю Я",
}

KEYPAD_LOCALES = list(SUPPORTED_LOCALES)

_LANGUAGE_LABELS = {
    "all": "全部语言（通用）", "uk": "乌克兰语 (uk)", "ru": "俄语 (ru)",
    "en": "英语 (en)", "other": "其他 / 兜底", "es": "西班牙语 (es)",
    "de": "德语 (de)", "fr": "法语 (fr)", "pl": "波兰语 (pl)",
    "it": "意大利语 (it)", "pt": "葡萄牙语 (pt)", "tr": "土耳其语 (tr)",
    "ja": "日语 (ja)", "ko": "韩语 (ko)", "zh": "简体中文 (zh)",
    "ar": "阿拉伯语 (ar)", "he": "希伯来语 (he)",
}

_BOLD_LABELS = {
    "both": "通用（常规 + 粗体）", "bold": "仅粗体（快速）",
    "regular": "仅常规（快速）",
}

_VERSION_LABELS = {
    "TelephonyUI-10": "TelephonyUI-10（iOS 18 及以上）",
    "TelephonyUI-9": "TelephonyUI-9（iOS 16–17）",
    "TelephonyUI-8": "TelephonyUI-8（iOS 14–15）",
    "all": "通用（8、9、10 全部）",
}


def language_label(code: str) -> str:
    return _LANGUAGE_LABELS.get(code, code)


def bold_label(code: str) -> str:
    return _BOLD_LABELS.get(code, code)


def version_label(code: str) -> str:
    return _VERSION_LABELS.get(code, code)


def language_codes() -> list[str]:
    return ["all", "uk", "ru", "en", "other", "es", "de", "fr", "pl",
            "it", "pt", "tr", "ja", "ko", "zh", "ar", "he"]


def bold_codes() -> list[str]:
    return ["both", "bold", "regular"]


def version_codes() -> list[str]:
    return ["TelephonyUI-10", "TelephonyUI-9", "TelephonyUI-8", "all"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _target_directories(version: str) -> list[str]:
    normalised = (version or "TelephonyUI-10").strip()
    if normalised.lower() in ("all", "universal"):
        return [
            "/var/mobile/Library/Caches/TelephonyUI-10",
            "/var/mobile/Library/Caches/TelephonyUI-9",
            "/var/mobile/Library/Caches/TelephonyUI-8",
        ]
    return [f"/var/mobile/Library/Caches/{normalised}"]


def image_entries(archive: zipfile.ZipFile) -> list[str]:
    return [
        name for name in archive.namelist()
        if not name.startswith("__MACOSX")
        and not name.endswith("/")
        and not Path(name).name.startswith(".")
        and name.lower().endswith((".png", ".jpg", ".jpeg"))
    ]


def parse_passthm_archive(
    passthm_path: str,
    telephony_ver: str = "TelephonyUI-10",
    target_lang: str = "all",
    target_bold: str = "both",
    scientific: bool = False,
) -> list[tuple[str, str, bytes]]:
    path = Path(passthm_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"找不到密码主题文件：{passthm_path}")

    with zipfile.ZipFile(path, "r") as archive:
        entries = image_entries(archive)
        if not entries:
            return []

        items: dict[str, bytes] = {}
        target_lang = (target_lang or "all").lower().strip()
        target_bold = (target_bold or "both").lower().strip()

        for entry in entries:
            leaf = Path(entry).name
            data = archive.read(entry)
            stem = Path(leaf).stem
            stem_clean = re.sub(r"--?white(?:-bold)?$", "", stem, flags=re.IGNORECASE)
            match = re.search(r"^(?:([a-zA-Z]+)-)?([0-9*#])(?:-([^-\n]+))?", stem_clean)
            digit = None
            subtext = ""
            original_lang = None
            if match:
                original_lang = match.group(1)
                digit = match.group(2)
                if match.group(3):
                    subtext = match.group(3).strip()
            if not digit:
                fallback = re.search(r"([0-9*#])", leaf)
                if fallback:
                    digit = fallback.group(1)

            if subtext and subtext.lower() in (
                    "bold", "regular", "white", "black", "light", "dark", "normal"):
                subtext = ""

            if not scientific:
                # Preserve the fast-path behaviour of the macOS build.
                if target_lang == "all" and target_bold == "both":
                    items[leaf] = data

                if digit:
                    if target_lang == "all":
                        languages = list(KEYPAD_LOCALES)
                        if original_lang and original_lang.lower() not in languages:
                            languages.insert(0, original_lang.lower())
                    else:
                        languages = [target_lang]
                        if target_lang != "other":
                            languages.append("other")

                    if target_bold == "bold":
                        bold_suffixes = ["-bold"]
                    elif target_bold == "regular":
                        bold_suffixes = [""]
                    else:
                        bold_suffixes = ["", "-bold"]

                    standard = KEYPAD_SUBTEXTS.get(digit)
                    for lang in languages:
                        for bold_suffix in bold_suffixes:
                            items[f"{lang}-{digit}---white{bold_suffix}.png"] = data
                            if standard:
                                items[f"{lang}-{digit}-{standard}--white{bold_suffix}.png"] = data
                                if " " in standard:
                                    items[
                                        f"{lang}-{digit}-{standard.replace(' ', '')}"
                                        f"--white{bold_suffix}.png"
                                    ] = data
                            if lang in ("ru", "all") and digit in CYRILLIC_SUBTEXTS_RU:
                                items[
                                    f"{lang}-{digit}-{CYRILLIC_SUBTEXTS_RU[digit]}"
                                    f"--white{bold_suffix}.png"
                                ] = data
                            if lang in ("uk", "all") and digit in CYRILLIC_SUBTEXTS_UK:
                                items[
                                    f"{lang}-{digit}-{CYRILLIC_SUBTEXTS_UK[digit]}"
                                    f"--white{bold_suffix}.png"
                                ] = data
                            if subtext:
                                items[
                                    f"{lang}-{digit}-{subtext}--white{bold_suffix}.png"
                                ] = data
            else:
                if digit:
                    items[leaf] = data

        result: list[tuple[str, str, bytes]] = []
        for directory in _target_directories(telephony_ver):
            for leaf, data in items.items():
                result.append((directory, leaf, data))
        return result


def detect_version(passthm_path: str) -> str:
    detected = "TelephonyUI-10"
    with zipfile.ZipFile(Path(passthm_path).expanduser(), "r") as archive:
        for entry in archive.namelist():
            low = entry.lower()
            if "telephonyui-8" in low or "telephony-8" in low:
                return "TelephonyUI-8"
            if "telephonyui-9" in low or "telephony-9" in low:
                detected = "TelephonyUI-9"
                break
    return detected


def inspect_passthm(passthm_path: str) -> dict[str, Any]:
    """Returns everything the UI needs about a theme package."""
    path = Path(passthm_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"找不到密码主题文件：{passthm_path}")

    detected = detect_version(str(path))
    items = parse_passthm_archive(str(path), detected)
    if not items:
        raise ValueError("压缩包中未找到任何图片资源")

    previews: dict[str, Image.Image] = {}
    for _directory, leaf, data in items:
        match = re.search(r"^[a-zA-Z]+-([0-9*#])-?", leaf)
        digit = match.group(1) if match else None
        if not digit:
            fallback = re.search(r"([0-9*#])", leaf)
            digit = fallback.group(1) if fallback else None
        if digit and digit not in previews:
            image = Image.open(io.BytesIO(data))
            image.load()
            previews[digit] = image.convert("RGBA")
    return {
        "name": path.stem,
        "filePath": str(path),
        "detectedVersion": detected,
        "fileCount": len(items),
        "keysPreview": previews,
    }


# ---------------------------------------------------------------------------
# Flashing
# ---------------------------------------------------------------------------

def flash_passthm(
    udid: str,
    passthm_path: str,
    telephony_ver: str = "TelephonyUI-10",
    target_lang: str = "all",
    target_bold: str = "both",
    on_message: ProgressCB | None = None,
    logger: Callable[[str], None] | None = None,
) -> bool:
    path = Path(passthm_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"找不到密码主题文件：{passthm_path}")

    def report(message: str, step: int | None = None, total: int | None = None) -> None:
        if on_message is not None:
            on_message(message, step or 0, total or 0, None)
        if logger is not None:
            logger(message)

    items = parse_passthm_archive(str(path), telephony_ver, target_lang, target_bold)
    if not items:
        raise ValueError("压缩包中未找到任何图片资源")

    by_directory: dict[str, list[tuple[str, bytes]]] = {}
    for directory, leaf, payload in items:
        by_directory.setdefault(directory, []).append((leaf, payload))

    # Marker files such as _big / _small must ride along
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for entry in archive.namelist():
                leaf_name = Path(entry).name
                if leaf_name in ("_big", "_small") and not entry.endswith("/"):
                    data = archive.read(entry)
                    for directory, files in by_directory.items():
                        if all(leaf != leaf_name for leaf, _ in files):
                            files.append((leaf_name, data))
    except Exception:
        pass

    total_steps = sum(len(files) for files in by_directory.values())
    processed = 0
    report(f"正在刷入密码主题「{path.stem}」（共 {total_steps} 个资源）…", 0, total_steps)

    for directory, files in by_directory.items():
        name = Path(directory).name
        base = processed

        def handler(base: int = base) -> Callable[[dict[str, Any]], None]:
            def inner(payload: dict[str, Any]) -> None:
                index = payload.get("index", 0)
                leaf = payload.get("leaf", "")
                current = min(base + index, total_steps)
                report(f"正在写入 {leaf}（{current}/{total_steps}）…", current, total_steps)
            return inner

        report(f"正在将 {len(files)} 个资源写入 {name}…", base, total_steps)
        ok = write_files_batch(
            udid, directory, files, retries=3,
            progress_callback=handler(),
            logger=logger)

        if not ok:
            report(f"{name} 批量写入未成功，正在降级为逐个写入…", base, total_steps)
            failures: list[str] = []
            for index, (leaf, payload) in enumerate(files, 1):
                current = base + index
                report(f"[逐个] 正在写入 {leaf}（{current}/{total_steps}）…",
                       current, total_steps)
                if not write_file(udid, directory, leaf, payload, retries=3):
                    failures.append(leaf)
                time.sleep(0.08)
            if failures:
                report(f"{name} 中有 {len(failures)} 个文件写入失败：" +
                       "、".join(failures[:5]), total_steps, total_steps)
                return False
        processed += len(files)

    report(f"密码主题「{path.stem}」已成功应用！请锁定 iPhone 查看效果。",
           total_steps, total_steps)
    return True


# ---------------------------------------------------------------------------
# Authoring
# ---------------------------------------------------------------------------

def export_theme(keys: dict[str, Image.Image],
                 target_path: str,
                 language: str = "all",
                 bold_mode: str = "both") -> None:
    """Writes a standard .passthm package (zipped TelephonyUI-10 and -9 trees)."""
    target = Path(target_path)
    if target.exists():
        target.unlink()

    locales = list(KEYPAD_LOCALES) if language == "all" else (
        [language] if language == "other" else [language, "other"])
    if bold_mode == "both":
        bold_suffixes = ["", "-bold"]
    elif bold_mode == "bold":
        bold_suffixes = ["-bold"]
    else:
        bold_suffixes = [""]

    payloads: dict[str, bytes] = {}
    for digit, image in keys.items():
        png = to_png_bytes(image)
        subtext = KEYPAD_SUBTEXTS.get(digit, "")
        for locale in locales:
            for suffix in bold_suffixes:
                payloads[f"{locale}-{digit}---white{suffix}.png"] = png
                if subtext:
                    payloads[f"{locale}-{digit}-{subtext}--white{suffix}.png"] = png

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for version in ("TelephonyUI-10", "TelephonyUI-9"):
            archive.writestr(f"{version}/_big", b"")
            for leaf, data in payloads.items():
                archive.writestr(f"{version}/{leaf}", data)


def stage_temporary_theme(keys: dict[str, Image.Image],
                          language: str = "all",
                          bold_mode: str = "both") -> str:
    import tempfile
    import uuid

    target = Path(tempfile.gettempdir()) / f"AirCard_Custom_{uuid.uuid4().hex}.passthm"
    export_theme(keys, str(target), language, bold_mode)
    return str(target)
