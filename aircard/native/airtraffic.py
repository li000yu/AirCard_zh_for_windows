"""Bindings for AirTrafficHost.dll plus the high level sync pipeline.

This replaces the macOS-only helper binary `airtraffic_host`. The DLL exposes
the same C entry points iTunes uses on Windows, so the sync sequence - and the
resulting sandbox escape - is byte-for-byte identical to the macOS build.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
读到本文件的人请注意（真机踩过、代价很大的坑）：

`ATHostConnectionReadMessage` 在 Windows 上是**阻塞调用** —— 没有消息时它不会
立刻返回 NULL，而是一直等到设备说话为止。

老实现每次读取都另起一个线程、超时（探测 1.5s / 同步 8s）就丢弃结果再重开一次。
后果：设备稍晚一点回的消息全部落到被抛弃的线程里，主线程永远读到 0 条 ——
真机表现就是「AirTraffic 连接：已建立 / SyncAllowed：否 / 握手期间收到 0 条消息」。

现在改成 `_MessagePump`：一条常驻线程持续保持一个阻塞读在飞，收到就塞进队列，
主线程按 deadline 从队列取。消息一条都不会漏，也不再产生孤儿线程。
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
"""
from __future__ import annotations

import ctypes
import queue
import threading
import time
from typing import Any, Callable

from .cf import AppleRuntime, as_void_p, cf_shared

ATH_CANARY_TOTAL_CAP = 8          # 兼容旧引用：现在只用于限制被遗弃的读取线程
_SYNC_DEADLINE = 45.0             # 等 SyncAllowed / ReadyForSync / 清单的总时限
_SYNC_PHASE_DEADLINE = 25.0       # 单个阶段的时限
_PROBE_DEADLINE = 12.0            # 诊断时每个 UDID 候选的等待时限
_PUMP_POLL = 0.25                 # 主线程从队列取消息的轮询间隔
_PUMP_GRACE = 3.0                 # 收尾时等读取线程退出的宽限时间


class AirTrafficError(RuntimeError):
    pass


