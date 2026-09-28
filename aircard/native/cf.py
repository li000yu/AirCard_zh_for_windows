"""CoreFoundation bindings used to talk to Apple's Windows private frameworks.

The macOS build compiled Objective-C helpers against MobileDevice.framework and
AirTrafficHost.framework. On Windows those very same APIs ship inside
"Common Files\\Apple\\Mobile Device Support" as MobileDevice.dll,
AirTrafficHost.dll and CoreFoundation.dll, exported with identical C symbols.

This module keeps only that subset which is actually needed, and adds helpers to
move data between Python plists and CoreFoundation objects so no CF collection
has to be built by hand.
"""
from __future__ import annotations

import ctypes
import os
import plistlib
from ctypes import wintypes
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

kCFStringEncodingUTF8 = 0x08000100
kCFPropertyListBinaryFormat_v1_0 = 200

APLIST_MAX_DEPTH = 0x7FFFFFFF  # 0 == no extra validation, keep it permissive

# Every directory we add must stay referenced for the lifetime of the process,
# otherwise Windows drops it from the loader search list again.
_DLL_DIRECTORY_HANDLES: list[Any] = []


class AppleSupportNotFoundError(RuntimeError):
    """Raised when Apple's Mobile Device Support runtime cannot be located."""


def as_void_p(value: Any = None) -> ctypes.c_void_p:
    """把任意「指针形态」的值安全地转成 c_void_p。

    ctypes 不允许对已经是 c_void_p 实例的值再套一层：
    `ctypes.c_void_p(some_c_void_p_instance)` 会抛
    "cannot be converted to pointer"。由于 CFStringCreateWithCString 等函数的
    restype 已经是 c_void_p，返回值在传递前必须走这里做幂等转换。
    """
    if isinstance(value, ctypes.c_void_p):
        return value
    if value is None:
        return ctypes.c_void_p()
    if isinstance(value, int):
        return ctypes.c_void_p(value)
    return ctypes.c_void_p(value)


def candidate_support_dirs() -> list[Path]:
    """Return every plausible location of Apple's Mobile Device Support DLLs."""
    roots: list[Path] = []
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    roots.append(Path(program_files) / "Common Files" / "Apple" / "Mobile Device Support")
    roots.append(Path(program_files_x86) / "Common Files" / "Apple" / "Mobile Device Support")
    roots.append(Path(program_files) / "Common Files" / "Apple" / "Apple Application Support")
    roots.append(Path(program_files_x86) / "Common Files" / "Apple" / "Apple Application Support")
    roots.append(Path(program_data) / "Apple" / "Mobile Device Support")
    # Apple Devices / Apple Music from the Microsoft Store keep a copy inside
    # the WindowsApps package directory.
    windows_apps = Path(program_files) / "WindowsApps"
    if windows_apps.is_dir():
        try:
            for entry in sorted(windows_apps.iterdir()):
                name = entry.name.lower()
                if name.startswith(("appleinc.appledevices", "appleinc.itunes", "appleinc.applemusic")):
                    roots.append(entry / "Mobile Device Support")
                    roots.append(entry)
        except OSError:
            pass
    if local_app_data:
        roots.append(Path(local_app_data) / "Apple" / "Mobile Device Support")

    seen: set[str] = set()
    result: list[Path] = []
    for directory in roots:
        try:
            key = str(directory.resolve()).lower()
        except OSError:
            key = str(directory).lower()
        if key in seen:
            continue
        seen.add(key)
        if directory.is_dir():
            result.append(directory)
    return result


def _prime_loader(candidates: list[Path]) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetDllDirectoryW.restype = wintypes.BOOL
    for directory in candidates:
        text = str(directory)
        os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
        kernel32.SetDllDirectoryW(text)
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(text))
        except (AttributeError, OSError):
            pass


def _load(name: str, preferred: Path | None) -> ctypes.WinDLL | None:
    if preferred is not None and (preferred / name).is_file():
        try:
            return ctypes.WinDLL(str(preferred / name))
        except OSError:
            pass
    try:
        return ctypes.WinDLL(name)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Lazy singleton holding the three Apple libraries
