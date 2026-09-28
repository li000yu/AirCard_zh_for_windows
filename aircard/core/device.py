"""Windows port of `Sources/device_helper.m`.

Keeps every command, every validation and every JSON field produced by the
Objective-C helper so the Python side above it stays unchanged.
"""
from __future__ import annotations

import ctypes
import posixpath
import re
import struct
import time
from ctypes import POINTER, byref, c_char_p, c_int, c_long, c_uint32, c_void_p
from pathlib import Path
from typing import Any, Iterator

from ..core import usbmux
from ..native.cf import AppleRuntime, as_void_p, cf_shared
from ..native.mobiledevice import md_shared

AIRLIFT_TESTED_BUILDS = [
    ("27.0", "24A435"),
    ("27.0", "24A437"),
    ("27.0", "24A5390f"),
]

AIRLIFT_SOURCE_PREFIX = "airlift-src-"
AIRLIFT_LINK_PREFIX = "airlift-link-"
AIRLIFT_RECOVERED_PREFIX = "airlift-recovered-"
AIRLIFT_CANARY_PREFIX = "airlift-canary-"

TRACKED_BOOKS_FILES = [
    "Books/Books.plist",
    "Books/Sync/Books.plist",
    "Books/Sync/Upload.plist",
    "Books/Sync/Database/OutstandingAssets_4.sqlite",
    "Books/Sync/Database/OutstandingAssets_4.sqlite-shm",
    "Books/Sync/Database/OutstandingAssets_4.sqlite-wal",
]

TRACKED_BOOKS_DIRECTORIES = [
    "Books",
    "Books/Sync",
    "Books/Sync/Database",
]

_SCANNER_PREFIX = "扫描器: "


class DeviceHelperError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Small helpers ported verbatim from the Objective-C source
# ---------------------------------------------------------------------------

def is_safe_relative_path(path: str) -> bool:
    if not path or path.startswith("/") or path.endswith("/"):
        return False
    return all(part and part not in (".", "..") for part in path.split("/"))


def is_lowercase_hex(value: str, length: int) -> bool:
    return len(value) == length and bool(re.fullmatch(r"[0-9a-f]+", value or ""))


def generated_token(value: str, prefix: str) -> str | None:
    if not value.startswith(prefix) or "/" in value:
        return None
    token = value[len(prefix):]
    return token if is_lowercase_hex(token, 20) else None


def generated_names_match(source: str, link_destination: str, recovered: str) -> bool:
    token = generated_token(source, AIRLIFT_SOURCE_PREFIX)
    return bool(
        token
        and generated_token(link_destination, AIRLIFT_LINK_PREFIX) == token
        and generated_token(recovered, AIRLIFT_RECOVERED_PREFIX) == token
    )


def is_canary_leaf(leaf: str) -> bool:
    prefix = AIRLIFT_CANARY_PREFIX
    suffix = ".bin"
    if not leaf.startswith(prefix) or not leaf.endswith(suffix):
        return False
    if len(leaf) < len(prefix) + len(suffix) or "/" in leaf:
        return False
    token = leaf[len(prefix):len(leaf) - len(suffix)]
    return is_lowercase_hex(token, 32)


def snapshot_file_name(index: int) -> str:
    return f"file-{index}.bin"


def build_matches(summary: dict[str, Any], version: str, build: str) -> bool:
    return summary.get("productVersion") == version and summary.get("buildVersion") == build