class AirTraffic:
    def __init__(self) -> None:
        runtime = AppleRuntime.shared()
        self.lib = runtime.require("at")
        self.cf = cf_shared()
        lib = self.lib
        v = ctypes.c_void_p

        self.ATHostConnectionCreate = lib.ATHostConnectionCreate
        self.ATHostConnectionCreate.argtypes = [v]
        self.ATHostConnectionCreate.restype = v

        self.ATHostConnectionRelease = lib.ATHostConnectionRelease
        self.ATHostConnectionRelease.argtypes = [v]
        self.ATHostConnectionRelease.restype = None

        self.ATHostConnectionReadMessage = lib.ATHostConnectionReadMessage
        self.ATHostConnectionReadMessage.argtypes = [v]
        self.ATHostConnectionReadMessage.restype = v

        self.ATCFMessageGetName = lib.ATCFMessageGetName
        self.ATCFMessageGetName.argtypes = [v]
        self.ATCFMessageGetName.restype = v

        self.ATCFMessageGetParam = lib.ATCFMessageGetParam
        self.ATCFMessageGetParam.argtypes = [v, v]
        self.ATCFMessageGetParam.restype = v

        self.ATHostConnectionSendHostInfo = lib.ATHostConnectionSendHostInfo
        self.ATHostConnectionSendHostInfo.argtypes = [v, v]
        self.ATHostConnectionSendHostInfo.restype = None

        self.ATHostConnectionSendSyncRequest = lib.ATHostConnectionSendSyncRequest
        self.ATHostConnectionSendSyncRequest.argtypes = [v, v, v, v]
        self.ATHostConnectionSendSyncRequest.restype = None

        self.ATHostConnectionSendMetadataSyncFinished = \
            lib.ATHostConnectionSendMetadataSyncFinished
        self.ATHostConnectionSendMetadataSyncFinished.argtypes = [v, v, v]
        self.ATHostConnectionSendMetadataSyncFinished.restype = None

        self.ATHostConnectionSendAssetCompleted = lib.ATHostConnectionSendAssetCompleted
        self.ATHostConnectionSendAssetCompleted.argtypes = [v, v, v, v]
        self.ATHostConnectionSendAssetCompleted.restype = None

        self.ATHostConnectionInvalidate = getattr(lib, "ATHostConnectionInvalidate", None)
        if self.ATHostConnectionInvalidate is not None:
            self.ATHostConnectionInvalidate.argtypes = [v]
            self.ATHostConnectionInvalidate.restype = None

        # 诊断用的补充信息：会话号与 grappa 会话 id 能直接看出通道有没有真正起会话
        self.ATHostConnectionGetCurrentSessionNumber = getattr(
            lib, "ATHostConnectionGetCurrentSessionNumber", None)
        if self.ATHostConnectionGetCurrentSessionNumber is not None:
            self.ATHostConnectionGetCurrentSessionNumber.argtypes = [v]
            self.ATHostConnectionGetCurrentSessionNumber.restype = ctypes.c_longlong

        self.ATHostConnectionGetGrappaSessionId = getattr(
            lib, "ATHostConnectionGetGrappaSessionId", None)
        if self.ATHostConnectionGetGrappaSessionId is not None:
            self.ATHostConnectionGetGrappaSessionId.argtypes = [v]
            self.ATHostConnectionGetGrappaSessionId.restype = ctypes.c_void_p

    # ------------------------------------------------------------------
    def message_name(self, message: Any) -> str | None:
        if not message:
            return None
        name = self.ATCFMessageGetName(as_void_p(message))
        return self.cf.py_string(name)

    def message_param(self, message: Any, key: str) -> Any:
        cf_key = self.cf.cf_string(key)
        try:
            ref = self.ATCFMessageGetParam(
                as_void_p(message), as_void_p(cf_key))
            if not ref:
                return None
            return self.cf.cf_to_plist(ref)
        finally:
            self.cf.release(cf_key)

    def message_params(self, message: Any, limit: int = 200) -> str:
        """把整条 ATC 消息转成可读摘要，仅用于握手诊断。

        `message_param` 只能按 key 取值，但排障时我们需要知道设备到底发了什么；
        整条转成 plist 后连未知字段名也能看见。
        """
        if not message:
            return ""
        try:
            text = repr(self.cf.cf_to_plist(message))
        except Exception:  # noqa: BLE001
            return ""
        return text if len(text) <= limit else text[:limit] + "…"

    def session_number(self, connection: Any) -> int | None:
        fn = self.ATHostConnectionGetCurrentSessionNumber
        if fn is None:
            return None
        try:
            return int(fn(as_void_p(connection)))
        except Exception:  # noqa: BLE001
            return None

    def grappa_session_id(self, connection: Any) -> str:
        fn = self.ATHostConnectionGetGrappaSessionId
        if fn is None:
            return ""
        try:
            ref = fn(as_void_p(connection))
        except Exception:  # noqa: BLE001
            return ""
        if not ref:
            return ""
        text = self.cf.py_string(ref) or ""
        return text or "（非空但读不出内容）"

    def open(self, udid: str) -> Any:
        """`ATHostConnectionCreate`，返回一个已 retain 的连接（失败返回 0）。"""
        cf = self.cf
        identifier = cf.cf_string(udid)
        try:
            return self.ATHostConnectionCreate(as_void_p(identifier))
        finally:
            cf.release(identifier)

    def connection_state(self, connection: Any) -> dict[str, Any]:
        """诊断用：会话号 / grappa 会话 id，能看出通道到底有没有起会话。"""
        return {
            "session": self.session_number(connection),
            "grappa": self.grappa_session_id(connection),
        }


# ---------------------------------------------------------------------------
# 常驻读取线程
# ---------------------------------------------------------------------------

class _MessagePump:
    """把阻塞的 `ATHostConnectionReadMessage` 变成可超时取用的队列。

    为什么必须有它：Windows 版这个调用是阻塞的。老实现超时就弃线程重开，
    设备晚到的消息全部丢失（真机表现：握手 0 条消息）。这里让一条线程始终
    保持一个阻塞读在飞，收到的消息进队列，主线程按 deadline 取即可。
    """

    def __init__(self, at: AirTraffic, connection: Any) -> None:
        self._at = at
        self._conn = connection
        self._queue: "queue.Queue[int]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run,
                                        name="atc-reader", daemon=True)
        self._reads = 0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ref = self._at.ATHostConnectionReadMessage(as_void_p(self._conn))
            except Exception:  # noqa: BLE001
                ref = 0
            self._reads += 1
            if ref:
                value = ref.value if isinstance(ref, ctypes.c_void_p) else int(ref)
                if value:
                    self._queue.put(value)
                    continue
            # 非阻塞语义下（或连接已失效）短暂休眠后继续，避免空转烧 CPU
            if self._stop.wait(0.05):
                break

    def get(self, timeout: float = _PUMP_POLL) -> int:
        try:
            return self._queue.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return 0

    def stop(self, grace: float = _PUMP_GRACE) -> bool:
        """请求停止并等待读取线程退出；返回是否真的退出了。"""
        self._stop.set()
        self._thread.join(max(0.1, grace))
        return not self._thread.is_alive()