# ---------------------------------------------------------------------------

class AppleRuntime:
    """Loads CoreFoundation/MobileDevice/AirTrafficHost exactly once."""

    _instance: "AppleRuntime | None" = None

    def __init__(self) -> None:
        self.support_dir: Path | None = None
        self.cf: ctypes.WinDLL | None = None
        self.md: ctypes.WinDLL | None = None
        self.at: ctypes.WinDLL | None = None
        self.error: str | None = None

    # -- construction -------------------------------------------------
    @classmethod
    def shared(cls) -> "AppleRuntime":
        if cls._instance is None:
            cls._instance = cls()._load_now()
        return cls._instance

    def _load_now(self) -> "AppleRuntime":
        candidates = candidate_support_dirs()
        if not candidates:
            self.error = (
                "未找到 Apple 移动设备支持：请安装 iTunes 或 Apple Devices，"
                "以免其缺少 MobileDevice.dll。"
            )
            return self
        _prime_loader(candidates)

        # CoreFoundation comes first, it backs both other libraries.
        for directory in candidates:
            lib = _load("CoreFoundation.dll", directory)
            if lib is not None and _probe_symbol(lib, "CFStringCreateWithCString"):
                self.cf = lib
                self.support_dir = directory
                break
        if self.cf is None:
            self.cf = _load("CoreFoundation.dll", None)

        self.md = _load("MobileDevice.dll", self.support_dir)
        if self.md is None or not _probe_symbol(self.md, "AMDeviceCopyDeviceIdentifier"):
            self.md = None

        self.at = _load("AirTrafficHost.dll", self.support_dir)
        if self.at is None or not _probe_symbol(self.at, "ATHostConnectionCreate"):
            self.at = None
        return self

    # -- helpers ------------------------------------------------------
    def require(self, name: str) -> ctypes.WinDLL:
        lib = getattr(self, name)
        if lib is None:
            raise AppleSupportNotFoundError(
                self.error or "Apple 移动设备支持组件不可用。"
            )
        return lib


def _probe_symbol(lib: ctypes.WinDLL, name: str) -> bool:
    try:
        getattr(lib, name)
        return True
    except AttributeError:
        return False


