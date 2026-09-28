"""原卡面（皮肤）备份仓库 —— 扁平文件夹方案。

备份统一存放在「软件同级目录」下的 `aircard_backups/` 文件夹中（不存在自动创建）。

命名规则（均由 `safe_hash` 安全化后的卡号哈希构成文件名）：
    auto_<YYYYMMDDHHMMSS>_<安全哈希>.png   自动备份（读取卡面 / 刷入前自动备份时生成）
    <YYYYMMDDHHMMSS>_<安全哈希>.png        手动备份（点「备份卡面」时生成）
    lastflash_<安全哈希>.png               最近一次「成功刷入」的新皮肤（需求①，单文件覆盖式）

检测某卡有无备份 = 扫描文件夹中文件名「以 auto_ 前缀 + 末尾 _<安全哈希>.png」 或
「无 auto_ / lastflash_ 前缀 + 末尾 _<安全哈希>.png」 的文件：
    * 存在自动备份 → 「恢复原皮」按钮启用；
    * 存在手动备份 → 「从历史恢复」按钮启用；
    * 均无 → 两个按钮置灰禁用。

`lastflash_` 仅用于卡片预览显示「最近一次刷入的新皮肤」，不参与任何恢复按钮的
判定（否则会误点亮「从历史恢复」），因此在手动备份检测里被显式排除（见 `_iter_matching`）。

卡包原生素材（strip / logo …，仅用于界面认卡）单独以 `native_` 前缀存放。
它**不**算真实卡面备份（不参与「恢复原皮」），但被视同「手动备份」用于启用
「从历史恢复」：从未刷过皮肤的卡没有合并卡面文件、读不到真实卡面，读卡包原图
落盘的 native_ 文件即作为这类卡的唯一恢复来源（需求③）。

任何 IO 异常都在此吞掉并返回失败，绝不让备份问题打断主流程。
"""
from __future__ import annotations

import io
import time
from pathlib import Path

try:
    import sys

    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None) is not None:
        # PyInstaller 单文件：sys.executable 即 AirCard.exe 本身
        _BASE = Path(sys.executable).resolve().parent
    elif getattr(sys, "frozen", False):
        _BASE = Path(sys.executable).resolve().parent
    else:
        # 开发态：本文件位于 aircard/core/skinstore.py，向上两级是项目根
        _BASE = Path(__file__).resolve().parents[2]
except Exception:  # noqa: BLE001
    _BASE = Path.cwd()

# 软件同级目录下的备份文件夹
BACKUP_ROOT = _BASE / "aircard_backups"


# ---------------------------------------------------------------------------
# 路径 / 文件名工具
# ---------------------------------------------------------------------------
def ensure_root() -> Path:
    """确保备份根目录存在（不存在则自动创建），返回其路径。"""
    try:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return BACKUP_ROOT


def safe_hash(card_hash: str) -> str:
    """把可能含 `/` `+` `=` 等文件系统非法字符的卡号哈希变成安全的文件名片段。

    Windows 文件名非法字符：\\ / : * ? " < > |。这里只保留字母数字与 - _，
    其余一律替换为 `_`，并截断到 64 字符，避免过长或撞名。
    """
    out: list[str] = []
    for char in card_hash:
        out.append(char if (char.isalnum() or char in "-_") else "_")
    cleaned = "".join(out)
    return cleaned[:64] or "card"


def _stamp() -> str:
    return time.strftime("%Y%m%d%H%M%S")


def auto_filename(card_hash: str) -> str:
    return f"auto_{_stamp()}_{safe_hash(card_hash)}.png"


def manual_filename(card_hash: str) -> str:
    return f"{_stamp()}_{safe_hash(card_hash)}.png"


def native_filename(card_hash: str, leaf: str) -> str:
    """卡包原图预览的落盘文件名（带 native_ 前缀，避免与备份检测冲突）。"""
    safe_leaf = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in leaf)[:40]
    return f"native_{safe_hash(card_hash)}_{safe_leaf}.png"


def lastflash_filename(card_hash: str) -> str:
    """最近一次「成功刷入」的新皮肤文件名（单文件覆盖式，始终只留最新一份）。"""
    return f"lastflash_{safe_hash(card_hash)}.png"


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def _unique_target(prefix: str, card_hash: str) -> Path:
    """生成不撞名的目标路径。

    时间戳只到「秒」，同一秒内连续两次备份会算出完全相同的文件名。直接覆盖会
    静默丢掉一份备份，因此同名已存在时在时间戳后追加 `-2` / `-3` …
    （加在哈希**之前**，保证文件名始终以 `_<哈希>.png` 结尾，检测逻辑不受影响）。
    """
    base = ensure_root()
    suffix = f"_{safe_hash(card_hash)}.png"
    stamp = _stamp()
    candidate = base / f"{prefix}{stamp}{suffix}"
    attempt = 2
    while candidate.exists() and attempt < 100:
        candidate = base / f"{prefix}{stamp}-{attempt}{suffix}"
        attempt += 1
    return candidate