_at_singleton: AirTraffic | None = None


def at_shared() -> AirTraffic:
    global _at_singleton
    if _at_singleton is None:
        _at_singleton = AirTraffic()
    return _at_singleton


# ---------------------------------------------------------------------------
# UDID 候选：usbmux 带连字符、AMDeviceCopyDeviceIdentifier 可能不带
# ---------------------------------------------------------------------------

def candidate_identifiers(udid: str) -> list[str]:
    """返回应该尝试的设备标识序列（去重、保序）。

    iPhone 的 UDID 有两种写法：`00008130-001221242EEA001C`（usbmux 报告）与
    `00008130121242EEA001C`（部分 AMDevice 接口）。AirTrafficHost 内部按标识
    在「已连接设备表」里找设备，找错了就一条消息都不会发 —— 所以两种都试。
    """
    raw = (udid or "").strip()
    if not raw:
        return []
    out = [raw]
    stripped = raw.replace("-", "")
    if stripped and stripped not in out:
        out.append(stripped)
    if len(stripped) == 24 and "-" not in raw:
        dashed = stripped[:8] + "-" + stripped[8:]
        if dashed not in out:
            out.append(dashed)
    return out


# 记住哪个标识真的拿到过 SyncAllowed，下次直接用（省一轮几十秒的等待）
_IDENTIFIER_CACHE: dict[str, str] = {}


def _ordered_identifiers(udid: str) -> list[str]:
    key = (udid or "").strip().replace("-", "").lower()
    ordered = list(candidate_identifiers(udid))
    remembered = _IDENTIFIER_CACHE.get(key)
    if remembered and remembered in ordered and ordered[0] != remembered:
        ordered.remove(remembered)
        ordered.insert(0, remembered)
    elif remembered and remembered not in ordered:
        ordered.insert(0, remembered)
    return ordered


def _remember_identifier(udid: str, identifier: str) -> None:
    key = (udid or "").strip().replace("-", "").lower()
    if key and identifier:
        _IDENTIFIER_CACHE[key] = identifier


# ---------------------------------------------------------------------------
# 通用取消息循环
# ---------------------------------------------------------------------------

def _pump_until(at: AirTraffic, pump: _MessagePump, deadline: float,
                handler: Callable[[Any, str], bool],
                logger: Callable[[str], None] | None = None,
                prefix: str = "") -> tuple[bool, list[str]]:
    """从常驻读取线程取消息，直到 `handler` 返回 True 或超时。

    返回 (是否命中, 期间收到的全部消息摘要)。消息摘要会带参数，便于排障时
    看清设备到底回了什么。
    """
    cf = at.cf
    seen: list[str] = []
    end = time.monotonic() + max(0.1, deadline)
    while time.monotonic() < end:
        ref = pump.get(_PUMP_POLL)
        if not ref:
            continue
        stop = False
        try:
            name = at.message_name(ref) or ""
            label = name or "<无名消息>"
            params = at.message_params(ref)
            if params:
                label += f" {params}"
            seen.append(label)
            if logger is not None:
                try:
                    logger(f"ATC{prefix} 收到：{label[:200]}")
                except Exception:  # noqa: BLE001
                    pass
            stop = bool(handler(ref, name))
        except Exception as exc:  # noqa: BLE001
            seen.append(f"<解析失败：{exc!r}>")
        finally:
            cf.release(ref)
        if stop:
            return True, seen
    return False, seen


# ---------------------------------------------------------------------------
# High level sync pipeline (equivalent of airtraffic_host.m main())
# ---------------------------------------------------------------------------

def _host_info() -> dict[str, Any]:
    import uuid
    import platform

    return {
        "Type": "iTunes",
        "Version": "13.7.0.161",
        "MacOSVersion": platform.platform(),
        "SyncHostName": "airlift",
        "LibraryID": str(uuid.uuid4()).upper(),
        "SyncedDataclasses": ["Book"],
        "SyncedAssetTypes": ["Book"],
        "Wakeable": False,
    }


def _manifest_contains(manifest: Any, identifier: str) -> bool:
    if not isinstance(manifest, dict):
        return False
    books = manifest.get("Book")
    if not isinstance(books, (list, tuple)):
        return False
    for entry in books:
        if isinstance(entry, dict) and entry.get("AssetID") == identifier \
                and bool(entry.get("IsDownload")):
            return True
    return False


