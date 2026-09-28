"""读取 Windows 版 Apple 组件自己的日志（ASL）。

Apple 的 MobileDevice.dll / AirTrafficHost.dll 在 Windows 上通过 ASL.dll 记日志，
落盘位置是：

    %APPDATA%\\Apple Computer\\Logs\\asl.<HHMMSS>_<DDMMMYY>.log

每个进程首次写日志时会新建一个文件，所以「同步前后各取一次快照、只读增量」
就能拿到本次操作期间 Apple 组件自己报的错 —— 例如 `com.apple.atc` 服务起不来、
会话启动失败、grappa 初始化失败等。这些信息是 ctypes 调用拿不到的，对定位
airlift 同步通道不通的原因至关重要。

模块原则：只读、绝不抛异常、绝不写任何东西。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterable

MAX_LINES = 40
MAX_LINE = 220

# 只保留跟设备连接 / 同步有关的线索，避免被无关噪音淹没
KEYWORDS = (
    "airtraffic", "athost", "atc", "grappa", "syncallowed", "sync",
    "amdevice", "securestartservice", "startsession", "validatepairing",
    "pair", "trust", "lockdown", "usbmux", "afc", "error", "fail",
    "denied", "invalid", "timeout", "service", "session",
)


def log_dir() -> Path | None:
    try:
        root = os.environ.get("APPDATA") or ""
        if not root:
            return None
        path = Path(root) / "Apple Computer" / "Logs"
        return path if path.is_dir() else None
    except Exception:  # noqa: BLE001
        return None


def snapshot() -> dict[str, int]:
    """记录当前每个日志文件的字节数（不含目录遍历失败的异常）。"""
    state: dict[str, int] = {}
    directory = log_dir()
    if directory is None:
        return state
    try:
        for entry in directory.glob("asl.*.log"):
            try:
                state[str(entry)] = entry.stat().st_size
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        return state
    return state


def _is_relevant(line: str) -> bool:
    low = line.lower()
    return any(word in low for word in KEYWORDS)


def delta(before: dict[str, int] | None, limit: int = MAX_LINES) -> list[str]:
    """返回自 `before` 以来新增的、与连接/同步相关的日志行。"""
    if not before:
        return []
    directory = log_dir()
    if directory is None:
        return []
    collected: list[str] = []
    try:
        entries = sorted(directory.glob("asl.*.log"), key=lambda p: p.stat().st_mtime)
    except Exception:  # noqa: BLE001
        return []
    for entry in entries:
        key = str(entry)
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        start = before.get(key, 0) if key in before else 0
        if size <= start:
            continue
        try:
            with open(entry, "rb") as handle:
                handle.seek(start)
                raw = handle.read(size - start)
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.rstrip()
            if not line or not _is_relevant(line):
                continue
            collected.append(line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…")
    return collected[-limit:]


def recent(limit: int = MAX_LINES, max_age: float = 900.0) -> list[str]:
    """读取最近 `max_age` 秒内的相关日志（不依赖快照，用于兜底）。"""
    directory = log_dir()
    if directory is None:
        return []
    now = time.time()
    collected: list[str] = []
    try:
        entries = sorted(directory.glob("asl.*.log"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:  # noqa: BLE001
        return []
    for entry in entries[:3]:
        try:
            if now - entry.stat().st_mtime > max_age:
                continue
            raw = entry.read_bytes()
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.rstrip()
            if not line or not _is_relevant(line):
                continue
            collected.append(line if len(line) <= MAX_LINE else line[:MAX_LINE] + "…")
    return collected[-limit:]


def describe(lines: Iterable[str]) -> str:
    lines = list(lines)
    if not lines:
        return ""
    return "\n".join(f"    · {line}" for line in lines)