def save_auto_backup(card_hash: str, png_bytes: bytes) -> Path | None:
    """写入一份自动备份，返回文件路径；失败返回 None。"""
    if not png_bytes:
        return None
    try:
        path = _unique_target("auto_", card_hash)
        path.write_bytes(png_bytes)
        return path
    except Exception:  # noqa: BLE001
        return None


def save_manual_backup(card_hash: str, png_bytes: bytes) -> Path | None:
    """写入一份手动备份，返回文件路径；失败返回 None。"""
    if not png_bytes:
        return None
    try:
        path = _unique_target("", card_hash)
        path.write_bytes(png_bytes)
        return path
    except Exception:  # noqa: BLE001
        return None


def save_artwork(card_hash: str, leaf: str, png_bytes: bytes,
                 preview_bytes: bytes | None = None) -> bool:
    """落盘一份"卡包原图"预览（仅认卡用，不参与恢复）。"""
    if not png_bytes:
        return False
    try:
        ensure_root()
        (BACKUP_ROOT / native_filename(card_hash, leaf)).write_bytes(
            preview_bytes or png_bytes)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 最近一次「成功刷入」的新皮肤（需求①）
# ---------------------------------------------------------------------------
def save_last_flash(card_hash: str, png_bytes: bytes) -> Path | None:
    """记录某卡「最近一次成功刷入的新皮肤」。单文件覆盖式，始终只留最新一份。

    仅用于卡片预览展示（让重启/重扫后已刷过的卡直接显示新皮肤，而非原皮），
    不参与任何「恢复」按钮的判定。失败返回 None。
    """
    if not png_bytes:
        return None
    try:
        ensure_root()
        path = BACKUP_ROOT / lastflash_filename(card_hash)
        path.write_bytes(png_bytes)
        return path
    except Exception:  # noqa: BLE001
        return None


def last_flash_path(card_hash: str) -> Path | None:
    """该卡最近一次刷入的新皮肤预览路径；无则 None。"""
    try:
        path = BACKUP_ROOT / lastflash_filename(card_hash)
        return path if path.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def clear_last_flash(card_hash: str) -> bool:
    """清除该卡「最近一次刷入的新皮肤」记录（例如「恢复原皮」成功后调用）。

    返回是否真的删除了文件。任何异常都吞掉并返回 False。
    """
    try:
        path = BACKUP_ROOT / lastflash_filename(card_hash)
        if path.is_file():
            path.unlink()
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 查找 / 检测
# ---------------------------------------------------------------------------
def _iter_matching(card_hash: str, auto: bool) -> list[Path]:
    """返回匹配的文件（按时间戳倒序）。

    auto=True  → 文件名以 `auto_` 开头、以 `_<安全哈希>.png` 结尾；
    auto=False → 文件名**不以** `auto_` 开头、但以 `_<安全哈希>.png` 结尾
                 （即手动备份；native_ 前缀文件结尾是 `_<leaf>.png`，不会命中）。
    `lastflash_` 前缀文件以 `_<安全哈希>.png` 结尾，故会被手动检测误命中——必须
    显式排除（它只用于预览最近一次刷入的新皮肤，不参与任何恢复按钮判定）。
    """
    try:
        if not BACKUP_ROOT.is_dir():
            return []
        target = f"_{safe_hash(card_hash)}.png"
        matches: list[tuple[str, Path]] = []
        for item in BACKUP_ROOT.iterdir():
            name = item.name
            if not name.endswith(target) or not item.is_file():
                continue
            # 预览用途的 lastflash_ 不计入备份检测；native_ 虽不以 _<哈希>.png 结尾
            # 但同样排除，避免将来命名变化引发的误判。
            if name.startswith("lastflash_") or name.startswith("native_"):
                continue
            is_auto = name.startswith("auto_")
            if auto and not is_auto:
                continue
            if (not auto) and is_auto:
                continue
            # 时间戳位于前缀与 <哈希> 之间（14 位数字）
            stamp = name[len("auto_" if is_auto else ""):-len(target)]
            matches.append((stamp, item))
        matches.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in matches]
    except Exception:  # noqa: BLE001
        return []


def find_auto_backup(card_hash: str) -> Path | None:
    """最新的一份自动备份；无则 None。"""
    files = _iter_matching(card_hash, auto=True)
    return files[0] if files else None


def find_manual_backups(card_hash: str) -> list[Path]:
    """全部手动备份（按时间倒序），供「从历史恢复」选择。"""
    return _iter_matching(card_hash, auto=False)