def probe_sync_handshake(udid: str, deadline: float = _PROBE_DEADLINE,
                         max_candidates: int = 2) -> dict[str, Any]:
    """只做 ATC 握手，不同步任何资源 —— 用于快速判断 airlift 通道是否可用。

    换卡面 / 读卡面 / 写主题全都依赖这条通道；拿不到 SyncAllowed 时它们会以
    完全相同的错误失败。这个函数让「连接诊断」能直接回答"通道通不通"。

    会按顺序尝试多种 UDID 写法（带/不带连字符），因为 AirTrafficHost 内部按
    标识查设备，写法不对就一条消息都收不到。
    """
    result: dict[str, Any] = {
        "connected": False, "messages": [], "sync_allowed": False,
        "error": "", "identifier": "", "candidates": [], "attempts": [],
        "session": None, "grappa": "",
    }
    try:
        at = at_shared()
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"AirTrafficHost.dll 不可用：{exc}"
        return result

    candidates = _ordered_identifiers(udid)[:max(1, max_candidates)]
    result["candidates"] = candidates
    if not candidates:
        result["error"] = "缺少设备 UDID"
        return result

    for identifier in candidates:
        attempt: dict[str, Any] = {"identifier": identifier, "connected": False,
                                   "messages": 0, "error": ""}
        result["attempts"].append(attempt)
        try:
            connection = at.open(identifier)
        except Exception as exc:  # noqa: BLE001
            attempt["error"] = f"创建连接异常：{exc!r}"
            continue
        if not connection:
            attempt["error"] = "ATHostConnectionCreate 返回空"
            continue

        connection = ctypes.c_void_p(connection)
        attempt["connected"] = True
        result["connected"] = True
        result["identifier"] = identifier
        result["state"] = at.connection_state(connection)

        pump = _MessagePump(at, connection)
        pump.start()
        try:
            found, seen = _pump_until(
                at, pump, deadline, lambda ref, name: name == "SyncAllowed")
        except Exception as exc:  # noqa: BLE001
            attempt["error"] = f"读取握手消息异常：{exc!r}"
            seen = []
        finally:
            stopped = pump.stop()
            if stopped:
                try:
                    at.ATHostConnectionRelease(connection)
                except Exception:  # noqa: BLE001
                    pass

        attempt["messages"] = len(seen)
        result["messages"] = seen
        if found:
            result["sync_allowed"] = True
            _remember_identifier(udid, identifier)
            return result
        if not attempt["error"]:
            attempt["error"] = f"等待 {deadline:.0f}s 未收到任何 SyncAllowed"
        result["messages"] = seen

    if not result["connected"]:
        result["error"] = "所有 UDID 写法都无法建立 AirTraffic 连接"
    return result


