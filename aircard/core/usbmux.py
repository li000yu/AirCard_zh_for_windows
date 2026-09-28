"""usbmux 协议客户端，仅用于连通性诊断与设备在线确认。

Windows 的 Apple Mobile Device Service 在 127.0.0.1:27015 提供 usbmuxd 服务。
新版协议使用 16 字节头 + 二进制 plist：

    struct { uint32 length; uint32 version; uint32 message; uint32 tag; }

其中 version=1、message=8（plist 载荷）。iTunes 自带的 MobileDevice.dll 在
Windows 上的设备通知回调不可靠，因此这里独立实现一份查询，用来给用户明确的
可操作提示（未信任 / 未连接 / 服务未运行）。
"""

from __future__ import annotations

import plistlib
import socket
import struct
from typing import Any

USBMUX_HOST = "127.0.0.1"
USBMUX_PORT = 27015

_HEADER = struct.Struct("<IIII")
_VERSION = 1
_MESSAGE_PLIST = 8


def _round_trip(payload: dict[str, Any], timeout: float = 4.0) -> dict[str, Any]:
    body = plistlib.dumps(payload, fmt=plistlib.FMT_BINARY)
    packet = _HEADER.pack(len(body) + _HEADER.size, _VERSION, _MESSAGE_PLIST, 1) + body
    with socket.create_connection((USBMUX_HOST, USBMUX_PORT), timeout=timeout) as sock:
        sock.sendall(packet)
        header = _recv_exact(sock, _HEADER.size)
        if len(header) < _HEADER.size:
            return {"_error": "响应头不完整"}
        length, _version, _message, _tag = _HEADER.unpack(header)
        remaining = max(0, int(length) - _HEADER.size)
        body = _recv_exact(sock, remaining) if remaining else b""
    try:
        return plistlib.loads(body) if body else {}
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"响应解析失败：{exc}"}


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    left = size
    while left > 0:
        chunk = sock.recv(left)
        if not chunk:
            break
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def list_devices(timeout: float = 4.0) -> list[dict[str, Any]]:
    """返回 usbmux 报告的设备属性列表；异常时返回空列表。"""
    try:
        reply = _round_trip({
            "MessageType": "ListDevices",
            "ClientVersionString": "aircard-windows",
            "ProgName": "AirCard",
            "kLibUSBMuxVersion": 3,
        }, timeout=timeout)
    except OSError:
        return []
    except Exception:  # noqa: BLE001
        return []
    devices: list[dict[str, Any]] = []
    for entry in reply.get("DeviceList", []) or []:
        props = entry.get("Properties") or {}
        if isinstance(props, dict):
            props = dict(props)
            props["DeviceID"] = entry.get("DeviceID")
            devices.append(props)
    return devices


def probe(timeout: float = 4.0) -> dict[str, Any]:
    """综合探测：端口可达性 + 设备列表。绝不抛异常。"""
    result: dict[str, Any] = {
        "reachable": False,
        "devices": [],
        "serials": [],
        "error": "",
    }
    try:
        with socket.create_connection((USBMUX_HOST, USBMUX_PORT), timeout=timeout):
            result["reachable"] = True
    except OSError as exc:
        result["error"] = f"无法连接 usbmux（{USBMUX_HOST}:{USBMUX_PORT}）：{exc}"
        return result
    try:
        reply = _round_trip({
            "MessageType": "ListDevices",
            "ClientVersionString": "aircard-windows",
            "ProgName": "AirCard",
            "kLibUSBMuxVersion": 3,
        }, timeout=timeout)
    except OSError as exc:
        result["error"] = f"usbmux 通信失败：{exc}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"usbmux 通信异常：{exc}"
        return result
    if "_error" in reply:
        result["error"] = str(reply["_error"])
        return result
    for entry in reply.get("DeviceList", []) or []:
        props = entry.get("Properties") or {}
        if not isinstance(props, dict):
            continue
        props = dict(props)
        props["DeviceID"] = entry.get("DeviceID")
        result["devices"].append(props)
        serial = props.get("SerialNumber")
        if serial:
            result["serials"].append(str(serial))
    return result
