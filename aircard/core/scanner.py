"""LiveWallet card hash discovery from the unified device log."""
from __future__ import annotations

import re
from typing import Iterable, Iterator

CARD_REGEXES = [
    # 注意：\) 是"字面右括号"，不能写成 \\) —— 后者会提前闭合分组。
    re.compile(
        r"/(?:Cards|Passes/Cards)/([-A-Za-z0-9_+=]{20,44})"
        r"(?:\.pkpass|\.cache|\.pkcache|/|\s|\"|\'|\)|,|$)"),
    re.compile(r"/([-A-Za-z0-9_+=]{20,44})\.(?:pkpass|cache|pkcache)"),
    re.compile(r"(?<![A-Za-z0-9+/_-])([A-Za-z0-9+/_-]{27}=)(?![A-Za-z0-9+/_-])"),
]

_SUBSYSTEM_MARKERS = (
    "passd", "passbook", "passkit", "stockholm", "nanopassd", "wallet", "/cards/",
)

_CONTEXT_MARKERS = (
    "card", "pass", "payment", "pkpass", "uniqueid", "identifier",
    "face", "cache", "stockholm", "/cards/",
)

# 真机实测：PassbookUIService / runningboardd / SpringBoard 会持续广播进程生命周期
# 事件，只要行里出现 passbook / pass / wallet 就会被旧规则判成"钱包相关"，
# 单次扫描刷出 7 万多行噪音，既淹没有效信息也白白消耗 CPU。
# 这里改为**按进程名白名单**判定：日志展示与计数只保留真正的钱包进程。
_WALLET_PROCESSES = (
    "passd", "nanopassd", "passbook", "passkit", "stockholm", "wallet",
)

# 进程白名单还不够：`Passbook(UIKitCore)` 的 `com.apple.Passbook` 会命中 `pass`，
# `passd(Network)` / `passd(libxpc.dylib)` 也都是与卡号无关的底层噪音。
# 再按 image（`process(image)` 括号里的框架名）排除一层。
# 安全性：含卡包路径的行已在 has_hash_hint 处短路返回 True，不受本表影响。
_NOISE_IMAGES = (
    "uikitcore", "uikit", "backlightservices", "frontboard", "runningboard",
    "springboard", "network", "libusrtcp", "libxpc", "libsystem",
    "libdispatch", "cfnetwork", "quic", "coretelephony", "coremedia",
    "mediaserverd", "assertion", "security", "keychain",
    # 真机实测补充：下面这些框架只因为消息里带着 `com.apple.Passbook` / `pass`
    # 就被判成钱包行。其中 CoreFoundation 尤其重要 —— 它同时也会打印真实的
    # 卡包路径，但那些行含 /Passes/Cards，已在 has_hash_hint 处短路保留。
    "corefoundation", "libaccessibility", "accounts", "applemediaservices",
    "libboringssl", "boardservices", "backboardservices", "baseboard",
    "hangtracer", "usernotifications", "libsqlite3", "corelocation",
    "locationsupport", "corebrightness", "libmobilegestalt",
    "axmediautilities", "swiftui", "coreanimation", "imageio",
)

# 卡号一定伴随卡包路径出现。这几个子串大小写敏感、几乎不会误命中，
# 用作"是否值得跑正则"的快速前置判断（比直接跑三条正则便宜一个数量级）。
_HASH_HINT_MARKERS = ("/Passes/Cards", "/Cards/", ".pkpass", ".pkcache")

DUMMY_HASHES = {
    "M6nDwZrkYbFlsodLgCbvyFZQ1cc=",
    "kJL-D0rr-SZhbj2c8nK-OQ9hCMY=",
    "hwAtAmHKYwsQrJbT5cTNDsaxVME=",
}


def has_hash_hint(line: str) -> bool:
    """行内是否含有卡包路径特征（大小写敏感，纯子串判断，极快）。"""
    return any(marker in line for marker in _HASH_HINT_MARKERS)


def _legacy_wallet_line(line: str) -> bool:
    """原版 macOS 的宽松判定：子系统标记 ∩ 上下文标记。"""
    lower = line.lower()
    if not any(marker in lower for marker in _SUBSYSTEM_MARKERS):
        return False
    return any(marker in lower for marker in _CONTEXT_MARKERS)


def _process_name(line: str) -> str:
    """取 `process(image): message` 里的进程名。"""
    return line.split("(", 1)[0].strip().lower()


def _image_name(line: str) -> str:
    """取 `process(image): message` 里的框架 / 镜像名。"""
    head = line.find("(")
    if head < 0:
        return ""
    tail = line.find(")", head + 1)
    return line[head + 1:tail].strip().lower() if tail > head else ""