def run_airtraffic_sync(
    udid: str,
    assets: list[tuple[str, str]],
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    logger: Callable[[str], None] | None = None,
    deadline: float = _SYNC_PHASE_DEADLINE,
) -> dict[str, Any]:
    """Relocate the given asset identifiers to the supplied destinations.

    `assets` is a list of (identifier, destination) pairs exactly like the argv
    pairs consumed by the macOS helper.
    """
    at = at_shared()
    cf = at.cf

    def fail(code: int, reason: str, **extra: Any) -> dict[str, Any]:
        payload = {"ok": False, "error": reason}
        payload.update(extra)
        payload["exitCode"] = code
        return payload

    if not udid:
        return fail(64, "empty device identifier")
    for identifier, destination in assets:
        if not identifier or not destination:
            return fail(64, "empty argument")

    candidates = _ordered_identifiers(udid)
    connection: Any = 0
    used_identifier = ""
    open_error = ""
    for candidate in candidates:
        try:
            connection = at.open(candidate)
        except Exception as exc:  # noqa: BLE001
            open_error = f"{exc!r}"
            continue
        if connection:
            used_identifier = candidate
            break
        open_error = "ATHostConnectionCreate 返回空"
    if not connection:
        return fail(2, "AirTraffic 连接失败", detail=open_error)

    connection = ctypes.c_void_p(connection)
    pump = _MessagePump(at, connection)
    pump.start()
    handshake: list[str] = []
    state = at.connection_state(connection)

    try:
        # 1. Wait for SyncAllowed
        # 真机实测：Windows 上 ReadMessage 阻塞，老实现超时就丢弃结果，导致永远
        # 读不到消息。现在由常驻线程持续读，这里只管按 deadline 取。
        sync_allowed, handshake = _pump_until(
            at, pump, deadline, lambda ref, name: name == "SyncAllowed",
            logger, "·握手")
        if not sync_allowed:
            return fail(3, "未收到 SyncAllowed", handshake=handshake,
                        state=state, identifier=used_identifier,
                        hint=("设备没有在 "
                              f"{deadline:.0f}s 内授予同步许可，airlift 通道未打通。\n"
                              "这不是卡片的问题 —— 换卡面、读卡面、写主题全都走"
                              "这条通道，现在它们都会以同样的方式失败。"))
        _remember_identifier(udid, used_identifier)

        host_info = _host_info()
        cf_host_info = cf.plist_to_cf(host_info)
        try:
            at.ATHostConnectionSendHostInfo(connection, as_void_p(cf_host_info))
            time.sleep(0.2)
            cf_dataclasses = cf.plist_to_cf(["Book"])
            cf_anchors = cf.plist_to_cf({})
            try:
                at.ATHostConnectionSendSyncRequest(
                    connection,
                    as_void_p(cf_dataclasses),
                    as_void_p(cf_anchors),
                    as_void_p(cf_host_info))
            finally:
                cf.release(cf_dataclasses)
                cf.release(cf_anchors)
        finally:
            cf.release(cf_host_info)

        # 2. Wait for ReadyForSync
        ready, seen = _pump_until(
            at, pump, deadline, lambda ref, name: name == "ReadyForSync",
            logger, "·就绪")
        handshake.extend(seen)
        if not ready:
            return fail(4, "未收到 ReadyForSync", handshake=handshake,
                        state=state, identifier=used_identifier)

        cf_sync_types = cf.plist_to_cf({"Book": 1})
        cf_anchors = cf.plist_to_cf({})
        try:
            at.ATHostConnectionSendMetadataSyncFinished(
                connection, as_void_p(cf_sync_types), as_void_p(cf_anchors))
        finally:
            cf.release(cf_sync_types)
            cf.release(cf_anchors)

        # 3. Collect the asset manifest
        manifest_box: dict[str, Any] = {}

        def take_manifest(ref: Any, name: str) -> bool:
            if name == "AssetManifest":
                value = at.message_param(ref, "AssetManifest")
                if isinstance(value, dict):
                    manifest_box["manifest"] = value
                    return True
            return name in ("SyncFailed", "SyncFinished")

        got_manifest, seen = _pump_until(at, pump, deadline, take_manifest,
                                         logger, "·清单")
        handshake.extend(seen)
        manifest = manifest_box.get("manifest")
        if manifest is None:
            return fail(5, "未收到 AssetManifest", handshake=handshake,
                        state=state, identifier=used_identifier)

        missing = sum(
            0 if _manifest_contains(manifest, identifier) else 1
            for identifier, _ in assets)
        if missing:
            return fail(5, "清单中缺少预期的资源", missingCount=missing,
                        handshake=handshake, state=state,
                        identifier=used_identifier)

        # 4. Complete each asset so AirTraffic moves it into place
        for index, (identifier, destination) in enumerate(assets):
            cf_identifier = cf.cf_string(identifier)
            cf_dataclass = cf.cf_string("Book")
            cf_destination = cf.cf_string(destination)
            try:
                at.ATHostConnectionSendAssetCompleted(
                    connection,
                    as_void_p(cf_identifier),
                    as_void_p(cf_dataclass),
                    as_void_p(cf_destination))
            finally:
                cf.release(cf_identifier)
                cf.release(cf_dataclass)
                cf.release(cf_destination)

            if index > 0:
                leaf = destination.rsplit("/", 1)[-1]
                if on_progress is not None:
                    on_progress({
                        "type": "atc_progress",
                        "index": index,
                        "total": max(1, len(assets) - 1),
                        "leaf": leaf,
                    })
            if logger is not None and index > 0:
                logger(f"已派发资源 {index}/{len(assets) - 1}: "
                       f"{destination.rsplit('/', 1)[-1]}")

            if index + 1 < len(assets):
                time.sleep(0.4 if index == 0 else 0.06)

        time.sleep(2)
    finally:
        # 读取线程可能仍阻塞在 ReadMessage 里；给一段宽限时间让它自然退出。
        # 即便没退出也要 Release —— 与 macOS 原版保持一致，且 DLL 内部另有引用，
        # Release 只是减引用计数，不会把还被读取线程持有的对象直接释放掉。
        pump.stop()
        try:
            at.ATHostConnectionRelease(connection)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "syncAllowed": True,
        "readyForSync": True,
        "fileCompleteMessages": len(assets),
        "exitCode": 0,
        "handshake": handshake,
        "identifier": used_identifier,
        "state": state,
    }
