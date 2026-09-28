"""Bindings for MobileDevice.dll (AMDevice*) and its Apple File Conduit (AFC).

Every function mirrors the declaration used by the Objective-C device_helper on
macOS, so the Windows port keeps identical semantics.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import POINTER, Structure, c_bool, c_char_p, c_char, c_int, c_long, c_uint, c_uint32, c_void_p, byref, cast, create_string_buffer
from typing import Any, Callable

from .cf import AppleRuntime, as_void_p, cf_shared


class AMDeviceNotificationCallbackInfo(Structure):
    _fields_ = [("device", c_void_p), ("message", c_uint)]


_DEVICE_CALLBACK = ctypes.CFUNCTYPE(None, POINTER(AMDeviceNotificationCallbackInfo), c_void_p)

_DEVICE_CALLBACKS: list[Any] = []


class MobileDeviceError(RuntimeError):
    pass


def _normalize_udid(value: str) -> str:
    """统一 UDID 形式。

    usbmux 报告的 SerialNumber 带连字符（00008030-00054C320AD1402E），
    而 AMDeviceCopyDeviceIdentifier 返回不带连字符的形式，比较前必须归一化。
    """
    return "".join(ch for ch in (value or "") if ch.isalnum()).lower()


class MobileDevice:
    def __init__(self) -> None:
        runtime = AppleRuntime.shared()
        self.lib = runtime.require("md")
        self.cf = cf_shared()
        v = c_void_p
        lib = self.lib

        self.AMDeviceNotificationSubscribeWithOptions = lib.AMDeviceNotificationSubscribeWithOptions
        self.AMDeviceNotificationSubscribeWithOptions.argtypes = [
            _DEVICE_CALLBACK, c_uint, c_uint, c_void_p, POINTER(c_void_p), v]
        self.AMDeviceNotificationSubscribeWithOptions.restype = c_int

        self.AMDeviceNotificationUnsubscribe = lib.AMDeviceNotificationUnsubscribe
        self.AMDeviceNotificationUnsubscribe.argtypes = [v]
        self.AMDeviceNotificationUnsubscribe.restype = c_int

        # Windows 版 MobileDevice.dll 的 AMDeviceNotificationSubscribe* 从不回调，
        # 真正的设备枚举入口是无参的 AMDCreateDeviceList()，直接返回 CFArrayRef。
        self.AMDCreateDeviceList = getattr(lib, "AMDCreateDeviceList", None)
        if self.AMDCreateDeviceList is not None:
            self.AMDCreateDeviceList.argtypes = []
            self.AMDCreateDeviceList.restype = v

        self.AMDeviceCopyDeviceIdentifier = lib.AMDeviceCopyDeviceIdentifier
        self.AMDeviceCopyDeviceIdentifier.argtypes = [v]
        self.AMDeviceCopyDeviceIdentifier.restype = v

        self.AMDeviceCopyValue = lib.AMDeviceCopyValue
        self.AMDeviceCopyValue.argtypes = [v, v, v]
        self.AMDeviceCopyValue.restype = v

        self.AMDeviceConnect = lib.AMDeviceConnect
        self.AMDeviceConnect.argtypes = [v]
        self.AMDeviceConnect.restype = c_int
        self.AMDeviceDisconnect = lib.AMDeviceDisconnect
        self.AMDeviceDisconnect.argtypes = [v]
        self.AMDeviceDisconnect.restype = c_int

        self.AMDeviceIsPaired = lib.AMDeviceIsPaired
        self.AMDeviceIsPaired.argtypes = [v]
        self.AMDeviceIsPaired.restype = c_int
        self.AMDevicePair = lib.AMDevicePair
        self.AMDevicePair.argtypes = [v]
        self.AMDevicePair.restype = c_int
        self.AMDeviceValidatePairing = lib.AMDeviceValidatePairing
        self.AMDeviceValidatePairing.argtypes = [v]
        self.AMDeviceValidatePairing.restype = c_int

        self.AMDeviceStartSession = lib.AMDeviceStartSession
        self.AMDeviceStartSession.argtypes = [v]
        self.AMDeviceStartSession.restype = c_int
        self.AMDeviceStopSession = lib.AMDeviceStopSession
        self.AMDeviceStopSession.argtypes = [v]
        self.AMDeviceStopSession.restype = c_int

        self.AMDeviceSecureStartService = lib.AMDeviceSecureStartService
        self.AMDeviceSecureStartService.argtypes = [v, v, v, POINTER(c_void_p)]
        self.AMDeviceSecureStartService.restype = c_int

        self.AMDServiceConnectionGetSocket = lib.AMDServiceConnectionGetSocket
        self.AMDServiceConnectionGetSocket.argtypes = [v]
        self.AMDServiceConnectionGetSocket.restype = c_int
        self.AMDServiceConnectionGetSecureIOContext = lib.AMDServiceConnectionGetSecureIOContext
        self.AMDServiceConnectionGetSecureIOContext.argtypes = [v]
        self.AMDServiceConnectionGetSecureIOContext.restype = v
        self.AMDServiceConnectionInvalidate = lib.AMDServiceConnectionInvalidate
        self.AMDServiceConnectionInvalidate.argtypes = [v]
        self.AMDServiceConnectionInvalidate.restype = c_int
        self.AMDServiceConnectionSend = lib.AMDServiceConnectionSend
        self.AMDServiceConnectionSend.argtypes = [v, c_void_p, ctypes.c_size_t]
        self.AMDServiceConnectionSend.restype = c_int
        self.AMDServiceConnectionReceive = lib.AMDServiceConnectionReceive
        self.AMDServiceConnectionReceive.argtypes = [v, c_void_p, c_long]
        self.AMDServiceConnectionReceive.restype = c_long
        self.AMDServiceConnectionSendMessage = lib.AMDServiceConnectionSendMessage
        self.AMDServiceConnectionSendMessage.argtypes = [v, v, c_int]
        self.AMDServiceConnectionSendMessage.restype = c_int
        self.AMDServiceConnectionReceiveMessage = lib.AMDServiceConnectionReceiveMessage
        self.AMDServiceConnectionReceiveMessage.argtypes = [v, POINTER(c_void_p), POINTER(c_int)]
        self.AMDServiceConnectionReceiveMessage.restype = c_int

        self._bind_afc(lib)

    def _bind_afc(self, lib: Any) -> None:
        v = c_void_p
        self.AFCConnectionOpen = lib.AFCConnectionOpen
        self.AFCConnectionOpen.argtypes = [c_int, c_uint, POINTER(c_void_p)]
        self.AFCConnectionOpen.restype = c_int
        self.AFCConnectionClose = lib.AFCConnectionClose
        self.AFCConnectionClose.argtypes = [v]
        self.AFCConnectionClose.restype = c_int
        self.AFCConnectionSetSecureContext = lib.AFCConnectionSetSecureContext
        self.AFCConnectionSetSecureContext.argtypes = [v, v]
        self.AFCConnectionSetSecureContext.restype = c_int
        self.AFCConnectionSetDisposeSecureContextOnInvalidate = getattr(
            lib, "AFCConnectionSetDisposeSecureContextOnInvalidate", None)
        if self.AFCConnectionSetDisposeSecureContextOnInvalidate is not None:
            self.AFCConnectionSetDisposeSecureContextOnInvalidate.argtypes = [v, c_int]
            self.AFCConnectionSetDisposeSecureContextOnInvalidate.restype = c_int
        self.AFCConnectionSetIOTimeout = lib.AFCConnectionSetIOTimeout
        self.AFCConnectionSetIOTimeout.argtypes = [v, c_uint]
        self.AFCConnectionSetIOTimeout.restype = c_int

        self.AFCFileInfoOpen = lib.AFCFileInfoOpen
        self.AFCFileInfoOpen.argtypes = [v, c_char_p, POINTER(c_void_p)]
        self.AFCFileInfoOpen.restype = c_int
        self.AFCKeyValueRead = lib.AFCKeyValueRead
        self.AFCKeyValueRead.argtypes = [v, POINTER(c_char_p), POINTER(c_char_p)]
        self.AFCKeyValueRead.restype = c_int
        self.AFCKeyValueClose = lib.AFCKeyValueClose
        self.AFCKeyValueClose.argtypes = [v]
        self.AFCKeyValueClose.restype = c_int

        self.AFCFileRefOpen = lib.AFCFileRefOpen
        self.AFCFileRefOpen.argtypes = [v, c_char_p, ctypes.c_ulonglong, POINTER(c_void_p)]
        self.AFCFileRefOpen.restype = c_int
        self.AFCFileRefRead = lib.AFCFileRefRead
        self.AFCFileRefRead.argtypes = [v, v, c_void_p, POINTER(c_uint32)]
        self.AFCFileRefRead.restype = c_int
        self.AFCFileRefWrite = lib.AFCFileRefWrite
        self.AFCFileRefWrite.argtypes = [v, v, c_void_p, c_long]
        self.AFCFileRefWrite.restype = c_int
        self.AFCFileRefClose = lib.AFCFileRefClose
        self.AFCFileRefClose.argtypes = [v, v]
        self.AFCFileRefClose.restype = c_int

        self.AFCDirectoryOpen = lib.AFCDirectoryOpen
        self.AFCDirectoryOpen.argtypes = [v, c_char_p, POINTER(c_void_p)]
        self.AFCDirectoryOpen.restype = c_int
        self.AFCDirectoryRead = lib.AFCDirectoryRead
        self.AFCDirectoryRead.argtypes = [v, v, POINTER(c_char_p)]
        self.AFCDirectoryRead.restype = c_int
        self.AFCDirectoryClose = lib.AFCDirectoryClose
        self.AFCDirectoryClose.argtypes = [v, v]
        self.AFCDirectoryClose.restype = c_int
        self.AFCDirectoryCreate = lib.AFCDirectoryCreate
        self.AFCDirectoryCreate.argtypes = [v, c_char_p]
        self.AFCDirectoryCreate.restype = c_int
        self.AFCRemovePath = lib.AFCRemovePath
        self.AFCRemovePath.argtypes = [v, c_char_p]
        self.AFCRemovePath.restype = c_int

    # ------------------------------------------------------------------
    # Device discovery
    # ------------------------------------------------------------------
    def subscription_options(self, direct_connections_only: bool) -> Any:
        return self.cf.plist_to_cf({
            "NotificationOptionSearchForPairedDevices": True,
            "NotificationOptionSearchForPairedDevicesViaDirectConnectionsOnly":
                bool(direct_connections_only),
            "NotificationOptionSearchForWiFiPairableDevices": False,
            "NotificationOptionEnableRemoteXPC": True,
            "NotificationOptionEnableUSBMux": True,
        })

    def _subscribe(self, callback: Any, direct_only: bool) -> tuple[int, c_void_p]:
        options = self.subscription_options(direct_only)
        subscription = c_void_p()
        try:
            status = self.AMDeviceNotificationSubscribeWithOptions(
                callback, 0, 0, None, byref(subscription), as_void_p(options))
        finally:
            self.cf.release(options)
        return status, subscription

    def _unsubscribe(self, subscription: c_void_p | None) -> None:
        if subscription and subscription.value:
            try:
                self.AMDeviceNotificationUnsubscribe(as_void_p(subscription.value))
            except Exception:
                pass

    def device_list(self) -> list[Any]:
        """Windows 上唯一可靠的设备枚举入口。

        `AMDCreateDeviceList()` 无参数、直接返回 CFArrayRef。数组元素通常是
        AMDeviceRef（会被 retain 后返回，调用方负责 release）；少数版本返回
        描述设备的 CFDictionary，此时转为 Python dict 返回。
        """
        fn = self.AMDCreateDeviceList
        if fn is None:
            return []
        try:
            array = fn()
        except Exception:
            return []
        if not array:
            return []
        try:
            handled: list[Any] = []
            for item in self.cf.array_items(array):
                if self.cf.is_dictionary(item):
                    info = self.cf.cf_to_plist(item)
                    handled.append(info if isinstance(info, dict) else {})
                else:
                    self.cf.retain(item)
                    handled.append(item)
            return handled
        finally:
            self.cf.release(array)

    def identifier_of(self, item: Any) -> str:
        if isinstance(item, dict):
            for key in ("SerialNumber", "UDID", "UniqueDeviceID", "DeviceID"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    return value
            return ""
        try:
            identifier = self.AMDeviceCopyDeviceIdentifier(as_void_p(item))
        except Exception:
            return ""
        if not identifier:
            return ""
        udid = self.cf.py_string(identifier)
        self.cf.release(identifier)
        return udid or ""

    def _describe_device(self, device: Any) -> dict[str, Any]:
        """连接设备并读取名称/型号/语言等；失败时只返回 udid。"""
        entry: dict[str, Any] = {}
        handle = as_void_p(device)
        if self.AMDeviceConnect(handle) != 0:
            return entry
        try:
            if self.AMDeviceIsPaired(handle) == 0:
                self.AMDevicePair(handle)
            # 首次校验常因 iPhone 侧信任弹窗待响应而失败，配对后重试一次。
            if self.AMDeviceValidatePairing(handle) != 0:
                self.AMDevicePair(handle)
            if self.AMDeviceValidatePairing(handle) == 0 and \
                    self.AMDeviceStartSession(handle) == 0:
                for field, key in (
                        ("name", "DeviceName"),
                        ("version", "ProductVersion"),
                        ("product", "ProductType"),
                        ("buildVersion", "BuildVersion")):
                    entry[field] = self._copy_value(handle, None, key, "")
                entry["language"] = self._copy_value(
                    handle, "com.apple.international", "Language", "en")
                entry["locale"] = self._copy_value(
                    handle, "com.apple.international", "Locale", "")
                bold = self._copy_value_raw(
                    handle, "com.apple.Accessibility", "EnhancedTextLegibility")
                if bold is not None:
                    entry["bold_text"] = bool(bold)
                self.AMDeviceStopSession(handle)
        finally:
            self.AMDeviceDisconnect(handle)
        return entry

    def _describe_from_dict(self, info: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {}
        for field, key in (
                ("name", "DeviceName"),
                ("version", "ProductVersion"),
                ("product", "ProductType"),
                ("buildVersion", "BuildVersion"),
                ("language", "Language"),
                ("locale", "Locale")):
            value = info.get(key)
            if value is not None:
                entry[field] = str(value)
        return entry

    def probe_device(self, item: Any) -> dict[str, Any]:
        """对单个设备跑完整握手流程，逐步记录状态码。用于精确诊断。"""
        detail: dict[str, Any] = {
            "udid": self.identifier_of(item),
            "connect": None, "paired": None, "pair": None,
            "validate": None, "session": None,
            "name": "", "product": "", "version": "",
        }
        if isinstance(item, dict):
            detail.update(self._describe_from_dict(item))
            return detail

        handle = as_void_p(item)
        try:
            detail["connect"] = self.AMDeviceConnect(handle)
            if detail["connect"] != 0:
                return detail
            try:
                detail["paired"] = self.AMDeviceIsPaired(handle)
                if detail["paired"] == 0:
                    detail["pair"] = self.AMDevicePair(handle)
                detail["validate"] = self.AMDeviceValidatePairing(handle)
                if detail["validate"] != 0:
                    return detail
                detail["session"] = self.AMDeviceStartSession(handle)
                if detail["session"] != 0:
                    return detail
                try:
                    detail["name"] = self._copy_value(handle, None, "DeviceName", "")
                    detail["product"] = self._copy_value(handle, None, "ProductType", "")
                    detail["version"] = self._copy_value(handle, None, "ProductVersion", "")
                finally:
                    self.AMDeviceStopSession(handle)
            finally:
                self.AMDeviceDisconnect(handle)
        except Exception as exc:  # noqa: BLE001
            detail["exception"] = str(exc)
        return detail

    def enumerate_devices(self, timeout: float = 2.0) -> list[dict[str, Any]]:
        """Equivalent of `device_helper list`."""
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in self.device_list():
            udid = self.identifier_of(item)
            if not udid or udid in seen:
                continue
            seen.add(udid)
            entry: dict[str, Any] = {"udid": udid}
            try:
                if isinstance(item, dict):
                    entry.update(self._describe_from_dict(item))
                else:
                    entry.update(self._describe_device(item))
            except Exception:
                pass
            finally:
                if not isinstance(item, dict):
                    self.cf.release(item)
            found.append(entry)

        if found:
            return found
        # 兜底：个别环境下通知回调仍可能可用
        return self._enumerate_via_notification(timeout)

    def _enumerate_via_notification(self, timeout: float) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        def handler(info: Any, context: Any) -> None:
            try:
                info = info.contents
            except Exception:
                return
            if not info.device or info.message != 1:
                return
            identifier = self.AMDeviceCopyDeviceIdentifier(as_void_p(info.device))
            if not identifier:
                return
            udid = self.cf.py_string(identifier)
            self.cf.release(identifier)
            if not udid or udid in seen:
                return
            seen.add(udid)
            entry: dict[str, Any] = {"udid": udid}
            entry.update(self._describe_device(as_void_p(info.device)))
            found.append(entry)

        callback = _DEVICE_CALLBACK(handler)
        _DEVICE_CALLBACKS.append(callback)
        for direct_only in (False, True):
            status, subscription = self._subscribe(callback, direct_only)
            try:
                if status == 0:
                    deadline = time.monotonic() + max(0.5, timeout)
                    while time.monotonic() < deadline and not found:
                        self.cf.pump(0.2)
            finally:
                self._unsubscribe(subscription)
            if found:
                break
        return found

    def find_target(self, udid: str, timeout: float = 30.0) -> Any | None:
        """Blocks until the requested UDID shows up. Returns a retained device."""
        # 首选：轮询 AMDCreateDeviceList（Windows 上唯一可靠的路径）
        wanted = _normalize_udid(udid)
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            for item in self.device_list():
                try:
                    matched = (not isinstance(item, dict)
                               and _normalize_udid(self.identifier_of(item)) == wanted)
                except Exception:
                    matched = False
                if matched:
                    return item
                if not isinstance(item, dict):
                    self.cf.release(item)
            time.sleep(0.4)
        return self._find_target_via_notification(udid, min(timeout, 8.0))

    def _find_target_via_notification(self, udid: str, timeout: float) -> Any | None:
        holder: dict[str, Any] = {"device": None}
        target = self.cf.cf_string(udid)

        def handler(info: Any, context: Any) -> None:
            try:
                info = info.contents
            except Exception:
                return
            if holder["device"] is not None:
                return
            if not info.device or info.message != 1:
                return
            identifier = self.AMDeviceCopyDeviceIdentifier(as_void_p(info.device))
            if not identifier:
                return
            text = self.cf.py_string(identifier)
            self.cf.release(identifier)
            if text == udid:
                holder["device"] = self.cf.CFRetain(as_void_p(info.device))

        callback = _DEVICE_CALLBACK(handler)
        _DEVICE_CALLBACKS.append(callback)
        status, subscription = self._subscribe(callback, False)
        try:
            if status == 0:
                deadline = time.monotonic() + max(1.0, timeout)
                while holder["device"] is None and time.monotonic() < deadline:
                    self.cf.pump(0.2)
        finally:
            self._unsubscribe(subscription)
            self.cf.release(target)
        return holder["device"]

    # ------------------------------------------------------------------
    # Value copy helpers
    # ------------------------------------------------------------------
    def _copy_value_raw(self, device: Any, domain: str | None, key: str) -> Any:
        cf_key = self.cf.cf_string(key)
        cf_domain = self.cf.cf_string(domain) if domain else None
        try:
            ref = self.AMDeviceCopyValue(
                as_void_p(device),
                as_void_p(cf_domain) if cf_domain else None,
                as_void_p(cf_key))
            if not ref:
                return None
            try:
                return self.cf.cf_to_plist(ref)
            finally:
                self.cf.release(ref)
        finally:
            self.cf.release(cf_key)
            if cf_domain:
                self.cf.release(cf_domain)

    def _copy_value(self, device: Any, domain: str | None, key: str, fallback: str) -> str:
        value = self._copy_value_raw(device, domain, key)
        if value is None:
            return fallback
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (bytes, bytearray)):
            try:
                return bytes(value).decode("utf-8", errors="replace")
            except Exception:
                return fallback
        if isinstance(value, str):
            return value
        # CFNumber -> plist number
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return fallback


_md_singleton: MobileDevice | None = None


def md_shared() -> MobileDevice:
    global _md_singleton
    if _md_singleton is None:
        _md_singleton = MobileDevice()
    return _md_singleton