class CF:
    """Typed wrappers around the handful of CoreFoundation calls we need."""

    def __init__(self) -> None:
        self.lib = AppleRuntime.shared().require("cf")
        self._bind()
        try:
            self.allocator = ctypes.c_void_p.in_dll(self.lib, "kCFAllocatorDefault")
        except (AttributeError, ValueError):
            self.allocator = None  # NULL behaves as kCFAllocatorDefault

    # ------------------------------------------------------------------
    def _bind(self) -> None:
        lib = self.lib
        v = ctypes.c_void_p

        self.CFRelease = lib.CFRelease
        self.CFRelease.argtypes = [v]
        self.CFRelease.restype = None

        self.CFRetain = lib.CFRetain
        self.CFRetain.argtypes = [v]
        self.CFRetain.restype = v

        self.CFDataCreate = lib.CFDataCreate
        self.CFDataCreate.argtypes = [v, ctypes.c_char_p, ctypes.c_long]
        self.CFDataCreate.restype = v

        # restype 必须是 c_void_p：二进制 plist 含 NUL 字节，若声明为 c_char_p
        # ctypes 会在首个 NUL 处截断，导致所有 plist 解析失败。
        self.CFDataGetBytePtr = lib.CFDataGetBytePtr
        self.CFDataGetBytePtr.argtypes = [v]
        self.CFDataGetBytePtr.restype = v

        self.CFDataGetLength = lib.CFDataGetLength
        self.CFDataGetLength.argtypes = [v]
        self.CFDataGetLength.restype = ctypes.c_long

        self.CFStringCreateWithCString = lib.CFStringCreateWithCString
        self.CFStringCreateWithCString.argtypes = [v, ctypes.c_char_p, ctypes.c_uint32]
        self.CFStringCreateWithCString.restype = v

        self.CFStringGetLength = lib.CFStringGetLength
        self.CFStringGetLength.argtypes = [v]
        self.CFStringGetLength.restype = ctypes.c_long

        self.CFStringGetCString = lib.CFStringGetCString
        self.CFStringGetCString.argtypes = [v, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self.CFStringGetCString.restype = ctypes.c_bool

        self.CFPropertyListCreateWithData = lib.CFPropertyListCreateWithData
        self.CFPropertyListCreateWithData.argtypes = [
            v, v, ctypes.c_ulong, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(v)]
        self.CFPropertyListCreateWithData.restype = v

        self.CFPropertyListCreateData = lib.CFPropertyListCreateData
        self.CFPropertyListCreateData.argtypes = [
            v, v, ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(v)]
        self.CFPropertyListCreateData.restype = v

        self.CFGetTypeID = lib.CFGetTypeID
        self.CFGetTypeID.argtypes = [v]
        self.CFGetTypeID.restype = ctypes.c_ulong

        self.CFStringGetTypeID = lib.CFStringGetTypeID
        self.CFStringGetTypeID.restype = ctypes.c_ulong

        # 集合类型辅助：用于解析 AMDCreateDeviceList 返回的 CFArray
        self.CFDictionaryGetTypeID = getattr(lib, "CFDictionaryGetTypeID", None)
        if self.CFDictionaryGetTypeID is not None:
            self.CFDictionaryGetTypeID.restype = ctypes.c_ulong

        self.CFArrayGetCount = getattr(lib, "CFArrayGetCount", None)
        if self.CFArrayGetCount is not None:
            self.CFArrayGetCount.argtypes = [v]
            self.CFArrayGetCount.restype = ctypes.c_long

        self.CFArrayGetValueAtIndex = getattr(lib, "CFArrayGetValueAtIndex", None)
        if self.CFArrayGetValueAtIndex is not None:
            self.CFArrayGetValueAtIndex.argtypes = [v, ctypes.c_long]
            self.CFArrayGetValueAtIndex.restype = v

        try:
            self.default_runloop_mode = ctypes.c_void_p.in_dll(self.lib, "kCFRunLoopDefaultMode")
        except (AttributeError, ValueError):
            self.default_runloop_mode = None

        self.CFRunLoopRunInMode = getattr(lib, "CFRunLoopRunInMode", None)
        self.CFRunLoopGetCurrent = getattr(lib, "CFRunLoopGetCurrent", None)
        self.CFRunLoopStop = getattr(lib, "CFRunLoopStop", None)
        if self.CFRunLoopRunInMode is not None:
            try:
                self.CFRunLoopRunInMode.argtypes = [v, ctypes.c_double, ctypes.c_bool]
                self.CFRunLoopRunInMode.restype = ctypes.c_int
            except AttributeError:
                self.CFRunLoopRunInMode = None
            self.CFRunLoopGetCurrent.argtypes = []
            self.CFRunLoopGetCurrent.restype = v
            self.CFRunLoopStop.argtypes = [v]
            self.CFRunLoopStop.restype = None

    # ------------------------------------------------------------------
    # TypeRef helpers
    # ------------------------------------------------------------------
    def release(self, ref: Any) -> None:
        if ref:
            try:
                self.CFRelease(as_void_p(ref))
            except (ctypes.ArgumentError, TypeError, ValueError):
                pass

    def retain(self, ref: Any) -> Any:
        if ref:
            try:
                return self.CFRetain(as_void_p(ref))
            except (ctypes.ArgumentError, TypeError, ValueError):
                return ref
        return ref

    def is_string(self, ref: Any) -> bool:
        if not ref:
            return False
        try:
            return self.CFGetTypeID(as_void_p(ref)) == self.CFStringGetTypeID()
        except Exception:
            return False

    def is_dictionary(self, ref: Any) -> bool:
        if not ref or self.CFDictionaryGetTypeID is None:
            return False
        try:
            return self.CFGetTypeID(as_void_p(ref)) == self.CFDictionaryGetTypeID()
        except Exception:
            return False

    def array_items(self, ref: Any) -> list[Any]:
        """从 CFArrayRef 取出元素；返回的是借用引用（数组仍持有所有权）。"""
        if not ref or self.CFArrayGetCount is None or self.CFArrayGetValueAtIndex is None:
            return []
        try:
            count = int(self.CFArrayGetCount(as_void_p(ref)))
        except Exception:
            return []
        items: list[Any] = []
        for index in range(count):
            try:
                item = self.CFArrayGetValueAtIndex(as_void_p(ref), index)
            except Exception:
                item = None
            if item:
                items.append(item)
        return items

    def cf_string(self, text: str) -> Any:
        """Owned CFStringRef built from a Python str."""
        raw = text.encode("utf-8")
        ref = self.CFStringCreateWithCString(self.allocator, raw, kCFStringEncodingUTF8)
        if not ref:
            raise RuntimeError("无法创建 CFString")
        return ref

    def py_string(self, ref: Any) -> str | None:
        """Convert a CFStringRef (borrowed) to str without taking ownership."""
        if not ref or not self.is_string(ref):
            return None
        try:
            length = int(self.CFStringGetLength(as_void_p(ref)))
        except Exception:
            return None
        if length < 0:
            return None
        buffer = ctypes.create_string_buffer(max(16, length * 4 + 16))
        if self.CFStringGetCString(as_void_p(ref), buffer, len(buffer),
                                   kCFStringEncodingUTF8):
            value = buffer.value
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.decode("utf-8", errors="replace")
        return None

    # ------------------------------------------------------------------
    # plist <-> CF conversion
    # ------------------------------------------------------------------
    def cf_data(self, payload: bytes) -> Any:
        ref = self.CFDataCreate(self.allocator, payload, len(payload))
        if not ref:
            raise RuntimeError("无法创建 CFData")
        return ref

    def plist_to_cf(self, obj: Any) -> Any:
        """Owned CFPropertyListRef for any plist-serialisable Python object."""
        raw = plistlib.dumps(obj, fmt=plistlib.FMT_BINARY, sort_keys=True)
        data = self.cf_data(raw)
        try:
            fmt = ctypes.c_int(0)
            error = ctypes.c_void_p()
            ref = self.CFPropertyListCreateWithData(
                self.allocator, as_void_p(data), 0,
                ctypes.byref(fmt), ctypes.byref(error))
            if error.value:
                self.release(error.value)
            if not ref:
                raise RuntimeError(" CoreFoundation 无法解析该属性列表")
            return ref
        finally:
            self.release(data)

    def cf_to_plist(self, ref: Any) -> Any:
        """Python object for a borrowed CFPropertyListRef."""
        if not ref:
            return None
        error = ctypes.c_void_p()
        data = self.CPropertyListCreateDataWrapper(ref, error)
        if not data:
            return None
        try:
            pointer = self.CFDataGetBytePtr(as_void_p(data))
            size = int(self.CFDataGetLength(as_void_p(data)))
            raw = ctypes.string_at(pointer, size) if pointer and size > 0 else b""
        finally:
            self.release(data)
        try:
            return plistlib.loads(raw)
        except Exception:
            return None

    def CPropertyListCreateDataWrapper(self, ref: Any, error: Any) -> Any:
        return self.CFPropertyListCreateData(
            self.allocator, as_void_p(ref),
            kCFPropertyListBinaryFormat_v1_0, 0, ctypes.byref(error))

    # ------------------------------------------------------------------
    def pump(self, seconds: float) -> None:
        """Give CoreFoundation a chance to dispatch pending callbacks."""
        if self.CFRunLoopRunInMode is None or self.default_runloop_mode is None:
            import time
            time.sleep(seconds)
            return
        try:
            self.CFRunLoopRunInMode(
                as_void_p(self.default_runloop_mode), float(seconds), True)
        except Exception:
            import time
            time.sleep(seconds)


_cf_singleton: CF | None = None


def cf_shared() -> CF:
    global _cf_singleton
    if _cf_singleton is None:
        _cf_singleton = CF()
    return _cf_singleton
