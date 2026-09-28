"""Windows port of `apply_card_skin.py` - the airlift write/read/remove primitives.

The archive layout, the Books preimage protocol and the retry behaviour are
unchanged; only the transport moved from a subprocess helper to this module.
"""
from __future__ import annotations

import io
import plistlib
import posixpath
import secrets
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from .device import DeviceSession, operation_ok, run_command

AIRLOCK_ROOT = "/var/mobile/Media/Airlock/Book"
SOURCE_PREFIX = "airlift-src-"
LINK_PREFIX = "airlift-link-"
RECOVERED_PREFIX = "airlift-recovered-"
SZ_EXTRA_ID = 0x5A53

CACHE_FILES = ("FrontFace", "PlaceHolder", "Preview")


# ---------------------------------------------------------------------------
# Archive construction
# ---------------------------------------------------------------------------

def zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    import struct

    info = zipfile.ZipInfo(name, date_time=(2026, 9, 14, 5, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (mode & 0xFFFF) << 16
    info.extra = struct.pack("<HHH", SZ_EXTRA_ID, 2, mode & 0xFFFF)
    return info


def build_archive(target: str, payload: bytes) -> bytes:
    import stat

    target_tail = target[1:]
    metadata = plistlib.dumps({"Version": 2}, fmt=plistlib.FMT_BINARY, sort_keys=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(zip_info("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600),
            metadata)
        for directory in ("p0/", "p0/p1/", "p0/p1/p2/"):
            archive.writestr(zip_info(directory, stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("p0/p1/p2/link", stat.S_IFLNK | 0o777),
            f"../../../{target_tail}".encode())
        cursor = ""
        for component in target_tail.split("/"):
            cursor += component + "/"
            archive.writestr(zip_info(cursor, stat.S_IFDIR | 0o755), b"")
        archive.writestr(zip_info("payload", stat.S_IFREG | 0o600), payload)
    return output.getvalue()


def build_archive_multi(target: str, files: list[tuple[str, bytes]]) -> bytes:
    import stat

    target_tail = target.lstrip("/")
    metadata = plistlib.dumps({"Version": 2}, fmt=plistlib.FMT_BINARY, sort_keys=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        archive.writestr(zip_info("META-INF/", stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("META-INF/com.apple.ZipMetadata.plist", stat.S_IFREG | 0o600),
            metadata)
        for directory in ("p0/", "p0/p1/", "p0/p1/p2/"):
            archive.writestr(zip_info(directory, stat.S_IFDIR | 0o755), b"")
        archive.writestr(
            zip_info("p0/p1/p2/link", stat.S_IFLNK | 0o777),
            f"../../../{target_tail}".encode())
        cursor = ""
        for component in target_tail.split("/"):
            if not component:
                continue
            cursor += component + "/"
            archive.writestr(zip_info(cursor, stat.S_IFDIR | 0o755), b"")
        for index, (_leaf, payload) in enumerate(files):
            archive.writestr(zip_info(f"payload_{index}", stat.S_IFREG | 0o600), payload)
        if files:
            archive.writestr(zip_info("payload", stat.S_IFREG | 0o600), files[0][1])
    return output.getvalue()


def build_books(identifiers: list[str]) -> bytes:
    rows = [
        {"Persistent ID": identifier, "Item ID": str(index), "DSID": "1"}
        for index, identifier in enumerate(identifiers, 1)
    ]
    return plistlib.dumps({"Books": rows}, fmt=plistlib.FMT_BINARY, sort_keys=True)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def native(command: str, udid: str, *arguments: str, timeout: float = 30.0) -> dict[str, Any]:
    result, _gate, ok = run_command(udid, command, list(arguments), timeout=timeout)
    result["exitCode"] = 0 if ok else 2
    return result


def describe_failure(result: dict[str, Any] | None) -> str:
    """把 native() 的返回结构翻译成一句人能看懂的失败原因。

    之前所有失败都被 `except Exception: pass` 吞掉，界面只能显示"写入失败"，
    无法定位是快照、stage、同步还是收尾哪一步出了问题。
    """
    if not result:
        return "设备无响应"
    parts: list[str] = []
    code = result.get("exitCode")
    if code:
        parts.append(f"退出码 {code}")
    if not result.get("targetGatePassed"):
        parts.append(f"目标校验未通过（{result.get('targetGateReason') or '原因未上报'}）")
    operation = result.get("operation")
    if isinstance(operation, dict) and not operation.get("ok"):
        detail = (operation.get("error") or operation.get("reason")
                  or operation.get("message") or "原因未上报")
        parts.append(f"操作失败：{detail}")
    for key in ("error", "message", "stderr"):
        value = result.get(key)
        if value and str(value) not in "".join(parts):
            parts.append(str(value))
    handshake = result.get("handshake")
    if handshake:
        parts.append(f"握手期间收到 {len(handshake)} 条：" + "、".join(
            str(item)[:60] for item in handshake[:6]))
    elif "handshake" in result:
        parts.append("握手期间一条消息都没收到（连接建立了，但设备/通道没有任何回应）")
    identifier = result.get("identifier")
    if identifier:
        parts.append(f"使用的设备标识 {identifier}")
    state = result.get("state")
    if isinstance(state, dict):
        parts.append(f"ATC 会话号 {state.get('session')}，"
                     f"Grappa {'有' if state.get('grappa') else '无'}")
    hint = result.get("hint")
    if hint:
        parts.append(str(hint))
    return "；".join(parts) if parts else "未知原因（设备未给出细节）"


Reporter = Callable[[str], None] | None


def _report(reporter: Reporter, text: str) -> None:
    if reporter is not None:
        try:
            reporter(text)
        except Exception:  # noqa: BLE001
            pass


# 对外公开的同名入口（跨模块复用，避免 import 私有名）
report = _report


# 最近一次 airlift 同步（ATC）的完整结果。
# 用途：① 通道没打通时（exitCode 3 = 未收到 SyncAllowed），重试多少次、换多少
#      文件都是同样的结果 —— 不短路的话「读卡包原图」会连试十几个候选、每个
#      干等 25 秒，用户要白等好几分钟；② 界面诊断可直接取用，不必再跑握手。
_LAST_ATC: dict[str, Any] = {}


def last_atc() -> dict[str, Any]:
    """最近一次 airlift 同步结果的副本（供诊断与短路判断使用）。"""
    return dict(_LAST_ATC)


def _remember_atc(result: Any) -> None:
    _LAST_ATC.clear()
    if isinstance(result, dict):
        _LAST_ATC.update(result)


def channel_dead(result: dict[str, Any] | None = None) -> bool:
    """airlift 通道是否根本没打通（设备始终没授予 SyncAllowed）。"""
    payload = result if result is not None else _LAST_ATC
    return bool(payload) and payload.get("exitCode") == 3


def write_file(udid: str, target: str, leaf: str, payload: bytes,
               retries: int = 3, logger: Callable[[str], None] | None = None,
               reporter: Reporter = None) -> bool:
    total = max(1, retries)
    for attempt in range(1, total + 1):
        try:
            token = secrets.token_hex(10)
            source = f"{SOURCE_PREFIX}{token}"
            link_destination = f"{LINK_PREFIX}{token}"
            recovered = f"{RECOVERED_PREFIX}{token}"

            link_identifier = f"../../{source}/p0/p1/p2/link"
            payload_identifier = f"../../{source}/payload"
            identifiers = [link_identifier, payload_identifier]
            destinations = [link_destination, posixpath.join(link_destination, leaf)]

            with tempfile.TemporaryDirectory(prefix="airlift-write-") as temporary:
                work = Path(temporary)
                archive_path = work / "payload.zip"
                books_path = work / "Books.plist"
                snapshot_root = work / "books-snapshot"
                snapshot_root.mkdir()

                archive_path.write_bytes(build_archive(target, payload))
                books_path.write_bytes(build_books(identifiers))

                snapshot = native("snapshot-books", udid, str(snapshot_root))
                if not operation_ok(snapshot):
                    _report(reporter,
                            f"写入 {leaf}：Books 快照失败（第 {attempt}/{total} 次）"
                            f" —— {describe_failure(snapshot)}")
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return False

                stage = native("stage", udid, source, link_destination, recovered,
                               str(archive_path), str(books_path), str(snapshot_root))
                if not operation_ok(stage):
                    _report(reporter,
                            f"写入 {leaf}：stage 准备失败（第 {attempt}/{total} 次）"
                            f" —— {describe_failure(stage)}")
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return False

                from ..native.airtraffic import run_airtraffic_sync
                result = run_airtraffic_sync(
                    udid, list(zip(identifiers, destinations)), logger=logger)
                _remember_atc(result)

                finish = native("finish-write", udid, source, link_destination,
                                recovered, str(snapshot_root))

            if result.get("exitCode") == 0 and result.get("ok") and operation_ok(finish):
                return True
            _report(reporter,
                    f"写入 {leaf}：同步未成功（第 {attempt}/{total} 次）"
                    f" —— {describe_failure(result)}"
                    + ("" if operation_ok(finish) else f"；收尾：{describe_failure(finish)}"))
            if channel_dead(result):
                _report(reporter,
                        f"写入 {leaf}：airlift 通道未打通，重试没有意义，已放弃后续尝试。")
                return False
        except Exception as error:  # noqa: BLE001
            _report(reporter,
                    f"写入 {leaf}：异常（第 {attempt}/{total} 次）—— {error!r}")
        if attempt < retries:
            time.sleep(0.3 * attempt)
    return False


def write_files_batch(
    udid: str,
    target: str,
    files: list[tuple[str, bytes]],
    retries: int = 3,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    logger: Callable[[str], None] | None = None,
    reporter: Reporter = None,
) -> bool:
    if not files:
        return True

    from ..native.airtraffic import run_airtraffic_sync

    total = max(1, retries)
    for attempt in range(1, total + 1):
        try:
            token = secrets.token_hex(10)
            source = f"{SOURCE_PREFIX}{token}"
            link_destination = f"{LINK_PREFIX}{token}"
            recovered = f"{RECOVERED_PREFIX}{token}"

            link_identifier = f"../../{source}/p0/p1/p2/link"
            identifiers = [link_identifier]
            destinations = [link_destination]
            for index, (leaf, _payload) in enumerate(files):
                identifiers.append(f"../../{source}/payload_{index}")
                destinations.append(posixpath.join(link_destination, leaf))

            with tempfile.TemporaryDirectory(prefix="airlift-batch-") as temporary:
                work = Path(temporary)
                archive_path = work / "payload.zip"
                books_path = work / "Books.plist"
                snapshot_root = work / "books-snapshot"
                snapshot_root.mkdir()

                archive_path.write_bytes(build_archive_multi(target, files))
                books_path.write_bytes(build_books(identifiers))

                snapshot = native("snapshot-books", udid, str(snapshot_root))
                if not operation_ok(snapshot):
                    _report(reporter,
                            f"批量写入：Books 快照失败（第 {attempt}/{total} 次）"
                            f" —— {describe_failure(snapshot)}")
                    if attempt < retries:
                        time.sleep(0.4 * attempt)
                        continue
                    return False

                stage = native("stage", udid, source, link_destination, recovered,
                               str(archive_path), str(books_path), str(snapshot_root),
                               timeout=60.0)
                if not operation_ok(stage):
                    _report(reporter,
                            f"批量写入：stage 准备失败（第 {attempt}/{total} 次）"
                            f" —— {describe_failure(stage)}")
                    if attempt < retries:
                        time.sleep(0.4 * attempt)
                        continue
                    return False

                atc = run_airtraffic_sync(
                    udid, list(zip(identifiers, destinations)),
                    on_progress=progress_callback, logger=logger)
                _remember_atc(atc)

                finish = native("finish-write", udid, source, link_destination,
                                recovered, str(snapshot_root))

            if atc.get("exitCode") == 0 and atc.get("ok") and operation_ok(finish):
                return True
            _report(reporter,
                    f"批量写入：同步未成功（第 {attempt}/{total} 次）"
                    f" —— {describe_failure(atc)}"
                    + ("" if operation_ok(finish) else f"；收尾：{describe_failure(finish)}"))
            if channel_dead(atc):
                _report(reporter,
                        "批量写入：airlift 通道未打通，重试没有意义，已放弃后续尝试。")
                return False
        except Exception as error:  # noqa: BLE001
            _report(reporter,
                    f"批量写入：异常（第 {attempt}/{total} 次）—— {error!r}")
        if attempt < retries:
            time.sleep(0.4 * attempt)
    return False


def remove_files(udid: str, target: str, leaves: list[str], retries: int = 3,
                 reporter: Reporter = None) -> bool:
    """Unlinks files through the relocated Airlift symlink.

    A real unlink is required: Wallet only rebuilds rendered card faces when the
    previous cache entries are actually gone.
    """
    if not leaves:
        return True
    if any(not leaf or "/" in leaf or leaf in {".", ".."} for leaf in leaves):
        raise ValueError("cache leaves must be plain file names")

    from ..native.airtraffic import run_airtraffic_sync

    for attempt in range(1, max(1, retries) + 1):
        try:
            token = secrets.token_hex(10)
            source = f"{SOURCE_PREFIX}{token}"
            link_destination = f"{LINK_PREFIX}{token}"
            recovered = f"{RECOVERED_PREFIX}{token}"
            link_identifier = f"../../{source}/p0/p1/p2/link"
            protected_identifiers = [f"../../{link_destination}/{leaf}" for leaf in leaves]
            removed_destinations = [f"{source}/removed-{index}"
                                    for index in range(len(leaves))]

            with tempfile.TemporaryDirectory(prefix="airlift-remove-") as temporary:
                work = Path(temporary)
                archive_path = work / "payload.zip"
                books_path = work / "Books.plist"
                snapshot_root = work / "books-snapshot"
                snapshot_root.mkdir()

                archive_path.write_bytes(build_archive(target, b"aircard-v2"))
                books_path.write_bytes(build_books([link_identifier, *protected_identifiers]))

                snapshot = native("snapshot-books", udid, str(snapshot_root))
                if not operation_ok(snapshot):
                    raise RuntimeError("无法记录 Books 状态快照 —— "
                                       f"{describe_failure(snapshot)}")
                stage = native("stage", udid, source, link_destination, recovered,
                               str(archive_path), str(books_path), str(snapshot_root),
                               timeout=60.0)
                if not operation_ok(stage):
                    raise RuntimeError(f"无法准备缓存清理 —— {describe_failure(stage)}")

                pairs: list[tuple[str, str]] = [(link_identifier, link_destination)]
                pairs.extend(zip(protected_identifiers, removed_destinations))
                atc = run_airtraffic_sync(udid, pairs)
                if atc.get("exitCode") != 0 or not atc.get("ok"):
                    native("finish-write", udid, source, link_destination,
                           recovered, str(snapshot_root))
                    raise RuntimeError(f"无法搬迁缓存链接 —— {describe_failure(atc)}")

                finish = native("finish-moved-removal", udid, source, link_destination,
                                recovered, str(snapshot_root), str(len(leaves)))
                if operation_ok(finish):
                    return True
                raise RuntimeError(f"收尾清理未完成 —— {describe_failure(finish)}")
        except Exception as error:  # noqa: BLE001
            _report(reporter,
                    f"删除 {posixpath.basename(target)}/*（第 {attempt}/{max(1, retries)} 次）："
                    f"{error}")
        if attempt < retries:
            time.sleep(0.4 * attempt)
    return False


def read_file(udid: str, target: str, leaf: str, retries: int = 1,
              reporter: Reporter = None) -> bytes | None:
    """Exports one file out of the sandbox into Media, reads it, restores it."""
    if "/" in leaf or leaf in ("", ".", ".."):
        raise ValueError("leaf must be a plain file name")

    from ..native.airtraffic import run_airtraffic_sync

    total = max(1, retries)
    for attempt in range(1, total + 1):
        # 这些变量在 finally 的清理里要用；先给默认值，避免变量赋值前异常导致
        # finally 里 NameError。
        source = link_destination = recovered = ""
        snapshot_root = ""
        try:
            token = secrets.token_hex(10)
            source = f"{SOURCE_PREFIX}{token}"
            link_destination = f"{LINK_PREFIX}{token}"
            recovered = f"{RECOVERED_PREFIX}{token}"

            link_identifier = f"../../{source}/p0/p1/p2/link"
            target_path = posixpath.join(target, leaf)
            target_identifier = posixpath.relpath(target_path, AIRLOCK_ROOT)
            identifiers = [link_identifier, target_identifier]
            destinations = [link_destination, recovered]

            with tempfile.TemporaryDirectory(prefix="airlift-read-") as temporary:
                work = Path(temporary)
                archive_path = work / "payload.zip"
                books_path = work / "Books.plist"
                local_out = work / "recovered.bin"
                snapshot_root = work / "books-snapshot"
                snapshot_root.mkdir()

                archive_path.write_bytes(build_archive(target, b"aircard-backup-staging"))
                books_path.write_bytes(build_books(identifiers))

                snapshot = native("snapshot-books", udid, str(snapshot_root))
                if not operation_ok(snapshot):
                    _report(reporter, f"读取 {leaf}：Books 快照失败"
                                      f"（第 {attempt}/{total} 次）"
                                      f" —— {describe_failure(snapshot)}")
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return None

                stage = native("stage", udid, source, link_destination, recovered,
                               str(archive_path), str(books_path), str(snapshot_root))
                if not operation_ok(stage):
                    _report(reporter, f"读取 {leaf}：stage 准备失败"
                                      f"（第 {attempt}/{total} 次）"
                                      f" —— {describe_failure(stage)}")
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return None

                atc = run_airtraffic_sync(udid, list(zip(identifiers, destinations)))
                _remember_atc(atc)
                if atc.get("exitCode") != 0 or not atc.get("ok"):
                    _report(reporter, f"读取 {leaf}：同步未成功"
                                      f"（第 {attempt}/{total} 次）"
                                      f" —— {describe_failure(atc)}")
                    if channel_dead(atc):
                        _report(reporter,
                                f"读取 {leaf}：airlift 通道未打通，"
                                "重试没有意义，已放弃后续尝试。")
                        return None
                    if attempt < retries:
                        time.sleep(0.3 * attempt)
                        continue
                    return None

                # 设备把文件重定位到 Media/recovered 是异步的：ATC 返回"成功"后，
                # 文件未必已经落在 AFC 可读的位置。某些卡片（尤其刚刷入过、或大文件）
                # 会出现"重定位完成滞后"——此时 AFC 立刻读会报"文件不存在"
                # （退出码 2：设备未生成可读取的文件）。这里对 AFC 读回做几次轮询重试，
                # 给设备一点落地时间，避免把本可成功的读取误判为失败。
                read = None
                for afc_try in range(1, 5):
                    read = native("afc-read", udid, recovered, str(local_out))
                    if operation_ok(read) and local_out.is_file():
                        break
                    if afc_try < 4:
                        time.sleep(1.0)
                if not operation_ok(read) or not local_out.is_file():
                    detail = describe_failure(read)
                    if "未生成可读取的文件" in detail or "找不到该路径" in detail:
                        detail += ("（多为重定位时序问题：可重试；若始终如此，"
                                   "则该卡可能从未生成合并卡面文件，本就无法读取）")
                    _report(reporter,
                            f"读取 {leaf}：AFC 读回失败 —— {detail}；"
                            "（设备未能把文件放到可读取的位置，原文件可能仍留在 "
                            "Media/recovered，也可能从未被移动；卡包不会被改动，"
                            "可稍后重试。）")
                    return None
                data = local_out.read_bytes()

                restored = write_file(udid, target, leaf, data, retries=3,
                                      reporter=reporter)
                finish = native("finish-write", udid, source, link_destination,
                                recovered, str(snapshot_root))
                if not restored:
                    _report(reporter,
                            f"读取 {leaf}：已取到 {len(data)} 字节，但写回原位失败 —— "
                            "该卡面文件目前留在设备的 Media/recovered 中，"
                            "请在软件里点「恢复原皮」用本地备份写回。")
                if restored and operation_ok(finish):
                    return data
                if data:
                    return data
                return None
        except Exception as error:  # noqa: BLE001
            _report(reporter, f"读取 {leaf}：异常"
                              f"（第 {attempt}/{total} 次）—— {error!r}")
        finally:
            # 关键修复：无论本次尝试成功还是失败，都必须清理 source/link/recovered，
            # 否则这些临时路径会残留在设备上，导致下一次 read/write 的 stage 因
            # fresh_paths=False 直接失败（日志里表现为「stage 准备失败」），进而
            # 引发一连串连锁失败。原代码只在 atc 失败 / 末尾成功路径清理，
            # afc-read 失败时直接 return，遗漏了清理。
            if source and snapshot_root:
                try:
                    native("finish-write", udid, source, link_destination,
                           recovered, str(snapshot_root))
                except Exception:  # noqa: BLE001
                    pass
        if attempt < retries:
            time.sleep(0.3 * attempt)
    return None


def invalidate_cache(udid: str, card_hash: str) -> bool:
    """Removes every rendered card face so Wallet must rebuild from the pass."""
    ok = True
    for ext in (".cache", ".pkcache"):
        cache_dir = f"/var/mobile/Library/Passes/Cards/{card_hash}{ext}"
        try:
            ok = remove_files(udid, cache_dir, list(CACHE_FILES)) and ok
        except Exception:
            ok = False
    return ok