def target_gate(summary: dict[str, Any]) -> tuple[bool, bool]:
    """Returns (passes, tested) exactly like the macOS helper."""
    product = summary.get("productType") or ""
    if not product.startswith("iPhone"):
        return False, False
    for version, build in AIRLIFT_TESTED_BUILDS:
        if build_matches(summary, version, build):
            return True, True
    return True, False


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class DeviceSession:
    """Mirrors the `DeviceSession` struct and `OpenSession`/`CloseSession`."""

    def __init__(self, udid: str, timeout: float = 30.0) -> None:
        self.udid = udid
        self.md = md_shared()
        self.cf = cf_shared()
        self.timeout = timeout

        self.device: Any = None
        self.connected = False
        self.session_started = False
        self.afc_service: Any = None
        self.afc: Any = None
        self.subscribe_status = -1
        self.connect_status = -1
        self.validate_status = -1
        self.session_status = -1
        self.service_status = -1
        self.afc_status = -1

    # -- lifecycle ----------------------------------------------------
    def open(self) -> None:
        device = self.md.find_target(self.udid, timeout=self.timeout)
        self.subscribe_status = 0 if device else -1
        self.device = device
        if not device:
            return
        handle = as_void_p(device)
        self.connect_status = self.md.AMDeviceConnect(handle)
        self.connected = self.connect_status == 0
        if not self.connected:
            return
        if self.md.AMDeviceIsPaired(handle) == 0:
            self.md.AMDevicePair(handle)
        self.validate_status = self.md.AMDeviceValidatePairing(handle)
        if self.validate_status != 0:
            self.md.AMDevicePair(handle)
            self.validate_status = self.md.AMDeviceValidatePairing(handle)
        if self.validate_status != 0:
            return
        self.session_status = self.md.AMDeviceStartSession(handle)
        self.session_started = self.session_status == 0
        if not self.session_started:
            return
        service = c_void_p()
        cf_service = self.cf.cf_string("com.apple.afc")
        try:
            self.service_status = self.md.AMDeviceSecureStartService(
                handle, as_void_p(cf_service), None, byref(service))
        finally:
            self.cf.release(cf_service)
        if self.service_status != 0 or not service.value:
            return
        self.afc_service = service.value
        connection = c_void_p()
        self.afc_status = self.md.AFCConnectionOpen(
            self.md.AMDServiceConnectionGetSocket(as_void_p(self.afc_service)),
            0, byref(connection))
        secure = self.md.AMDServiceConnectionGetSecureIOContext(as_void_p(self.afc_service))
        if connection.value:
            self.afc = connection.value
            if self.afc_status == 0 and secure:
                self.md.AFCConnectionSetSecureContext(as_void_p(self.afc),
                                                      as_void_p(secure))
                if self.md.AFCConnectionSetDisposeSecureContextOnInvalidate is not None:
                    self.md.AFCConnectionSetDisposeSecureContextOnInvalidate(
                        as_void_p(self.afc), 0)
                self.md.AFCConnectionSetIOTimeout(as_void_p(self.afc), 30)

    def close(self) -> None:
        if self.afc:
            try:
                self.md.AFCConnectionClose(as_void_p(self.afc))
            except Exception:
                pass
        if self.afc_service:
            try:
                self.md.AMDServiceConnectionInvalidate(as_void_p(self.afc_service))
            except Exception:
                pass
        if self.session_started and self.device:
            try:
                self.md.AMDeviceStopSession(as_void_p(self.device))
            except Exception:
                pass
        if self.connected and self.device:
            try:
                self.md.AMDeviceDisconnect(as_void_p(self.device))
            except Exception:
                pass
        if self.device:
            self.cf.release(self.device)
            self.device = None

    def __enter__(self) -> "DeviceSession":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- bookkeeping --------------------------------------------------
    def summary(self) -> dict[str, Any]:
        product_type = self._value("ProductType") if self.connected else "(nil)"
        product_version = self._value("ProductVersion") if self.connected else "(nil)"
        build_version = self._value("BuildVersion") if self.connected else "(nil)"
        return {
            "subscribeStatus": self.subscribe_status,
            "targetObserved": self.device is not None,
            "connectStatus": self.connect_status,
            "validateStatus": self.validate_status,
            "sessionStatus": self.session_status,
            "serviceStatus": self.service_status,
            "afcStatus": self.afc_status,
            "productType": product_type,
            "productVersion": product_version,
            "buildVersion": build_version,
        }

    def _value(self, key: str) -> str:
        return self.md._copy_value(as_void_p(self.device), None, key, "(nil)")

    @property
    def ready(self) -> bool:
        return self.afc_status == 0 and self.afc is not None

    # -- AFC primitives -----------------------------------------------
    def _utf8(self, path: str) -> bytes:
        return path.encode("utf-8")

    def exists(self, path: str) -> bool:
        info = c_void_p()
        status = self.md.AFCFileInfoOpen(as_void_p(self.afc), self._utf8(path), byref(info))
        if info.value:
            self.md.AFCKeyValueClose(as_void_p(info.value))
        return status == 0

    def file_size(self, path: str) -> int:
        info = c_void_p()
        if self.md.AFCFileInfoOpen(as_void_p(self.afc), self._utf8(path), byref(info)) != 0 \
                or not info.value:
            return -1
        size = -1
        key = c_char_p()
        value = c_char_p()
        try:
            while self.md.AFCKeyValueRead(
                    as_void_p(info.value), byref(key), byref(value)) == 0:
                if not key.value or not value.value:
                    break
                if key.value == b"st_size":
                    try:
                        size = int(value.value)
                    except ValueError:
                        pass
                key = c_char_p()
                value = c_char_p()
        finally:
            self.md.AFCKeyValueClose(as_void_p(info.value))
        return size

    def file_kind(self, path: str) -> str | None:
        info = c_void_p()
        if self.md.AFCFileInfoOpen(as_void_p(self.afc), self._utf8(path), byref(info)) != 0 \
                or not info.value:
            return None
        kind = None
        key = c_char_p()
        value = c_char_p()
        try:
            while self.md.AFCKeyValueRead(
                    as_void_p(info.value), byref(key), byref(value)) == 0:
                if not key.value or not value.value:
                    break
                if key.value == b"st_ifmt":
                    kind = value.value.decode("utf-8", errors="replace")
                key = c_char_p()
                value = c_char_p()
        finally:
            self.md.AFCKeyValueClose(as_void_p(info.value))
        return kind

    def read_file_limited(self, path: str, limit: int) -> bytes | None:
        size = self.file_size(path)
        if size < 0 or size > limit:
            return None
        handle = c_void_p()
        if self.md.AFCFileRefOpen(as_void_p(self.afc), self._utf8(path), 1,
                                  byref(handle)) != 0 or not handle.value:
            return None
        afc_ref = handle.value
        buffer = bytearray(size)
        offset = 0
        status = 0
        chunk = c_uint32(0)
        if buffer:
            view = (ctypes.c_char * size).from_buffer(buffer)
            while offset < size:
                chunk.value = size - offset
                status = self.md.AFCFileRefRead(
                    as_void_p(self.afc), as_void_p(afc_ref),
                    ctypes.byref(view, offset), byref(chunk))
                if status != 0 or chunk.value <= 0 or chunk.value > size - offset:
                    break
                offset += chunk.value
        close_status = self.md.AFCFileRefClose(as_void_p(self.afc),
                                               as_void_p(afc_ref))
        if status != 0 or close_status != 0 or offset != size:
            return None
        return bytes(buffer)

    def read_file(self, path: str) -> bytes | None:
        return self.read_file_limited(path, 16 * 1024 * 1024)

    def write_file(self, path: str, data: bytes) -> bool:
        handle = c_void_p()
        if self.md.AFCFileRefOpen(as_void_p(self.afc), self._utf8(path), 3,
                                  byref(handle)) != 0 or not handle.value:
            return False
        afc_ref = handle.value
        status = 0
        if data:
            raw = (ctypes.c_char * len(data)).from_buffer_copy(data)
            status = self.md.AFCFileRefWrite(
                as_void_p(self.afc), as_void_p(afc_ref), raw, len(data))
        close_status = self.md.AFCFileRefClose(as_void_p(self.afc),
                                               as_void_p(afc_ref))
        return status == 0 and close_status == 0

    def ensure_directory(self, path: str) -> bool:
        return self.exists(path) or \
            self.md.AFCDirectoryCreate(as_void_p(self.afc), self._utf8(path)) == 0

    def remove_if_present(self, path: str) -> bool:
        if not self.exists(path):
            return True
        removed = self.md.AFCRemovePath(as_void_p(self.afc), self._utf8(path)) == 0
        return removed and not self.exists(path)

    def create_directory(self, path: str) -> bool:
        return self.md.AFCDirectoryCreate(as_void_p(self.afc), self._utf8(path)) == 0

    # -- Books snapshot helpers ---------------------------------------
    def all_tracked_books_files_absent(self) -> bool:
        return not any(self.exists(p) for p in TRACKED_BOOKS_FILES)

    def present_tracked_books_paths(self) -> list[str]:
        return [p for p in TRACKED_BOOKS_FILES if self.exists(p)]

    def load_books_snapshot(self, root: str) -> dict[str, Any] | None:
        import plistlib

        manifest = Path(root) / "manifest.plist"
        if not manifest.is_file():
            return None
        try:
            value = plistlib.loads(manifest.read_bytes())
        except Exception:
            return None
        if not isinstance(value, dict):
            return None
        if value.get("version") != 1:
            return None
        files = value.get("files")
        directories = value.get("directories")
        if not isinstance(files, dict) or not isinstance(directories, dict):
            return None
        for index, path in enumerate(TRACKED_BOOKS_FILES):
            row = files.get(path)
            if not isinstance(row, dict):
                return None
            if not isinstance(row.get("exists"), bool):
                return None
            expected = snapshot_file_name(index)
            if row.get("localName") != expected:
                return None
            if row["exists"]:
                local = Path(root) / expected
                if not local.is_file():
                    return None
        for path in TRACKED_BOOKS_DIRECTORIES:
            if not isinstance(directories.get(path), bool):
                return None
        return value

    def snapshot_books_state(self, root: str) -> dict[str, Any]:
        import plistlib

        root_path = Path(root)
        root_ready = root_path.is_dir()
        if not root_ready or (root_path / "manifest.plist").exists():
            return {"ok": False, "snapshotDirectoryReady": root_ready}

        files: dict[str, Any] = {}
        directories: dict[str, Any] = {}
        present: list[str] = []
        total = 0

        for index, path in enumerate(TRACKED_BOOKS_FILES):
            local_name = snapshot_file_name(index)
            exists = self.exists(path)
            if exists and self.file_kind(path) != "S_IFREG":
                return {"ok": False, "unexpectedFileType": path}
            data = self.read_file_limited(path, 128 * 1024 * 1024) if exists else None
            if exists and data is None:
                return {"ok": False, "snapshotReadFailed": path}
            if data is not None:
                total += len(data)
                if total > 256 * 1024 * 1024:
                    return {"ok": False, "snapshotTooLarge": True}
                local = root_path / local_name
                if local.exists():
                    return {"ok": False, "snapshotWriteFailed": path,
                            "localError": "file already exists"}
                try:
                    local.write_bytes(data)
                except OSError as error:
                    return {"ok": False, "snapshotWriteFailed": path,
                            "localError": str(error)}
                present.append(path)
            files[path] = {"exists": exists, "localName": local_name,
                           "size": len(data) if data is not None else 0}

        for path in TRACKED_BOOKS_DIRECTORIES:
            exists = self.exists(path)
            kind = self.file_kind(path) if exists else None
            if exists and kind != "S_IFDIR":
                return {"ok": False, "unexpectedDirectoryType": path}
            directories[path] = exists

        manifest = {"version": 1, "files": files, "directories": directories}
        try:
            payload = plistlib.dumps(manifest, fmt=plistlib.FMT_BINARY, sort_keys=True)
            (root_path / "manifest.plist").write_bytes(payload)
            wrote = True
            error_text = None
        except Exception as error:
            wrote = False
            error_text = str(error)
        return {
            "ok": wrote,
            "presentPaths": present,
            "snapshotBytes": total,
            "localError": error_text,
        }

    def books_state_matches_snapshot(self, root: str, snapshot: dict[str, Any]) -> bool:
        files = snapshot.get("files", {})
        for index, path in enumerate(TRACKED_BOOKS_FILES):
            row = files.get(path) or {}
            expected = bool(row.get("exists"))
            if self.exists(path) != expected:
                return False
            if expected:
                expected_data = Path(root, snapshot_file_name(index)).read_bytes() \
                    if Path(root, snapshot_file_name(index)).is_file() else None
                observed = self.read_file_limited(path, 128 * 1024 * 1024)
                if expected_data is None or observed is None or observed != expected_data:
                    return False
        directories = snapshot.get("directories", {})
        for path in TRACKED_BOOKS_DIRECTORIES:
            expected = bool(directories.get(path))
            exists = self.exists(path)
            if exists != expected:
                return False
            if exists and self.file_kind(path) != "S_IFDIR":
                return False
        return True

    def _ensure_books_parent(self, path: str) -> bool:
        if not self.ensure_directory("Books"):
            return False
        if path.startswith("Books/Sync/") and not self.ensure_directory("Books/Sync"):
            return False
        if path.startswith("Books/Sync/Database/") \
                and not self.ensure_directory("Books/Sync/Database"):
            return False
        return True

    def restore_books_state(self, root: str) -> dict[str, Any]:
        snapshot = self.load_books_snapshot(root)
        if not snapshot:
            return {"ok": False, "error": "invalid snapshot"}
        failures: list[str] = []
        files = snapshot.get("files", {})

        for index, path in enumerate(TRACKED_BOOKS_FILES):
            row = files.get(path) or {}
            if row.get("exists"):
                local = Path(root) / snapshot_file_name(index)
                data = local.read_bytes() if local.is_file() else None
                if data is None or not self._ensure_books_parent(path) \
                        or not self.write_file(path, data):
                    failures.append(path)
            elif not self.remove_if_present(path):
                failures.append(path)

        directories = snapshot.get("directories", {})
        for path in reversed(TRACKED_BOOKS_DIRECTORIES):
            if not directories.get(path) and not self.remove_if_present(path):
                failures.append(path)

        verified = not failures and self.books_state_matches_snapshot(root, snapshot)
        return {"ok": verified, "failures": failures, "preimageVerified": verified}

    def remove_generated_tree(self, path: str, depth: int = 0) -> bool:
        if depth > 32:
            return False
        kind = self.file_kind(path)
        if kind is None:
            return True
        if kind == "S_IFDIR":
            directory = c_void_p()
            if self.md.AFCDirectoryOpen(as_void_p(self.afc), self._utf8(path),
                                        byref(directory)) != 0 or not directory.value:
                return False
            children: list[str] = []
            read_ok = True
            for _ in range(8192):
                raw = c_char_p()
                status = self.md.AFCDirectoryRead(
                    as_void_p(self.afc), as_void_p(directory.value), byref(raw))
                if status != 0:
                    read_ok = False
                    break
                if not raw.value:
                    break
                name = raw.value.decode("utf-8", errors="replace")
                if name in (".", ".."):
                    continue
                children.append(name)
            close_ok = self.md.AFCDirectoryClose(
                as_void_p(self.afc), as_void_p(directory.value)) == 0
            if not read_ok or not close_ok:
                return False
            for name in children:
                if not self.remove_generated_tree(posixpath.join(path, name), depth + 1):
                    return False
        removed = self.md.AFCRemovePath(as_void_p(self.afc), self._utf8(path)) == 0
        return removed and not self.exists(path)

    # -- commands ------------------------------------------------------
    def probe(self) -> dict[str, Any]:
        present = self.present_tracked_books_paths()
        return {
            "ok": True,
            "booksStagingAbsent": self.all_tracked_books_files_absent(),
            "presentBooksPaths": present,
            "fixedSyncInputPresent": "Books/Sync/Books.plist" in present,
            "booksSyncPlistPresent": self.exists("Books/Sync/Books.plist"),
        }

    def stage(self, source: str, link_destination: str, recovered: str,
              archive_path: str, books_path: str, snapshot_root: str) -> dict[str, Any]:
        try:
            archive = Path(archive_path).read_bytes()
        except OSError:
            archive = b""
        try:
            books = Path(books_path).read_bytes()
        except OSError:
            books = b""

        snapshot = self.load_books_snapshot(snapshot_root)
        safe_arguments = generated_names_match(source, link_destination, recovered)
        snapshot_matches = bool(snapshot) and \
            self.books_state_matches_snapshot(snapshot_root, snapshot)
        fresh_paths = not self.exists(source) and not self.exists(link_destination) \
            and not self.exists(recovered)

        if not safe_arguments or not archive or not books \
                or not snapshot_matches or not fresh_paths:
            return {
                "ok": False,
                "cleanupAuthorized": False,
                "safeArguments": safe_arguments,
                "localInputsReadable": bool(archive) and bool(books),
                "booksPreimageStable": snapshot_matches,
                "freshPaths": fresh_paths,
            }

        zip_service = c_void_p()
        cf_service = self.cf.cf_string("com.apple.streaming_zip_conduit")
        try:
            service_status = self.md.AMDeviceSecureStartService(
                as_void_p(self.device), as_void_p(cf_service), None,
                byref(zip_service))
        finally:
            self.cf.release(cf_service)

        message_status = -1
        response_status = -1
        archive_sent = False
        response = None
        if service_status == 0 and zip_service.value:
            cf_payload = self.cf.plist_to_cf({"MediaSubdir": source})
            try:
                message_status = self.md.AMDServiceConnectionSendMessage(
                    as_void_p(zip_service.value), as_void_p(cf_payload), 200)
            finally:
                self.cf.release(cf_payload)
            if message_status == 0:
                archive_sent = self._send_all(zip_service.value, archive)
            if archive_sent:
                self._arm_receive_timeout(zip_service.value, 30)
                received = c_void_p()
                fmt = c_int(200)
                response_status = self.md.AMDServiceConnectionReceiveMessage(
                    as_void_p(zip_service.value), byref(received), byref(fmt))
                response = received.value

        self.cf.release(response)
        if zip_service.value:
            try:
                self.md.AMDServiceConnectionInvalidate(as_void_p(zip_service.value))
            except Exception:
                pass

        link = posixpath.join(source, "p0/p1/p2/link")
        has_payload = self.exists(posixpath.join(source, "payload")) or \
            self.exists(posixpath.join(source, "payload_0"))
        source_objects = self.exists(source) and self.exists(link) and has_payload
        directories_ready = self.ensure_directory("Books") and self.ensure_directory("Books/Sync")
        books_written = source_objects and directories_ready and \
            self.write_file("Books/Sync/Books.plist", books)
        ok = service_status == 0 and message_status == 0 and archive_sent \
            and source_objects and books_written
        return {
            "ok": ok,
            "cleanupAuthorized": True,
            "safeArguments": True,
            "booksPreimageStable": True,
            "freshPaths": True,
            "zipServiceStatus": service_status,
            "zipMessageStatus": message_status,
            "zipResponseStatus": response_status,
            "archiveSent": archive_sent,
            "sourceObjectsPresent": source_objects,
            "booksWritten": books_written,
        }

    def _send_all(self, service: Any, data: bytes) -> bool:
        remaining = len(data)
        offset = 0
        if not remaining:
            return True
        raw = (ctypes.c_char * remaining).from_buffer_copy(data)
        while remaining:
            sent = self.md.AMDServiceConnectionSend(
                as_void_p(service),
                ctypes.byref(raw, offset), remaining)
            if sent <= 0:
                return False
            offset += sent
            remaining -= sent
        return True

    def _arm_receive_timeout(self, service: Any, seconds: int) -> None:
        """Best effort SO_RCVTIMEO like the helper does with setsockopt()."""
        try:
            socket = self.md.AMDServiceConnectionGetSocket(as_void_p(service))
            ws2 = ctypes.WinDLL("ws2_32")
            timeval = struct.pack("<II", seconds, 0)
            ws2.setsockopt(ctypes.c_uint64(socket), 0xFFFF, 0x1006, timeval, 8)
        except Exception:
            pass

    def finish_write(self, source: str, link_destination: str, recovered: str,
                     snapshot_root: str) -> dict[str, Any]:
        failures: list[str] = []
        if not self.remove_if_present(link_destination):
            failures.append("relocated link")
        if not self.remove_if_present(recovered):
            failures.append("recovered file")
        if not self.remove_generated_tree(source, 0):
            failures.append("StreamingZip tree")
        time.sleep(2)
        books_restore = self.restore_books_state(snapshot_root)
        books_restored = bool(books_restore.get("ok"))
        if not books_restored:
            failures.append("Books preimage")
        source_absent = not self.exists(source)
        link_absent = not self.exists(link_destination)
        recovered_absent = not self.exists(recovered)
        cleanup_complete = not failures and source_absent and link_absent \
            and recovered_absent and books_restored
        return {
            "ok": cleanup_complete,
            "cleanupComplete": cleanup_complete,
            "failures": failures,
            "sourceAbsent": source_absent,
            "linkAbsent": link_absent,
            "recoveredAbsent": recovered_absent,
            "booksPreimageRestored": books_restored,
            "booksRestore": books_restore,
        }

    def finish_moved_removal(self, source: str, link_destination: str, recovered: str,
                             snapshot_root: str, expected_count: str) -> dict[str, Any]:
        try:
            count = int(expected_count)
        except (TypeError, ValueError):
            count = 0
        safe = generated_names_match(source, link_destination, recovered) \
            and 0 < count <= 32
        if not safe:
            return {"ok": False, "safeArguments": False}

        missing: list[str] = []
        for index in range(count):
            path = posixpath.join(source, f"removed-{index}")
            if not self.exists(path):
                missing.append(path)

        failures: list[str] = []
        if not self.remove_if_present(link_destination):
            failures.append("relocated link")
        if not self.remove_if_present(recovered):
            failures.append("recovered file")
        if not self.remove_generated_tree(source, 0):
            failures.append("StreamingZip tree")
        books_restore = self.restore_books_state(snapshot_root)
        if not books_restore.get("ok"):
            failures.append("Books preimage")
        cleanup_complete = not failures
        return {
            "ok": cleanup_complete,
            "safeArguments": True,
            "movedCount": count - len(missing),
            "alreadyAbsentCount": len(missing),
            "allTargetsMoved": not missing,
            "missing": missing,
            "cleanupComplete": cleanup_complete,
            "failures": failures,
            "booksRestore": books_restore,
        }

    def afc_read(self, media_path: str, local_out: str) -> dict[str, Any]:
        if not is_safe_relative_path(media_path):
            return {"ok": False, "error": "不安全的媒体路径（含 / 或 . ）"}
        size = self.file_size(media_path)
        if size < 0:
            # 文件根本不在设备上（airlift 的 ATC 重定位没把它放到这个位置）。
            # 之前这里静默返回 ok=False，导致上层只能报「原因未上报」—— 现在给
            # 出明确结论，便于区分「文件不存在」与「读取出错」。
            return {"ok": False,
                    "error": "设备未生成可读取的文件（AFC 找不到该路径，"
                             "airlift 重定位可能未生效）",
                    "size": -1, "path": media_path}
        if size > 32 * 1024 * 1024:
            return {"ok": False, "error": f"文件过大（{size} 字节，超过 32MB 上限）",
                    "size": size, "path": media_path}
        data = self.read_file_limited(media_path, 32 * 1024 * 1024)
        if data is None:
            return {"ok": False, "error": "文件存在但读取失败（AFC 读取出错）",
                    "size": size, "path": media_path}
        try:
            target = Path(local_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            return {"ok": False, "error": f"本地写入失败：{exc}",
                    "size": size, "path": media_path}
        return {"ok": True, "size": len(data), "path": media_path}


# ---------------------------------------------------------------------------
# High level command entry points
# ---------------------------------------------------------------------------

def list_devices() -> list[dict[str, Any]]:
    return md_shared().enumerate_devices(timeout=2.0)


def environment_probe(include_atc: bool = False) -> dict[str, Any]:
    """诊断设备检测链路的每一跳，供 UI 给出可操作提示。绝不抛异常。

    include_atc: 是否额外探测 airlift 同步通道。默认 **关** —— 它最长要花
    几秒，而启动时的诊断结果决定了界面控件何时可用，不能拖。只有用户主动
    点开诊断详情时才设为 True。
    """
    report: dict[str, Any] = {
        "apple_runtime": False,
        "support_dir": "",
        "runtime_error": "",
        "md_device_count": 0,
        "device_detail": None,
        "usbmux_reachable": False,
        "usbmux_serials": [],
        "usbmux_error": "",
        "hint": "",
    }

    try:
        runtime = AppleRuntime.shared()
        report["apple_runtime"] = runtime.md is not None and runtime.cf is not None
        report["support_dir"] = str(runtime.support_dir or "")
        report["runtime_error"] = runtime.error or ""
    except Exception as exc:  # noqa: BLE001
        report["runtime_error"] = str(exc)
        return report

    # 1) MobileDevice 侧枚举（并对首个设备跑完整握手，记录每一步状态码）
    try:
        md = md_shared()
        handles = md.device_list()
        report["md_device_count"] = len(handles)
        if handles:
            try:
                report["device_detail"] = md.probe_device(handles[0])
            except Exception as exc:  # noqa: BLE001
                report["device_detail"] = {"exception": str(exc)}
        for item in handles:
            if not isinstance(item, dict):
                md.cf.release(item)
    except Exception as exc:  # noqa: BLE001
        report["md_device_count"] = -1
        report["runtime_error"] = report["runtime_error"] or str(exc)

    # 2) usbmux 侧枚举（独立路径，用于定位是"没连设备"还是"没信任"）
    try:
        probe_result = usbmux.probe()
        report["usbmux_reachable"] = bool(probe_result.get("reachable"))
        report["usbmux_serials"] = list(probe_result.get("serials") or [])
        report["usbmux_error"] = str(probe_result.get("error") or "")
    except Exception as exc:  # noqa: BLE001
        report["usbmux_error"] = str(exc)

    # 3) airlift 通道（换卡面 / 读卡面 / 写主题全靠它）。
    #    拿不到 SyncAllowed 时上述功能会以完全相同的错误失败，所以必须单独探。
    report["atc"] = None
    if include_atc:
        report["atc"] = _probe_atc(report)
    return _finalize(report)


def _probe_atc(report: dict[str, Any]) -> dict[str, Any]:
    """探测 airlift 同步通道；任何异常都转成可展示的字典。

    同时抓取本次探测期间 Apple 组件（ASL）自己写的日志 —— ctypes 调用只能看到
    返回值，而 `com.apple.atc` 服务起不来、grappa 初始化失败这类内部报错只会
    出现在 Apple 自己的日志里。
    """
    try:
        from .asllog import delta, snapshot
        from ..native.airtraffic import probe_sync_handshake

        udid = ""
        detail = report.get("device_detail")
        if isinstance(detail, dict):
            udid = str(detail.get("udid") or "")
        if not udid and report.get("usbmux_serials"):
            udid = str(report["usbmux_serials"][0])
        if not udid:
            return {"error": "未拿到 UDID，跳过通道探测"}

        mark = snapshot()
        result = probe_sync_handshake(udid)
        if isinstance(result, dict):
            result["asl"] = delta(mark)
        return result
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _finalize(report: dict[str, Any]) -> dict[str, Any]:
    report["hint"] = _build_hint(report)
    return report


def _build_hint(report: dict[str, Any]) -> str:
    """把诊断结果翻译成一句中文可操作建议。"""
    if not report.get("apple_runtime"):
        return ("未加载 Apple 移动设备支持组件，请先安装 iTunes 或 Apple Devices "
                "（需包含 MobileDevice.dll）。")
    if not report.get("usbmux_reachable"):
        return ("Apple Mobile Device Service 未响应（端口 27015 不可达）。"
                "请在 Windows 服务中重启「Apple Mobile Device Service」，或重启 iTunes。")
    if report.get("usbmux_error"):
        return f"usbmux 通信异常：{report['usbmux_error']}"

    if report.get("md_device_count", 0) <= 0 and not report.get("usbmux_serials"):
        return ("未检测到任何连接的 iPhone。请确认：① 使用原装或支持数据传输的数据线；"
                "② iPhone 已解锁并点击「信任此电脑」；③ 换一个 USB 接口（避免使用仅供电口）。")

    detail = report.get("device_detail")
    if not isinstance(detail, dict):
        if report.get("usbmux_serials"):
            return ("iPhone 已连接但尚未被 MobileDevice 组件识别，请在 iPhone 上解锁并"
                    "点击「信任此电脑」，然后点刷新重试。")
        return "设备可见，但读取设备信息失败，请重新插拔数据线后重试。"

    if detail.get("exception"):
        return f"读取设备信息时出错：{detail['exception']}"

    connect = detail.get("connect")
    if connect not in (0, None):
        return (f"已发现设备但无法建立连接（错误码 {_hex_code(connect)}）。"
                "请重新插拔数据线，并确保 iPhone 已解锁。")

    validate = detail.get("validate")
    if validate not in (0, None):
        code = _unsigned(validate)
        if code == 0xE800001E:
            return ("iPhone 上尚未确认信任：请解锁手机，在「要信任此电脑吗？」"
                    "弹窗中点「信任」并输入锁屏密码，然后点刷新重试。")
        if code == 0xE8000025:
            return ("你已在 iPhone 上选择「不信任」。请拔掉数据线重新插入，"
                    "出现弹窗时选择「信任」。")
        if code == 0xE8000029:
            return "iPhone 处于锁定状态，请先解锁屏幕后再点刷新重试。"
        return (f"配对校验失败（错误码 {_hex_code(validate)}）。请在 iPhone 上解锁并"
                "点击「信任此电脑」，然后点刷新重试。")

    session = detail.get("session")
    if session not in (0, None):
        return (f"会话启动失败（错误码 {_hex_code(session)}）。请重启 iTunes，"
                "或在 Windows 服务中重启「Apple Mobile Device Service」。")

    if not detail.get("product"):
        return ("已连接并授权，但读取设备型号失败。请重新插拔数据线后点刷新重试。")

    return "设备已就绪，可以正常使用。"


def _unsigned(value: Any) -> int:
    try:
        return int(value) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return -1


def _hex_code(value: Any) -> str:
    code = _unsigned(value)
    return f"0x{code:08X}" if code >= 0 else str(value)


def get_connected_device() -> dict[str, Any] | None:
    devices = list_devices()
    usable = [d for d in devices if d.get("udid") and d.get("product")]
    if not usable:
        return None
    iphones = [d for d in usable if str(d["product"]).startswith("iPhone")]
    device = (iphones or usable)[0]
    return {
        "udid": device["udid"],
        "name": device.get("name") or "iPhone",
        "version": device.get("version") or "未知",
        "product": device["product"],
        "language": device.get("language") or "en",
        "locale": device.get("locale") or "",
        "bold_text": device.get("bold_text"),
        "buildVersion": device.get("buildVersion") or "",
    }


def run_command(udid: str, command: str, args: list[str],
                timeout: float = 30.0) -> tuple[dict[str, Any], bool, bool]:
    """Runs one device_helper command and returns (result, gate_passed, ok)."""
    session = DeviceSession(udid, timeout=timeout)
    session.open()
    try:
        summary = session.summary()
        gate_passed, tested = target_gate(summary)
        operation: dict[str, Any] = {"ok": False}
        if session.ready and gate_passed:
            if command == "probe":
                operation = session.probe()
            elif command == "snapshot-books" and len(args) == 1:
                operation = session.snapshot_books_state(args[0])
            elif command == "restore-books" and len(args) == 1:
                operation = session.restore_books_state(args[0])
            elif command == "stage" and len(args) == 6:
                operation = session.stage(*args[:6])
            elif command == "finish-write" and len(args) == 4:
                operation = session.finish_write(*args[:4])
            elif command == "finish-moved-removal" and len(args) == 5:
                operation = session.finish_moved_removal(*args[:5])
            elif command == "afc-read" and len(args) == 2:
                operation = session.afc_read(args[0], args[1])
        result = dict(summary)
        result["targetGatePassed"] = gate_passed
        result["targetTested"] = tested
        result["command"] = command
        result["operation"] = operation
        ok = gate_passed and session.afc_status == 0 and bool(operation.get("ok"))
        return result, gate_passed, ok
    finally:
        session.close()


def operation_ok(result: dict[str, Any]) -> bool:
    return bool(
        result.get("targetGatePassed")
        and result.get("operation", {}).get("ok")
    )


def native(command: str, udid: str, *arguments: str, timeout: float = 30.0) -> dict[str, Any]:
    result, _gate, _ok = run_command(udid, command, list(arguments), timeout=timeout)
    result["exitCode"] = 0 if _ok else 2
    return result


# ---------------------------------------------------------------------------
# Unified log streaming (port of RunSyslog + os_trace.h)
# ---------------------------------------------------------------------------

def _u32le(data: bytes) -> int:
    return int.from_bytes(data, "little")


def trace_log_line(record: bytes) -> str | None:
    if len(record) < 129:
        return None
    if record[0] != 2:
        return None
    header_length = int.from_bytes(record[5:9], "little")
    process_length = int.from_bytes(record[37:39], "little")
    image_length = int.from_bytes(record[107:109], "little")
    message_length = int.from_bytes(record[109:113], "little")
    if header_length < 129 or header_length > len(record):
        return None
    if not process_length or not message_length:
        return None
    if process_length + image_length + message_length > len(record) - header_length:
        return None

    text = record[header_length:]

    def decode(payload: bytes) -> str:
        payload = payload.rstrip(b"\x00")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return payload.decode("latin-1", errors="replace")

    process = decode(text[:process_length]).rsplit("/", 1)[-1]
    image = decode(text[process_length:process_length + image_length]).rsplit("/", 1)[-1]
    message = decode(text[process_length + image_length:
                         process_length + image_length + message_length])
    return f"{process}({image}): {message}\n"


class _TraceReader:
    def __init__(self, connection: Any) -> None:
        self.md = md_shared()
        self.connection = as_void_p(connection)

    def receive(self, length: int) -> bytes | None:
        buffer = bytearray(length)
        offset = 0
        if length:
            view = (ctypes.c_char * length).from_buffer(buffer)
            while offset < length:
                got = self.md.AMDServiceConnectionReceive(
                    self.connection, ctypes.byref(view, offset), length - offset)
                if got <= 0 or got > length - offset:
                    return None
                offset += got
        return bytes(buffer)

    def frame(self) -> tuple[int, bytes, str | None]:
        header = self.receive(5)
        if header is None:
            return 0, b"", "设备日志流已断开。"
        kind = header[0]
        if kind == 1:
            length = int.from_bytes(header[1:5], "big")
        elif kind == 2:
            length = _u32le(header[1:5])
        else:
            return 0, b"", "设备返回了不支持的日志帧类型。"
        if length == 0 or length > 16 * 1024 * 1024:
            return 0, b"", "设备返回了非法的日志帧长度。"
        payload = self.receive(length)
        if payload is None:
            return 0, b"", "日志流在记录中途结束。"
        return kind, payload, None


class DeviceLogStream:
    """Port of `device_helper syslog`, controllable so the UI can stop it.

    The native receive blocks, so stopping additionally invalidates the service
    connection which makes the pending read fail and lets the worker exit - the
    same net effect macOS got from killing the helper process.
    """

    def __init__(self, udid: str) -> None:
        self.udid = udid
        self.md = md_shared()
        self.cf = cf_shared()
        self._device: Any = None
        self._connection: Any = None
        self._reader: _TraceReader | None = None
        self._stopped = False

    # -- lifecycle ----------------------------------------------------
    def open(self, timeout: float = 30.0) -> tuple[bool, str]:
        device = self.md.find_target(self.udid, timeout=timeout)
        if not device:
            return False, f"{_SCANNER_PREFIX}未找到 iPhone，请用 USB 重新连接。"
        self._device = device
        handle = as_void_p(device)
        if self.md.AMDeviceConnect(handle) != 0:
            return False, f"{_SCANNER_PREFIX}无法连接到 iPhone。"
        if self.md.AMDeviceIsPaired(handle) == 0:
            self.md.AMDevicePair(handle)
        if self.md.AMDeviceValidatePairing(handle) != 0 or \
                self.md.AMDeviceStartSession(handle) != 0:
            self.md.AMDeviceDisconnect(handle)
            self.md.AMDeviceStopSession(handle)
            return False, f"{_SCANNER_PREFIX}请解锁 iPhone 并信任此电脑，然后重试。"

        # 出参容器：必须是一个全新的空 c_void_p 实例（不能用 as_void_p()）。
        connection = c_void_p()
        cf_service = self.cf.cf_string("com.apple.os_trace_relay")
        try:
            status = self.md.AMDeviceSecureStartService(
                handle, as_void_p(cf_service), None, byref(connection))
        finally:
            self.cf.release(cf_service)
        if status != 0 or not connection.value:
            self.md.AMDeviceStopSession(handle)
            self.md.AMDeviceDisconnect(handle)
            return False, f"{_SCANNER_PREFIX}无法打开设备日志服务，请解锁 iPhone 后重试。"
        self._connection = connection.value
        self._handle = handle

        import plistlib

        request = self.cf.plist_to_cf({
            "Request": "StartActivity",
            "Pid": 0xFFFFFFFF,
            "MessageFilter": 0xFFFF,
            "StreamFlags": 0x3C,
        })
        try:
            sent = self.md.AMDServiceConnectionSendMessage(
                as_void_p(self._connection), as_void_p(request), 200)
        finally:
            self.cf.release(request)
        if sent != 0:
            self.close()
            return False, f"{_SCANNER_PREFIX}无法请求设备日志流。"

        reader = _TraceReader(self._connection)
        kind, payload, error = reader.frame()
        reply = None
        if kind == 1:
            try:
                reply = plistlib.loads(payload)
            except Exception:
                reply = None
        if not isinstance(reply, dict) or reply.get("Status") != "RequestSuccessful":
            self.close()
            return False, f"{_SCANNER_PREFIX}{error or '设备拒绝启动日志流。'}"

        self._reader = reader
        return True, f"{_SCANNER_PREFIX}已连接到统一设备日志流。"

    def read_line(self) -> str | None:
        """Returns one formatted line, or None once the stream is finished."""
        if self._stopped or self._reader is None:
            return None
        while True:
            kind, payload, error = self._reader.frame()
            if kind == 0:
                return f"{_SCANNER_PREFIX}{error or '日志流结束。'}"
            if kind != 2:
                continue
            line = trace_log_line(payload)
            if line is not None:
                return line

    def stop(self) -> None:
        """Unblocks any pending read so the consumer thread can return."""
        self._stopped = True
        connection = getattr(self, "_connection", None)
        if connection:
            try:
                self.md.AMDServiceConnectionInvalidate(as_void_p(connection))
            except Exception:
                pass

    def close(self) -> None:
        try:
            if getattr(self, "_connection", None):
                self.md.AMDServiceConnectionInvalidate(
                    as_void_p(self._connection))
        except Exception:
            pass
        try:
            if getattr(self, "_handle", None):
                self.md.AMDeviceStopSession(self._handle)
                self.md.AMDeviceDisconnect(self._handle)
        except Exception:
            pass
        finally:
            if self._device:
                self.cf.release(self._device)
                self._device = None
            self._connection = None
            self._reader = None


def stream_device_logs(udid: str) -> Iterator[str]:
    """Convenience generator, kept for scripting outside the GUI."""
    stream = DeviceLogStream(udid)
    ok, message = stream.open()
    yield message
    if not ok:
        stream.close()
        return
    try:
        while True:
            line = stream.read_line()
            if line is None:
                break
            yield line
    finally:
        stream.close()