def has_auto_backup(card_hash: str) -> bool:
    return find_auto_backup(card_hash) is not None


def has_manual_backup(card_hash: str) -> bool:
    """存在手动备份（<时间戳>_<哈希>.png）或卡包原图备份（native_<哈希>_*）即视为有。

    需求③：从未刷过皮肤的卡没有合并卡面文件、无法生成真实备份，读卡包原图后
    落盘的 native_ 文件即被视同手动备份，启用「从历史恢复」按钮，让用户能把它
    写回认卡。注意 native_ 文件本身不参与「恢复原皮」（它只是 strip/logo 素材）。
    """
    if find_manual_backups(card_hash):
        return True
    return artwork_path(card_hash) is not None


def artwork_path(card_hash: str, leaf: str = "") -> Path | None:
    """卡包原图（native_）预览路径；无 leaf 时返回**最新**一张（按修改时间）。"""
    try:
        if not BACKUP_ROOT.is_dir():
            return None
        target = safe_hash(card_hash)
        if leaf:
            candidate = BACKUP_ROOT / native_filename(card_hash, leaf)
            return candidate if candidate.is_file() else None
        found = [p for p in BACKUP_ROOT.iterdir()
                 if p.name.startswith(f"native_{target}_") and p.is_file()]
        # 需求②：文件名里没有时间戳，「最新一次」按文件修改时间判定
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return found[0] if found else None
    except Exception:  # noqa: BLE001
        return None


def artwork_leaf(card_hash: str) -> str:
    """从文件名反推上次读到的素材文件名（用于界面标注来源）。"""
    path = artwork_path(card_hash)
    if path is None:
        return ""
    name = path.name
    prefix = f"native_{safe_hash(card_hash)}_"
    if name.startswith(prefix):
        return name[len(prefix):-4]  # 去 .png
    return ""


def load_bytes(path) -> bytes | None:
    """读取任意备份文件路径的字节；失败返回 None。"""
    try:
        p = Path(path)
        if p.is_file():
            return p.read_bytes()
    except Exception:  # noqa: BLE001
        return None
    return None


def image_size(png_bytes: bytes) -> tuple[int, int] | None:
    """返回 PNG 字节的像素尺寸 (宽, 高)；无法解析返回 None。"""
    if not png_bytes:
        return None
    try:
        from PIL import Image
        with Image.open(io.BytesIO(png_bytes)) as image:
            return tuple(image.size)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001
        return None


def latest_face_backup(card_hash: str) -> tuple[str | None, tuple[int, int] | None]:
    """该卡最新一份「真实卡面」备份（自动或手动，取时间戳最新者）的路径与尺寸。

    用于扫描到卡片时直接在卡片上预览其已备份的卡面并标注尺寸（需求①）。
    取最新规则：分别取最新的 auto_ 与最新的手动备份，再按文件名里 14 位时间戳比较。
    native_ 卡包原图不属于真实卡面，不计入此处（它只在没刷过皮肤的卡上兜底认卡）。
    没有真实卡面备份时返回 (None, None)。
    """
    auto = find_auto_backup(card_hash)            # 最新自动备份（已按时间倒序）
    manuals = find_manual_backups(card_hash)      # 全部手动备份（已按时间倒序）
    manual = manuals[0] if manuals else None
    candidates = [p for p in (auto, manual) if p is not None]
    if not candidates:
        return None, None
    suffix = f"_{safe_hash(card_hash)}.png"

    def _stamp_of(path: Path) -> str:
        name = path.name
        prefix = "auto_" if name.startswith("auto_") else ""
        return name[len(prefix):-len(suffix)]

    candidates.sort(key=_stamp_of, reverse=True)
    best = candidates[0]
    data = load_bytes(best)
    size = image_size(data) if data else None
    return str(best), size


# ---------------------------------------------------------------------------
# 兼容旧调用（若仍有残留引用）
# ---------------------------------------------------------------------------
def backup_dir(card_hash: str) -> Path:  # noqa: D401  (旧接口保留)
    return ensure_root()


def has_backup(card_hash: str) -> bool:
    return has_auto_backup(card_hash) or has_manual_backup(card_hash)


def load_source_bytes(card_hash: str) -> bytes | None:
    return load_bytes(find_auto_backup(card_hash))


def save_backup(card_hash: str, png_bytes: bytes,
                preview_bytes: bytes | None = None) -> bool:
    return save_manual_backup(card_hash, png_bytes) is not None


def source_path(card_hash: str) -> Path:
    return ensure_root()


def preview_path(card_hash: str) -> Path:
    return ensure_root()


def read_preview_path(card_hash: str) -> Path:
    return ensure_root()