def _message_part(line: str) -> str:
    """去掉 `process(image)` 前缀，避免 `passd` 里的 `pass` 误命中上下文标记。"""
    head = line.find("(")
    if head < 0:
        return line
    tail = line.find(")", head + 1)
    return line[tail + 1:] if tail > head else line[head:]


def is_wallet_line(line: str) -> bool:
    """是否为"钱包相关"行——只用于日志展示与计数，判定收紧以过滤噪音。"""
    if has_hash_hint(line):
        return True
    name = _process_name(line)
    if not any(marker in name for marker in _WALLET_PROCESSES):
        return False
    image = _image_name(line)
    if any(noise in image for noise in _NOISE_IMAGES):
        return False
    lower = _message_part(line).lower()
    return any(marker in lower for marker in _CONTEXT_MARKERS)


# passd 每次查找卡包资源都会打一条 `Resource lookup`：
#     Request  : cardBackgroundCombined@3x type: png
#     Result   : None                                          ← 不存在
#     Result   : file:///…/Cards/<hash>.pkpass/strip@2x.png    ← 存在
# 合并卡面文件（cardBackgroundCombined@*）是否存在，直接决定了「备份卡面」
# 能不能成功：它是刷入皮肤时才生成的文件，iOS 原生卡面并没有落盘。
_ASSET_REQUEST_RE = re.compile(r"Request\s*:\s*(\S+)\s+type:\s*\w+")
_ASSET_RESULT_RE = re.compile(
    r"Result\s*:\s*(?:file:///var/mobile/Library/Passes/Cards/"
    r"([-A-Za-z0-9_+=]{20,44})\.pkpass/(\S+)|(None))")


def resource_hints(line: str) -> Iterator[tuple[str, str, bool]]:
    """从 `Resource lookup` 记录里读出「某张卡有没有某个资源」。

    产出 (card_hash, 资源名, 是否存在)。Result 为 None 时拿不到卡号，
    card_hash 会是空串——由调用方决定如何解读。
    """
    match = _ASSET_RESULT_RE.search(line)
    if not match:
        return
    card_hash, leaf, missing = match.group(1), match.group(2), match.group(3)
    if missing:
        request = _ASSET_REQUEST_RE.search(line)
        if request:
            yield ("", request.group(1), False)
        return
    if card_hash and leaf:
        yield (card_hash, leaf, True)


def hash_source(line: str) -> str:
    """卡号是从哪类证据里来的。

    "path"  —— 行内含 /Passes/Cards/… 或 .pkpass/.pkcache，基本可确认是真卡；
    "token" —— 只匹配到第 3 条"裸 base64"正则，可能是日志里的无关 ID。
    仅用于提示与诊断，不做任何过滤（宁可多报一张假卡，也不漏真卡）。
    """
    return "path" if has_hash_hint(line) else "token"


def should_scan(line: str) -> bool:
    """是否值得跑卡号正则——**宁可多扫，绝不漏卡**。

    取"宽松原版规则 ∪ 路径提示"的并集，是旧行为的超集：
    收紧 is_wallet_line 只会让日志更干净，不会让任何一张卡片漏掉。
    """
    return has_hash_hint(line) or _legacy_wallet_line(line)


def _iter_hashes(patterns, line: str) -> Iterator[str]:
    for pattern in patterns:
        for match in pattern.finditer(line):
            candidate = match.group(1).strip().strip("'\"").rstrip(".").rstrip(",")
            if len(candidate) == 36 and "-" in candidate:
                continue
            if candidate in DUMMY_HASHES:
                continue
            if candidate:
                yield candidate


def extract_hashes(line: str) -> Iterator[str]:
    """跑全部三条正则（含"裸哈希"那条），用于钱包进程的行。"""
    yield from _iter_hashes(CARD_REGEXES, line)


def extract_path_hashes(line: str) -> Iterator[str]:
    """只跑前两条"路径型"正则。

    命中路径提示的行一定含有 /Passes/Cards/… 或 .pkpass/.pkcache，
    此时再跑第 3 条"裸哈希"正则，反而容易把日志里的无关 base64 当成卡号。
    """
    yield from _iter_hashes(CARD_REGEXES[:2], line)


def scan_lines(lines: Iterable[str], known: set[str] | None = None) -> Iterator[str]:
    """Yields freshly discovered hashes from a stream of log lines."""
    seen = set(known or ())
    for line in lines:
        if not should_scan(line):
            continue
        for candidate in extract_hashes(line):
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
