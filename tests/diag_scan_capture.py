"""临时诊断脚本：抓取真实设备统一日志流，定位"扫描识别不出卡片"的断点。

仅用于排障，不属于交付产物；排查结束后删除。

用法：
    python tests/diag_scan_capture.py --seconds 40
    python tests/diag_scan_capture.py --udid 00008030-00054C320AD1402E --seconds 40

运行期间请在 iPhone 上操作：双击侧键 -> Face ID 验证 -> 点击要识别的卡片。
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aircard.core.device import DeviceLogStream, trace_log_line, _SCANNER_PREFIX  # noqa: E402
from aircard.core.scanner import CARD_REGEXES, extract_hashes, is_wallet_line  # noqa: E402
from aircard.native.mobiledevice import md_shared  # noqa: E402

INTERESTING = re.compile(
    r"passd|passbook|passkit|stockholm|nanopassd|wallet|/cards/|pkpass|pkcache|<private>",
    re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--udid", default="")
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--out", default="scan_capture.log")
    args = parser.parse_args()

    md = md_shared()
    devices = md.device_list() or []
    print(f"[枚举] MobileDevice 设备数：{len(devices)}")
    detected: list[str] = []
    for item in devices:
        udid_found = ""
        if isinstance(item, dict):
            udid_found = str(item.get("udid") or item.get("UniqueDeviceID") or "")
        else:
            try:
                udid_found = str(md.identifier_of(item) or "")
            except Exception:
                udid_found = ""
        if udid_found:
            detected.append(udid_found)
        print(f"  - {udid_found or item}")

    udid = args.udid or (detected[0] if detected else "")
    if not udid:
        print("[失败] 没有可用 UDID，请用 --udid 指定。")
        return 2
    print(f"[目标] UDID = {udid}")

    stream = DeviceLogStream(udid)
    ok, message = stream.open(timeout=30.0)
    print(f"[连接] {message}")
    if not ok:
        return 2

    stats = collections.Counter()
    lines: list[str] = []
    interesting: list[str] = []
    hashes: set[str] = set()
    wallet_lines: list[str] = []
    hashes_no_filter: set[str] = set()
    hexdumps: list[str] = []
    lock = threading.Lock()
    stop = threading.Event()

    def pump() -> None:
        reader = stream._reader
        try:
            while not stop.is_set():
                kind, payload, error = reader.frame()
                if kind == 0:
                    stats["error"] += 1
                    with lock:
                        lines.append(f"[[FRAME ERROR]] {error}\n")
                    return
                stats["frames_total"] += 1
                if kind == 1:
                    stats["frames_kind1"] += 1
                    continue
                if kind != 2:
                    stats["frames_other"] += 1
                    continue
                stats["frames_kind2"] += 1
                if len(hexdumps) < 3 and len(payload) >= 129:
                    hexdumps.append(
                        f"record len={len(payload)} head={payload[:129].hex()}\n"
                        f"  u32@5={int.from_bytes(payload[5:9], 'little')} "
                        f"u16@37={int.from_bytes(payload[37:39], 'little')} "
                        f"u16@107={int.from_bytes(payload[107:109], 'little')} "
                        f"u32@109={int.from_bytes(payload[109:113], 'little')}\n"
                    )
                line = trace_log_line(payload)
                if line is None:
                    stats["record_parse_none"] += 1
                    continue
                stats["lines_ok"] += 1
                with lock:
                    lines.append(line)
                    if len(interesting) < 400 and INTERESTING.search(line):
                        interesting.append(line)
                    if is_wallet_line(line):
                        stats["lines_wallet"] += 1
                        if len(wallet_lines) < 200:
                            wallet_lines.append(line)
                        for cand in extract_hashes(line):
                            hashes.add(cand)
                    for cand in extract_hashes(line):
                        hashes_no_filter.add(cand)
        except Exception as exc:  # pragma: no cover
            stats["exception"] += 1
            with lock:
                lines.append(f"[[EXCEPTION]] {exc!r}\n")

    worker = threading.Thread(target=pump, daemon=True)
    worker.start()
    print(f"[抓取] 开始，持续 {args.seconds:g} 秒 —— 请现在操作 iPhone 上的 Apple Pay ...")
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        time.sleep(2.0)
        with lock:
            print(f"  帧 {stats['frames_total']} / 行 {stats['lines_ok']} / "
                  f"钱包行 {stats['lines_wallet']} / 哈希 {len(hashes)}", flush=True)
    stop.set()
    stream.stop()
    worker.join(timeout=6.0)
    try:
        stream.close()
    except Exception:
        pass

    with lock:
        captured = list(lines)
        interesting_out = list(interesting)
        wallet_out = list(wallet_lines)
        hash_out = set(hashes)
        hash_nf = set(hashes_no_filter)
        hex_out = list(hexdumps)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    with open(out_path, "w", encoding="utf-8", errors="replace") as handle:
        handle.writelines(captured)
    base, ext = os.path.splitext(out_path)
    with open(base + "_wallet" + ext, "w", encoding="utf-8", errors="replace") as handle:
        handle.writelines(wallet_out)
    with open(base + "_interesting" + ext, "w", encoding="utf-8", errors="replace") as handle:
        handle.writelines(interesting_out)
    with open(base + "_hex" + ext, "w", encoding="utf-8", errors="replace") as handle:
        handle.writelines(hex_out)

    print("\n================ 统计 ================")
    for key in ("frames_total", "frames_kind1", "frames_kind2", "frames_other",
                "record_parse_none", "lines_ok", "lines_wallet", "error", "exception"):
        print(f"  {key:<20} {stats.get(key, 0)}")
    print(f"  {'哈希(经钱包过滤)':<20} {len(hash_out)}")
    print(f"  {'哈希(不过滤全行)':<20} {len(hash_nf)}")
    print()
    if hash_out:
        print("  命中哈希（钱包过滤后）:")
        for item in sorted(hash_out):
            print(f"    {item}")
    if hash_nf - hash_out:
        print("  不过滤才命中的哈希（说明 is_wallet_line 过严）:")
        for item in sorted(hash_nf - hash_out):
            print(f"    {item}")

    print()
    print("  进程 TOP20:")
    counter = collections.Counter()
    for item in captured:
        name = item.split("(", 1)[0]
        counter[name] += 1
    for name, count in counter.most_common(20):
        print(f"    {count:>6}  {name}")

    print()
    print(f"  原始日志  -> {out_path}")
    print(f"  钱包行    -> {base}_wallet{ext}  ({len(wallet_out)} 行)")
    print(f"  相关行    -> {base}_interesting{ext}  ({len(interesting_out)} 行)")
    print(f"  记录头    -> {base}_hex{ext}")

    print()
    print("  相关行样本（最多 25 条）:")
    for item in interesting_out[:25]:
        print("    " + item.rstrip("\n")[:220])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
