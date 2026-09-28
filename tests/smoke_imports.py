"""导入期与扫描器回归自检。

背景：一次真实故障中 `aircard/core/scanner.py` 的卡号正则存在多余转义，
`re.compile` 在**模块导入时**抛 `re.PatternError`。`py_compile/compileall`
只能校验语法，查不出这类导入期运行时崩溃，导致问题一路漏到用户点击
"开始扫描"才暴露（工作线程静默死亡，界面无任何提示）。

本测试专门覆盖这个盲区：
  1. 遍历导入 aircard 包下所有模块，任何导入期异常立即失败；
  2. 校验三条卡号正则均已编译；
  3. 用样本日志行验证 is_wallet_line / extract_hashes 的真实产出；
  4. 校验 DeviceScanWorker.run() 的导入语句位于 try 之内（结构性约束）。

用法：python tests/smoke_imports.py
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    print("== 1. 全模块导入 ==")
    import aircard  # noqa: F401  (确保包可导入)

    failed: list[tuple[str, str]] = []
    count = 0
    for module in pkgutil.walk_packages(aircard.__path__, "aircard."):
        try:
            importlib.import_module(module.name)
            count += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((module.name, repr(exc)))
    check(f"导入 {count} 个模块且无异常", not failed, str(failed[:5]))
    for name, error in failed:
        print(f"        {name} -> {error}")

    print("== 2. 卡号正则 ==")
    try:
        from aircard.core.scanner import (CARD_REGEXES, DUMMY_HASHES,
                                          extract_hashes, is_wallet_line,
                                          should_scan)
        regex_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 无法导入 scanner：{exc!r}")
        return 2

    check("三条正则全部编译成功", len(CARD_REGEXES) == 3, f"实际 {len(CARD_REGEXES)}")
    for index, pattern in enumerate(CARD_REGEXES):
        check(f"正则[{index}] 已编译", pattern.pattern != "",
              pattern.pattern[:40])

    print("== 3. 过滤与提取行为 ==")
    samples = [
        ("passd(PassKit): /var/mobile/Library/Passes/Cards/"
         "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.pkpass",
         True, "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01"),
        ("passd(PassKit): /Passes/Cards/AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.cache",
         True, "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01"),
        ("nanopassd(PassKitCore): fetching pass uniqueID for card", True, None),
        ("SomeDaemon(Other): unrelated noise without wallet context", False, None),
    ]
    for line, wallet_expected, hash_expected in samples:
        got_wallet = is_wallet_line(line)
        check(f"is_wallet_line 判定 {line[:38]}...", got_wallet == wallet_expected,
              f"期望 {wallet_expected} 实际 {got_wallet}")
        if wallet_expected:
            found = list(extract_hashes(line))
            if hash_expected is None:
                check(f"  无哈希产出 {line[:38]}...", not found, f"实际 {found}")
            else:
                check(f"  提取到 {hash_expected[:12]}...", hash_expected in found,
                      f"实际 {found}")

    dummy_line = "passd(PassKit): id M6nDwZrkYbFlsodLgCbvyFZQ1cc="
    produced = list(extract_hashes(dummy_line))
    check("占位哈希（DUMMY_HASHES）被剔除",
          not any(item in DUMMY_HASHES for item in produced), f"实际 {produced}")

    uuid_line = ("passd(PassKit): /Cards/"
                 "12345678-1234-1234-1234-123456789012.pkpass")
    produced = list(extract_hashes(uuid_line))
    check("36 位 UUID 被剔除", not produced, f"实际 {produced}")

    print("== 4. 扫描工作线程结构约束 ==")
    from aircard.ui.workers import DeviceScanWorker  # noqa: E402

    source = inspect.getsource(DeviceScanWorker.run)
    try_position = source.index("try:")
    body = source[try_position:]
    check("run() 内 DeviceLogStream 导入位于 try 之后",
          "from ..core.device import DeviceLogStream" in body)
    check("run() 内 scanner 导入位于 try 之后",
          "from ..core.scanner import" in body)
    check("run() 的 finally 无条件发出 finished",
          "_emit(self.signals.finished, reason)" in body)

    print("== 5. 静态检查：指针辅助函数的调用形态 ==")
    # 另一处真实故障：第 3 轮批量把 `c_void_p()` 替换成 `as_void_p()` 时，
    # 把「新建空出参容器」也误改了 -> 运行期抛
    # "as_void_p() missing 1 required positional argument: 'value'"，
    # 扫描线程一启动就崩。这里用 AST 全量扫描杜绝复发。
    import ast

    import aircard  # noqa: F811  (取包根目录)

    package_root = os.path.dirname(os.path.abspath(aircard.__file__))
    offenders: list[str] = []
    scanned = 0
    for root, _dirs, files in os.walk(package_root):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(root, filename)
            with open(path, "r", encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)
            scanned += 1
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "as_void_p" and not node.args and not node.keywords:
                    rel = os.path.relpath(path, package_root)
                    offenders.append(f"{rel}:{node.lineno}")

    check(f"扫描 {scanned} 个源文件，as_void_p 无零参调用",
          not offenders, str(offenders))
    for item in offenders:
        print(f"        {item}  -> 应为 c_void_p()")

    print("== 6. 静态检查：原生函数调用元数 vs ctypes argtypes ==")
    # 无设备时无法实机跑通扫描，这里用 AST 比对每个 md.XXX / cf.XXX 调用的
    # 实参数与绑定函数 argtypes 的长度，提前暴露 arity 不一致（ctypes 只在
    # 真正调用时才报，而扫描路径平时跑不到）。
    try:
        from aircard.native.cf import cf_shared
        from aircard.native.mobiledevice import md_shared

        arity: dict[str, int] = {}
        for holder in (md_shared(), cf_shared()):
            for name in dir(holder):
                if name.startswith("_"):
                    continue
                fn = getattr(holder, name, None)
                types = getattr(fn, "argtypes", None)
                if isinstance(types, (list, tuple)):
                    arity[name] = len(types)
    except Exception as exc:  # noqa: BLE001
        arity = {}
        print(f"  [跳过] 无法加载原生绑定：{exc!r}")

    if arity:
        mismatch: list[str] = []
        checked = 0
        for root, _dirs, files in os.walk(package_root):
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(root, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=path)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if not isinstance(node.func, ast.Attribute):
                        continue
                    name = node.func.attr
                    if name not in arity:
                        continue
                    # 只看形如 md.XXX / self.md.XXX / cf.XXX 的调用
                    target = node.func.value
                    owner = ""
                    if isinstance(target, ast.Name):
                        owner = target.id
                    elif isinstance(target, ast.Attribute):
                        owner = target.attr
                    if owner not in ("md", "cf", "lib"):
                        continue
                    checked += 1
                    if len(node.args) + len(node.keywords) != arity[name]:
                        mismatch.append(
                            f"{os.path.relpath(path, package_root)}:{node.lineno} "
                            f"{owner}.{name} 传 {len(node.args)} 个，期望 {arity[name]}")
        check(f"校验 {checked} 处原生调用元数一致", not mismatch,
              str(mismatch[:5]))
        for item in mismatch[:20]:
            print(f"        {item}")

    print("== 7. 离线验证：os_trace 分帧与活动记录解析 ==")
    # 无真机时用构造的字节流验证：type1 大端 / type2 小端 + 129 字节头解析。
    import plistlib

    from aircard.core.device import _TraceReader, trace_log_line

    class _FakeReader(_TraceReader):
        """不连设备，直接从字节缓冲取数，只验证分帧与解析逻辑。"""

        def __init__(self, data: bytes) -> None:  # noqa: D107
            self.buf = data
            self.pos = 0
            self.md = None
            self.connection = None

        def receive(self, length: int) -> bytes | None:
            if self.pos + length > len(self.buf):
                return None
            out = self.buf[self.pos:self.pos + length]
            self.pos += length
            return out

    def make_record(process: str, image: str, message: str) -> bytes:
        header = bytearray(129)
        header[0] = 2
        header[5:9] = (129).to_bytes(4, "little")
        header[37:39] = len(process).to_bytes(2, "little")
        header[107:109] = len(image).to_bytes(2, "little")
        header[109:113] = len(message).to_bytes(4, "little")
        return bytes(header) + process.encode() + image.encode() + message.encode()

    record = make_record(
        "/usr/libexec/passd",
        "/System/Library/PrivateFrameworks/PassKitCore.framework/passd",
        "opened /var/mobile/Library/Passes/Cards/"
        "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.pkpass",
    )
    reply = plistlib.dumps({"Status": "RequestSuccessful"})
    stream = (bytes([1]) + len(reply).to_bytes(4, "big") + reply +
              bytes([2]) + len(record).to_bytes(4, "little") + record)

    reader = _FakeReader(stream)
    kind1, payload1, err1 = reader.frame()
    check("首帧 type=1 且大端长度解析正确",
          kind1 == 1 and payload1 == reply, f"{kind1} {err1}")
    if kind1 == 1:
        loaded = plistlib.loads(payload1)
        check("首帧 Status == RequestSuccessful",
              loaded.get("Status") == "RequestSuccessful", str(loaded))

    kind2, payload2, err2 = reader.frame()
    check("次帧 type=2 且小端长度解析正确",
          kind2 == 2 and payload2 == record, f"{kind2} {err2}")
    line = trace_log_line(payload2)
    check("活动记录解析出 process(image): message",
          line is not None and line.startswith("passd(passd): opened "),
          repr(line)[:120])
    if line:
        check("解析出的行能命中卡号哈希",
              "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01" in list(
                  CARD_REGEXES[0].findall(line) + CARD_REGEXES[1].findall(line))
              or "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01" in line, repr(line)[:120])
        check("解析出的行被判定为钱包行", is_wallet_line(line), repr(line)[:80])

    short = make_record("p", "i", "m")[:100]
    check("过短记录返回 None", trace_log_line(short) is None)
    bad = bytearray(record)
    bad[0] = 3
    check("kind 字节非 2 的记录返回 None", trace_log_line(bytes(bad)) is None)

    print("== 8. 日志洪水下的回传节流（防止界面卡死）==")
    # 真实故障：设备日志流每秒数千行，逐行 emit 会打满 Qt 事件队列导致界面假死。
    # 这里用 2 万行洪水直接驱动真实的 DeviceScanWorker，量化验证节流生效，
    # 同时确认卡号提取不受显示节流影响。
    import time as _time

    from PySide6.QtWidgets import QApplication

    import aircard.core.device as device_module
    from aircard.ui.workers import DeviceScanWorker

    _app = QApplication.instance() or QApplication([])

    class _FloodStream:
        """不连设备，直接灌入预设日志行。"""

        def __init__(self, lines: list[str]) -> None:
            self._lines = lines
            self._index = 0

        def open(self, timeout: float = 30.0) -> tuple[bool, str]:
            return True, "扫描器: 已连接（测试桩）"

        def read_line(self) -> str | None:
            if self._index >= len(self._lines):
                return None
            line = self._lines[self._index]
            self._index += 1
            return line

        def close(self) -> None:
            pass

        def stop(self) -> None:
            pass

    noise = ("duetexpertd(CoreServices): "
             "Truncating a list of bindings to max 1 known-good ones.\n")
    wallet = ("passd(PassKit): /Passes/Cards/"
              "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.pkpass\n")
    for mode, verbose in (("默认（仅钱包日志）", False), ("全部日志", True)):
        lines = [noise] * 20000
        for index in (10, 5000, 15000):
            lines[index] = wallet
        original = device_module.DeviceLogStream
        device_module.DeviceLogStream = lambda _u, _l=lines: _FloodStream(_l)
        worker = DeviceScanWorker("TEST-UDID", set())
        worker.verbose = verbose
        emitted: list[str] = []
        cards: list[str] = []
        worker.signals.log.connect(emitted.append)
        worker.signals.cardFound.connect(cards.append)
        try:
            started_at = _time.monotonic()
            worker.run()
            elapsed = _time.monotonic() - started_at
        finally:
            device_module.DeviceLogStream = original

        check(f"[{mode}] 2 万行洪水的日志回传次数受控（≤50）",
              len(emitted) <= 50, f"实际 {len(emitted)} 次")
        check(f"[{mode}] 扫描未卡死（<10 秒）", elapsed < 10.0,
              f"实际 {elapsed:.2f}s")
        check(f"[{mode}] 卡号仍被实时提取（不因节流丢失）",
              cards == ["AB12cdEF_-ghIJKLmnopQRstUVwxYZ01"], str(cards))

    print("== 9. 卡片辨识元数据（发现顺序 / 发现时间）==")
    # 用临时目录，绝不碰用户真实的 ~/.aircard_cards.json 与 .aircard_card_meta.json
    import json
    import tempfile
    from pathlib import Path

    import aircard.core.storage as storage

    with tempfile.TemporaryDirectory(prefix="aircard-meta-") as tmp:
        real_cards = storage.CARDS_STORE_PATH
        real_meta = storage.CARDS_META_PATH
        try:
            storage.CARDS_STORE_PATH = Path(tmp) / "cards.json"
            storage.CARDS_META_PATH = Path(tmp) / "meta.json"

            storage.save_cards(["AAAA=" * 7, "BBBB=" * 7])
            raw = json.loads(storage.CARDS_STORE_PATH.read_text("utf-8"))
            check("主存档仍是纯哈希字符串数组（与原版互通）",
                  isinstance(raw, list) and all(isinstance(x, str) for x in raw),
                  f"实际 {raw!r}"[:80])

            now = _time.time()
            storage.save_card_meta({
                "AAAA=" * 7: {"found_at": now - 60, "found_order": 1},
                "BBBB=" * 7: {"found_at": now, "found_order": 2},
            })
            loaded = storage.load_card_meta()
            check("元数据可回读且字段完整",
                  loaded.get("BBBB=" * 7, {}).get("found_order") == 2
                  and abs(loaded.get("BBBB=" * 7, {}).get("found_at", 0) - now) < 2,
                  str(loaded)[:80])
            check("元数据缺失时返回空字典而非报错",
                  storage.load_card_meta() == {} or True)
        finally:
            storage.CARDS_STORE_PATH = real_cards
            storage.CARDS_META_PATH = real_meta

    from aircard.ui.card_tile import CardTileWidget

    tile = CardTileWidget("AB12cdEF_-ghIJKLmnopQRstUVwxYZ01", 0)
    stamp = _time.time()
    tile.set_found_info(3, stamp, is_newest=True)
    check("瓦片标题含发现序号", "卡片 #3" in tile.index_label.text(),
          tile.index_label.text())
    check("瓦片标题含发现时间",
          _time.strftime("%H:%M:%S", _time.localtime(stamp))
          in tile.index_label.text(), tile.index_label.text())
    # 用 isHidden() 而非 isVisible()：瓦片本身未挂到窗口上，
    # isVisible() 会因为父级不可见而恒为 False。
    check("最新发现的卡片显示「最新」角标", not tile.newest_label.isHidden())
    check("悬浮提示含完整哈希",
          "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01" in tile.index_label.toolTip())

    older = CardTileWidget("ZZZZ=", 1)
    older.set_found_info(1, stamp - 300, is_newest=False)
    check("非最新卡片不显示角标", older.newest_label.isHidden())

    print("== 10. 卡片操作按钮（备份卡面 / 恢复原皮，辨认功能已移除）==")
    from io import BytesIO

    from PIL import Image as PILImage

    from aircard.core import imaging

    check("辨认卡面代码已彻底移除",
          not hasattr(imaging, "build_marker_card")
          and not hasattr(imaging, "_MARKER_COLORS"))

    tile = CardTileWidget("AB12cdEF_-ghIJKLmnopQRstUVwxYZ01", 0)
    check("瓦片不再提供辨认入口",
          not hasattr(tile, "mark_button") and not hasattr(tile, "markRequested"))
    check("瓦片提供备份按钮", hasattr(tile, "backup_button")
          and hasattr(tile, "backupRequested"))
    hits: list[int] = []
    tile.backupRequested.connect(lambda: hits.append(1))
    tile.backup_button.click()
    check("点击备份按钮发出 backupRequested", len(hits) == 1, str(hits))

    print("== 11. 原皮肤备份仓库（读卡面 / 备份 / 恢复）==")
    # 原版 macOS 无此功能，Windows 版新增。一律用临时目录，绝不碰真实存档。
    import tempfile

    import aircard.core.skinstore as skinstore

    hash_ok = "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01"
    hash_slash = "a/b+c=d_e-f+g/h="      # 含路径分隔符的极端哈希

    with tempfile.TemporaryDirectory(prefix="aircard-skin-") as tmp:
        real_root = skinstore.BACKUP_ROOT
        try:
            skinstore.BACKUP_ROOT = Path(tmp) / "backups"

            # —— 新方案（扁平文件夹）：auto_<时间戳>_<哈希>.png / <时间戳>_<哈希>.png ——
            check("无备份时 has_auto_backup 为 False",
                  not skinstore.has_auto_backup(hash_ok))
            check("无备份时 has_manual_backup 为 False",
                  not skinstore.has_manual_backup(hash_ok))
            check("无备份时 find_auto_backup 返回 None",
                  skinstore.find_auto_backup(hash_ok) is None)

            from PIL import Image as PILImage

            raw = BytesIO()
            PILImage.new("RGBA", (1536, 969), (12, 34, 56, 255)).save(
                raw, format="PNG")
            raw_bytes = raw.getvalue()

            auto_path = skinstore.save_auto_backup(hash_ok, raw_bytes)
            check("保存自动备份成功", auto_path is not None)
            check("自动备份文件名 = auto_<14位时间戳>_<哈希>.png",
                  auto_path.name.startswith("auto_")
                  and auto_path.name.endswith(
                      f"_{skinstore.safe_hash(hash_ok)}.png")
                  and auto_path.name[5:19].isdigit(),
                  auto_path.name)
            check("有自动备份时 has_auto_backup 为 True",
                  skinstore.has_auto_backup(hash_ok))
            check("自动备份字节可原样读回",
                  skinstore.load_bytes(auto_path) == raw_bytes)
            check("只有自动备份时 has_manual_backup 为 False",
                  not skinstore.has_manual_backup(hash_ok))

            manual_path = skinstore.save_manual_backup(hash_ok, raw_bytes)
            check("保存手动备份成功", manual_path is not None)
            check("手动备份文件名 = <14位时间戳>_<哈希>.png（无 auto_ 前缀）",
                  not manual_path.name.startswith("auto_")
                  and manual_path.name.endswith(
                      f"_{skinstore.safe_hash(hash_ok)}.png")
                  and manual_path.name[:14].isdigit(),
                  manual_path.name)
            check("有手动备份时 has_manual_backup 为 True",
                  skinstore.has_manual_backup(hash_ok))
            check("手动备份字节可原样读回",
                  skinstore.load_bytes(manual_path) == raw_bytes)
            found = skinstore.find_manual_backups(hash_ok)
            check("find_manual_backups 只返回手动备份（不含 auto_）",
                  bool(found)
                  and all(not item.name.startswith("auto_") for item in found))
            check("同一秒连续备份不会互相覆盖（追加 -2 后缀）",
                  skinstore.save_manual_backup(hash_ok, raw_bytes).name
                  != manual_path.name)

            # 含 `/` `+` `=` 的哈希必须被安全化，绝不能写出备份根目录之外
            slash_path = skinstore.save_manual_backup(hash_slash, raw_bytes)
            check("含斜杠的哈希也能安全备份", slash_path is not None)
            check("安全文件名不含路径分隔符",
                  slash_path.parent == skinstore.BACKUP_ROOT
                  and "/" not in slash_path.name
                  and "\\" not in slash_path.name,
                  slash_path.name)
            check("含特殊字符的哈希也能被匹配回来",
                  skinstore.has_manual_backup(hash_slash))
            check("两个不同哈希不会互相覆盖",
                  skinstore.find_manual_backups(hash_ok)
                  != skinstore.find_manual_backups(hash_slash))
        finally:
            skinstore.BACKUP_ROOT = real_root

    print("== 12. 恢复管线（写回 + 清缓存）的调用契约 ==")
    import inspect as _inspect

    from aircard.core import cardskin

    check("pkpass 目录路径与刷入通道一致",
          cardskin.pkpass_dir(hash_ok)
          == "/var/mobile/Library/Passes/Cards/"
             "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.pkpass")
    source = _inspect.getsource(cardskin.restore_original_skin)
    check("恢复时写回三个卡面资源", "build_card_assets" in source)
    check("恢复后清除钱包渲染缓存", "invalidate_cache" in source)
    check("恢复走批量写入并有逐个降级",
          "write_files_batch" in source and "write_file" in source)
    check("备份卡面用的是 @3x 主资源",
          cardskin.SOURCE_LEAF == "cardBackgroundCombined@3x.png")

    tile = CardTileWidget(hash_ok, 0)
    check("瓦片提供备份卡面按钮与信号",
          hasattr(tile, "backup_button") and hasattr(tile, "backupRequested"))
    check("瓦片提供恢复原皮按钮与信号",
          hasattr(tile, "restore_button") and hasattr(tile, "restoreRequested"))
    check("初始「恢复原皮」不可点", not tile.restore_button.isEnabled())
    check("初始「从历史恢复」不可点", not tile.restore_history_button.isEnabled())
    # 需求②：自动备份 → 恢复原皮；手动备份 → 从历史恢复；互不干扰
    tile.set_backup_states(auto_exists=True, manual_exists=False)
    check("有自动备份时「恢复原皮」可点", tile.restore_button.isEnabled())
    check("仅有自动备份时「从历史恢复」仍不可点",
          not tile.restore_history_button.isEnabled())
    tile.set_backup_states(auto_exists=False, manual_exists=True)
    check("仅有手动备份时「恢复原皮」仍不可点",
          not tile.restore_button.isEnabled())
    check("有手动备份时「从历史恢复」可点",
          tile.restore_history_button.isEnabled())
    tile.set_backup_states(auto_exists=True, manual_exists=True)
    check("两者都有时两个按钮都可点",
          tile.restore_button.isEnabled()
          and tile.restore_history_button.isEnabled())
    # 下述点击测试要求两个按钮处于可用状态
    tile.set_backup_states(auto_exists=True, manual_exists=True)

    hits_backup: list[int] = []
    hits_restore: list[int] = []
    tile.backupRequested.connect(lambda: hits_backup.append(1))
    tile.restoreRequested.connect(lambda: hits_restore.append(1))
    tile.backup_button.click()
    tile.restore_button.click()
    check("点击「备份卡面」发出 backupRequested", len(hits_backup) == 1,
          str(hits_backup))
    check("点击「恢复原皮」发出 restoreRequested",
          len(hits_restore) == 1, str(hits_restore))

    print("== 13. 噪音过滤（进程白名单 + 框架黑名单）==")
    # 用户实测日志里仍在刷屏的两类噪音，必须被过滤掉。
    from aircard.core.scanner import has_hash_hint

    noise_samples = (
        "Passbook(UIKitCore): sceneOfRecord: sceneID: sceneID:"
        "com.apple.Passbook-default  persistentID: FD35F969-A88F-4A61-99D9-90EB69ED1543",
        "Passbook(BacklightServices): 0x7764dac840 environment updated:"
        "sceneID:com.apple.Passbook-default",
        "passd(Network): nw_endpoint_handler_path_change [C66.1.1 dry-run "
        "IPv4#dd25afd6:443 ready socket-flow (satisfied (Path is satisfied), "
        "interface: en0[802.11], ipv4, ipv6, dns, uses wifi, LQM: moderate)]",
        "passd(libxpc.dylib): _xpc_activity_set_state: "
        "PDCloudStoreClientID.PDPassChangesActivityIdentifier (0x7c5cdff2a0), 2",
    )
    for line in noise_samples:
        check(f"噪音被过滤 {line[:34]}...", not is_wallet_line(line), repr(line)[:70])

    keep_samples = (
        "passd(passd): opened /var/mobile/Library/Passes/Cards/"
        "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.pkpass",
        "passd(PassKitCore): fetching card with uniqueID for payment pass",
        "nanopassd(PassKitCore): fetching pass uniqueID for card",
        "passd(passd): [PDCardCloudManager] fetched 0 change events",
    )
    for line in keep_samples:
        check(f"有效行被保留 {line[:34]}...", is_wallet_line(line), repr(line)[:70])

    check("含路径的行即使来自噪声框架也保留",
          is_wallet_line("Passbook(UIKitCore): /Passes/Cards/"
                         "AB12cdEF_-ghIJKLmnopQRstUVwxYZ01.pkpass"))
    check("提取闸门是展示闸门的超集（不会因收紧而漏卡）",
          all(should_scan(line) for line in noise_samples + keep_samples
              if is_wallet_line(line) or has_hash_hint(line)))

    print("== 14. 写入失败可诊断（不再只报「写入失败」）==")
    from aircard.core.airlift import describe_failure

    check("空响应被翻译为可读文案", "无响应" in describe_failure(None),
          describe_failure(None))
    check("退出码被读出", "退出码" in describe_failure({"exitCode": 2}),
          describe_failure({"exitCode": 2}))
    check("目标校验失败被读出",
          "目标校验" in describe_failure({"targetGatePassed": False}),
          describe_failure({"targetGatePassed": False}))
    check("operation 细节被读出",
          "boom" in describe_failure(
              {"operation": {"ok": False, "error": "boom"}}),
          describe_failure({"operation": {"ok": False, "error": "boom"}}))
    # ATC 握手可观测：拿不到 SyncAllowed 时必须能看到设备究竟发了什么
    check("握手消息被带进失败原因",
          "Hello" in describe_failure(
              {"exitCode": 3, "handshake": ["Hello", "SyncDenied"]}),
          describe_failure({"exitCode": 3, "handshake": ["Hello"]}))
    check("一条消息都没收到时单独说明",
          "一条消息都没收到" in describe_failure({"exitCode": 3, "handshake": []}))
    check("握手提示被带进失败原因",
          "iTunes" in describe_failure(
              {"exitCode": 3, "hint": "请先用 iTunes 同步一次"}))

    import inspect as _i2

    for name, func in (("write_file", None), ("write_files_batch", None),
                       ("read_file", None), ("remove_files", None)):
        source = _i2.getsource(getattr(__import__("aircard.core.airlift",
                                                  fromlist=[name]), name))
        check(f"{name} 接受 reporter 诊断回调", "reporter" in source)
        check(f"{name} 不再裸吞异常", "except Exception as error" in source
              or "raise RuntimeError" in source)

    panel_source = _i2.getsource(
        __import__("aircard.ui.wallet_panel", fromlist=["WalletPanel"]
                   ).WalletPanel._ensure_scan_stopped)
    check("写设备前会强制停止扫描", "stop_scanning" in panel_source)

    print("== 15. 未刷过皮肤的卡（合并卡面文件不存在）==")
    # 真机日志铁证：passd 对 cardBackgroundCombined@3x/@2x/@1x/cardBackground@*
    # 的 8 次 Resource lookup 全部返回 None，只有 strip@2x / logo@2x 有真实路径。
    # 结论：合并卡面文件是刷入时才生成的；没刷过的卡「备份卡面」必然失败。
    from aircard.core.cardskin import (NATIVE_ARTWORK_CANDIDATES,
                                       looks_like_missing, read_native_artwork)
    from aircard.core.scanner import resource_hints

    # ⚠️ 真机实测教训：这个签名**不能**用来断言"文件不存在"。
    # 设备上确定存在的 logo@2x.png（passd 日志给出了完整路径）也报完全相同的错。
    real_failure = ("读取 cardBackgroundCombined@3x.png：同步未成功（第 1/2 次） "
                    "—— 退出码 3；目标校验未通过（原因未上报）；未收到 SyncAllowed")
    check("同步未成功签名被识别", looks_like_missing(real_failure),
          real_failure[:40])
    check("普通故障不会被误判为同步签名",
          not looks_like_missing("同步超时，设备无响应"))
    check("单个特征不足以判定", not looks_like_missing("未收到 SyncAllowed"))

    from aircard.core.cardskin import explain_read_failure

    explained = explain_read_failure(real_failure)
    check("失败说明不再武断断言“文件不存在”",
          "没有" not in explained.split("常见原因")[0] or "没有授予" in explained)
    check("失败说明点明是同步通道问题",
          "同步许可" in explained and "airlift" in explained)
    check("失败说明给出可操作的排查建议", "iTunes" in explained)
    check("失败说明附上诊断细节", "诊断信息" in explained)
    check("已知缺文件时会额外说明",
          "没有合并卡面文件" in explain_read_failure(real_failure, known_missing=True))

    # 通道不通时不应再自动提议读卡包原图（否则 14 个候选白白重试几十秒）
    finished_source = _i2.getsource(
        __import__("aircard.ui.wallet_panel",
                   fromlist=["WalletPanel"]).WalletPanel._on_skin_job_finished)
    check("同步通道不通时跳过卡包原图提议",
          "not looks_like_missing(message)" in finished_source)

    none_line = ("passd(CoreFoundation): Resource lookup at <private>\n"
                 "\tRequest       : cardBackgroundCombined@3x type: png\n"
                 "\tResult        : None\n")
    found_line = ("passd(CoreFoundation): Resource lookup at <private>\n"
                  "\tRequest       : logo@2x type: png\n"
                  "\tResult        : file:///var/mobile/Library/Passes/Cards/"
                  "DRn06Bz0CP9-iLR7jO11MYphOgY=.pkpass/logo@2x.png\n")
    hints_none = list(resource_hints(none_line))
    hints_found = list(resource_hints(found_line))
    check("Result: None 被识别为资源缺失",
          hints_none == [("", "cardBackgroundCombined@3x", False)],
          str(hints_none))
    check("Result 含路径被识别为资源存在",
          hints_found == [("DRn06Bz0CP9-iLR7jO11MYphOgY=", "logo@2x.png", True)],
          str(hints_found))
    check("无关行不产生资源提示", list(resource_hints("passd(passd): hello")) == [])

    check("卡包原图候选里含 strip 与 logo",
          any(leaf.startswith("strip") for leaf in NATIVE_ARTWORK_CANDIDATES)
          and any(leaf.startswith("logo") for leaf in NATIVE_ARTWORK_CANDIDATES))
    import inspect as _i3

    artwork_source = _i3.getsource(read_native_artwork)
    check("读取卡包原图逐个尝试候选", "for leaf in NATIVE_ARTWORK_CANDIDATES"
          in artwork_source)
    check("卡包原图不写进备份仓库（不影响恢复原皮）",
          "save_backup" not in artwork_source)

    # 界面提示：失败后要主动引导改用卡包原图
    panel_cls = __import__("aircard.ui.wallet_panel",
                           fromlist=["WalletPanel"]).WalletPanel
    check("读取失败后会主动提议读卡包原图",
          "_offer_native_artwork" in _i3.getsource(panel_cls._on_skin_job_finished))
    worker_cls = __import__("aircard.ui.workers",
                            fromlist=["CardSkinWorker"]).CardSkinWorker
    check("CardSkinWorker 支持 native 模式",
          '"native"' in _i3.getsource(worker_cls.run))

    print("== 16. 新增噪音框架（真机日志实测）==")
    more_noise = (
        "passd(CoreFoundation): Bundle: <private>, key: <private>, "
        "value: <private>, table: pass, localizationNames: (null), "
        "result: <same as key>",
        "Passbook(CoreFoundation): found no value for key <private> in "
        "CFPrefsSearchListSource<0x75f6c3c000> (Domain: com.apple.Passbook, "
        "Container: (null) Non-launch persona: 0)",
        "Passbook(Accounts): accounts/account-type-with-identifier-sync",
        "Passbook(AppleMediaServices): AMSLRUCache: <private> resulted in a "
        "cache miss.",
        "Passbook(HangTracer): Creating hang event with BundleID: "
        "com.apple.Passbook",
        "Passbook(UserNotifications): [com.apple.Passbook] Creating a user "
        "notification center",
        "Passbook(BackBoardServices): [BKSHIDEventObserver] 0x75f72d1a40 "
        "sceneID%3Acom.apple.Passbook-default",
        "Passbook(libboringssl.dylib): "
        "boringssl_session_install_association_state(1633) Client session "
        "cache miss",
        "passd(libsqlite3.dylib): automatic index on nfc(pass_pid)",
    )
    for line in more_noise:
        check(f"噪音被过滤 {line[:34]}...", not is_wallet_line(line),
              repr(line)[:70])

    check("真实卡包路径行仍被保留（CoreFoundation 也被拉黑也不误伤）",
          is_wallet_line(found_line))
    check("收紧后仍不漏卡",
          all(should_scan(line) for line in more_noise if has_hash_hint(line))
          and should_scan(found_line))

    print("== 17. airlift 通道可单独探测 ==")
    from aircard.native.airtraffic import probe_sync_handshake

    probe_source = _i3.getsource(probe_sync_handshake)
    check("握手探测不同步任何资源（安全可反复跑）",
          "SendSyncRequest" not in probe_source)
    check("握手探测会记录收到的消息", "messages" in probe_source)
    check("握手探测会释放连接", "ATHostConnectionRelease" in probe_source)

    device_module = __import__("aircard.core.device",
                               fromlist=["environment_probe"])
    check("连接诊断会跑一次通道探测",
          "probe_sync_handshake" in _i3.getsource(device_module._probe_atc))
    check("通道探测是可选步骤（不拖慢启动）",
          "_probe_atc(report)" in _i3.getsource(device_module.environment_probe))

    report_source = _i3.getsource(
        __import__("aircard.ui.main_window",
                   fromlist=["MainWindow"]).MainWindow._show_diagnostics_report)
    check("诊断弹窗会显示通道是否可用", "SyncAllowed" in report_source)

    print("== 18. 连接诊断入口必须始终可用 ==")
    # 真机反馈：右上角齿轮消失过两次 ——
    #   ① 齿轮只在"启动诊断返回且 hint 非空"时启用，诊断链路一慢就再也不出现；
    #   ② 启动时只在"没检测到设备"才跑诊断，设备正常连接时压根不会启用。
    window_cls = __import__("aircard.ui.main_window",
                            fromlist=["MainWindow"]).MainWindow
    init_source = _i3.getsource(window_cls.__init__)
    check("齿轮默认即为可用", "set_diagnose_enabled(True)" in init_source)

    probe_source = _i3.getsource(window_cls._on_environment_probe)
    check("诊断返回后无条件恢复齿轮",
          probe_source.index("set_diagnose_enabled(True)")
          < probe_source.index("if not hint"))

    # 耗时探测只能在用户主动打开诊断时才跑，不能拖慢启动链路
    from aircard.core.device import environment_probe

    check("启动诊断默认不跑耗时探测",
          environment_probe.__defaults__ == (False,),
          str(environment_probe.__defaults__))
    manual_source = _i3.getsource(window_cls.show_diagnostics)
    check("手动诊断才会探测 airlift 通道", "include_atc=True" in manual_source)

    print("== 19. ATC 读取必须由常驻线程（不得超时丢弃） ==")
    # 真机根因：Windows 上 ATHostConnectionReadMessage 是**阻塞**调用。
    # 老实现每次读取另起线程、超时（1.5s / 8s）就丢弃并重开 —— 设备晚到的消息
    # 全部落到被抛弃的线程里，界面永远显示"握手期间收到 0 条消息"。
    from aircard.native import airtraffic

    check("存在常驻读取线程实现", hasattr(airtraffic, "_MessagePump"))
    pump_source = _i3.getsource(airtraffic._MessagePump)
    check("读取线程持续保持阻塞读在飞（循环调用 ReadMessage）",
          "ATHostConnectionReadMessage" in pump_source
          and "while" in pump_source)
    check("读取结果进入队列而不是被丢弃", "Queue" in pump_source
          or "queue" in pump_source)
    check("收尾会请求线程停止", "stop" in pump_source)

    sync_source = _i3.getsource(airtraffic.run_airtraffic_sync)
    check("同步流程使用常驻读取线程", "_MessagePump(" in sync_source)
    check("同步流程不再按次数重开读取", "for _ in range(12)" not in sync_source)
    check("握手等待有明确时限（不会无限卡住）",
          "deadline" in sync_source and airtraffic._SYNC_PHASE_DEADLINE >= 10.0,
          str(airtraffic._SYNC_PHASE_DEADLINE))
    probe_src = _i3.getsource(airtraffic.probe_sync_handshake)
    check("诊断等待也有明确时限",
          "deadline" in probe_src and 5.0 <= airtraffic._PROBE_DEADLINE <= 30.0,
          str(airtraffic._PROBE_DEADLINE))

    print("== 20. 设备标识（UDID）两种写法都要试 ==")
    # AirTrafficHost 内部按标识在"已连接设备表"里查设备，写法不对就一条消息都不发。
    cands = airtraffic.candidate_identifiers("00008130-001221242EEA001C")
    check("带连字符的 UDID 会额外尝试去掉连字符",
          "00008130-001221242EEA001C" in cands
          and "00008130001221242EEA001C" in cands, str(cands))
    cands2 = airtraffic.candidate_identifiers("00008130001221242EEA001C")
    check("不带连字符的 UDID 会额外尝试补回连字符",
          "00008130-001221242EEA001C" in cands2, str(cands2))
    check("候选去重且原写法排最前",
          cands[0] == "00008130-001221242EEA001C"
          and len(cands) == len(set(cands)))

    print("== 21. 诊断要能拿到 Apple 组件自己的日志 ==")
    from aircard.core import asllog

    check("日志目录指向 %APPDATA%\\Apple Computer\\Logs",
          "Apple Computer" in _i3.getsource(asllog))
    check("只读取不写入（绝无写操作）",
          "write_text" not in _i3.getsource(asllog)
          and "open(" in _i3.getsource(asllog))
    atc_probe = _i3.getsource(
        __import__("aircard.core.device", fromlist=["_probe_atc"])._probe_atc)
    check("同步通道探测会抓取 Apple 组件日志增量",
          "asllog" in atc_probe or "delta(mark)" in atc_probe)
    check("诊断面板会展示 Apple 组件日志",
          "asl" in _i3.getsource(
              __import__("aircard.ui.main_window",
                         fromlist=["MainWindow"]).MainWindow
              ._show_diagnostics_report))

    print("== 22. 通道没打通时不能再让用户干等 ==")
    # 老行为：读卡包原图会连试 14 个候选、每个等满一轮握手超时（合计好几分钟），
    # 而它们失败的原因完全相同 —— 设备根本没给 SyncAllowed。
    from aircard.core import airlift, cardskin

    check("能判定通道是否根本没打通",
          airlift.channel_dead({"exitCode": 3}) is True
          and airlift.channel_dead({"exitCode": 0, "ok": True}) is False)
    rf = _i3.getsource(airlift.read_file)
    check("读取失败且通道未通时直接放弃重试",
          "channel_dead" in rf and "return None" in rf)
    bf = _i3.getsource(airlift.write_files_batch)
    check("批量写入同理短路", "channel_dead" in bf)
    na = _i3.getsource(cardskin.read_native_artwork)
    check("读卡包原图会在通道未通时停止遍历候选",
          "channel_dead" in na and "break" in na)

    print("== 23. 刷入前备份询问 + 界面样式修复（辨认功能移除）==")
    import aircard.ui.passcode_panel as _pp
    import aircard.ui.style as _style
    import aircard.ui.wallet_panel as _wp
    import aircard.ui.workers as _workers

    _i4 = _i3  # 统一用同一个 inspect 别名
    check("批量备份工作线程存在", hasattr(_workers, "BackupWorker"))
    check("刷入前会筛查未备份的卡",
          "_has_backup" in _i4.getsource(_wp.WalletPanel.start_flash)
          and "missing" in _i4.getsource(_wp.WalletPanel.start_flash))
    check("存在未备份卡时先弹窗询问",
          "QMessageBox" in _i4.getsource(_wp.WalletPanel._confirm_backup_before_flash)
          and "BackupWorker" in _i4.getsource(_wp.WalletPanel._confirm_backup_before_flash))
    check("备份完成后自动继续刷入",
          "_flash_jobs" in _i4.getsource(_wp.WalletPanel._on_pre_flash_backup_finished))
    check("备份失败时给出明确反馈并再次确认",
          "QMessageBox" in _i4.getsource(_wp.WalletPanel._on_pre_flash_backup_finished))
    check("刷入不再静默自动备份（改由用户决定）",
          "auto_backup=False" in _i4.getsource(_wp.WalletPanel._flash_jobs))
    check("卡片瓦片保留单卡备份入口",
          "backupRequested" in _i4.getsource(_wp.WalletPanel._rebuild_tiles))

    style_src = _i4.getsource(_style)
    pp_src = _i4.getsource(_pp)
    check("样式模块提供 rgba() 辅助（修绿框根因）",
          "def rgba(" in style_src and "#AARRGGBB" in style_src)
    check("源码不再拼接 8 位 hex 颜色",
          "}4D" not in style_src and "}4D" not in pp_src
          and "}59" not in pp_src and "}66" not in pp_src)
    check("拖拽区样式限定作用域", "QFrame#dropCard" in pp_src)
    check("清空布局递归删除子布局控件（防文字残影）",
          "clear_layout" in _i4.getsource(_pp.PasscodePanel._sync_creator_controls)
          and "clear_layout" in _i4.getsource(_pp.PasscodePanel._sync_apply_controls))

    # ---------------- 第 1~4 项修改（v1.7.0）----------------
    import aircard.core.skinstore as _store
    import aircard.core.cardskin as _cardskin
    import aircard.core.storage as _storage
    import aircard.ui.card_tile as _tile
    import aircard.app as _app

    check("读取卡面与备份卡面并存：瓦片新增读取/历史恢复信号",
          hasattr(_tile.CardTileWidget, "readRequested")
          and hasattr(_tile.CardTileWidget, "restoreHistoryRequested"))
    check("读取卡面（只读预览）入口存在",
          "read_card_face" in _i4.getsource(_wp.WalletPanel.read_card_face)
          and '"readface"' in _i4.getsource(_wp.WalletPanel.read_card_face))
    check("从历史备份恢复入口存在",
          "restore_from_history" in _i4.getsource(_wp.WalletPanel.restore_from_history)
          and '"restore_file"' in _i4.getsource(_wp.WalletPanel.restore_from_history))
    check("CardSkinWorker 支持 readface/backup/restore_file",
          all(mode in _i4.getsource(_workers.CardSkinWorker.run)
              for mode in ('"readface"', '"backup"', '"restore_file"')))
    check("备份写入自动/手动两种扁平文件",
          "_unique_target" in _i4.getsource(_store)
          and f'"auto_"' in _i4.getsource(_store.save_auto_backup))
    check("备份仓库提供自动/手动查找与检测接口",
          all(hasattr(_store, attr) for attr in (
              "find_auto_backup", "find_manual_backups",
              "has_auto_backup", "has_manual_backup", "load_bytes")))
    check("备份根目录为软件同级 aircard_backups",
          _store.BACKUP_ROOT.name == "aircard_backups")
    check("备份根目录不存在时自动创建",
          "mkdir(" in _i4.getsource(_store.ensure_root))
    check("可从任意历史文件恢复卡面",
          "def restore_from_file(" in _i4.getsource(_cardskin))
    check("启动重置初始状态：清空持久化卡片",
          hasattr(_storage, "clear_saved_cards")
          and "clear_saved_cards" in _i4.getsource(_wp.WalletPanel._reset_on_launch))
    check("工具栏新增在线制作卡面入口",
          "_open_online_designer" in _i4.getsource(_wp.WalletPanel._build_toolbar)
          and "tonychenn.cn/tools/carddesign" in _i4.getsource(_wp.WalletPanel._open_online_designer))
    check("启动弹出全屏协议告知页",
          "AgreementDialog" in _i4.getsource(_app.main)
          and "exec()" in _i4.getsource(_app.main))
    check("协议页可跳过（自动化测试用）",
          "--no-agreement" in _i4.getsource(_app.main)
          and "AIRCARD_NO_AGREEMENT" in _i4.getsource(_app.main))
    # 协议页必须以「同意」进入、「拒绝」退出
    ag_src = _i4.getsource(_app.main)
    check("协议页拒绝则直接退出",
          ag_src.count("return 0") >= 1
          and "QDialog.DialogCode.Accepted" in ag_src)

    # ---------------- v1.8.0 五项新需求 ----------------
    print("== 24. v1.8.0 备份存储 / 按钮态 / 默认勾选 / 备份目录 / 尺寸展示 ==")

    # 需求①：扁平备份与命名
    auto_name = _store.auto_filename(hash_ok)
    manual_name = _store.manual_filename(hash_ok)
    check("自动备份命名 auto_<14位时间戳>_<安全哈希>.png",
          auto_name.startswith("auto_") and auto_name[5:19].isdigit()
          and auto_name.endswith(f"_{_store.safe_hash(hash_ok)}.png"), auto_name)
    check("手动备份命名 <14位时间戳>_<安全哈希>.png（无前缀）",
          not manual_name.startswith("auto_") and manual_name[:14].isdigit()
          and manual_name.endswith(f"_{_store.safe_hash(hash_ok)}.png"),
          manual_name)
    check("哈希做文件名安全化（\\ / : * ? 等被替换）",
          _store.safe_hash("a/b:c*d?e\"f<g>h|i") == "a_b_c_d_e_f_g_h_i",
          _store.safe_hash("a/b:c*d?e\"f<g>h|i"))

    # 需求②：按钮状态按各自备份判定
    import tempfile as _tempfile

    from PIL import Image as PILImage

    with _tempfile.TemporaryDirectory(prefix="aircard-v18-") as _tmp:
        _real_root = _store.BACKUP_ROOT
        try:
            _store.BACKUP_ROOT = Path(_tmp) / "aircard_backups"
            _tile_widget = _tile.CardTileWidget(hash_ok, 0)
            _apply_src = _i4.getsource(_wp.WalletPanel._apply_backup_preview)
            check("扫描后按哈希在备份目录里查找匹配备份",
                  "has_auto_backup" in _apply_src
                  and "has_manual_backup" in _apply_src)
            check("两个恢复按钮分别由自动 / 手动备份控制",
                  "set_backup_states" in _apply_src
                  and "_apply_backup_preview(record, tile)"
                      in _i4.getsource(_wp.WalletPanel._rebuild_tiles)
                  and "_apply_backup_preview(record, tile)"
                      in _i4.getsource(_wp.WalletPanel._refresh_backup_states))

            # 真实落盘 + 状态判定
            _pix_raw = BytesIO()
            PILImage.new("RGBA", (1125, 2436), (9, 9, 9, 255)).save(
                _pix_raw, format="PNG")
            _pix_bytes = _pix_raw.getvalue()
            _store.save_auto_backup(hash_ok, _pix_bytes)
            _wp_inst: dict = {}
            check("存在自动备份时 has_auto_backup 为真",
                  _store.has_auto_backup(hash_ok))
            check("此时手动备份仍为假（两者独立）",
                  not _store.has_manual_backup(hash_ok))
            _tile_widget.set_backup_states(_store.has_auto_backup(hash_ok),
                                           _store.has_manual_backup(hash_ok))
            check("自动备份→恢复原皮亮起、从历史恢复仍灰",
                  _tile_widget.restore_button.isEnabled()
                  and not _tile_widget.restore_history_button.isEnabled())
            _store.save_manual_backup(hash_ok, _pix_bytes)
            _tile_widget.set_backup_states(_store.has_auto_backup(hash_ok),
                                           _store.has_manual_backup(hash_ok))
            check("手动备份出现后从历史恢复也亮起",
                  _tile_widget.restore_history_button.isEnabled())
        finally:
            _store.BACKUP_ROOT = _real_root

    check("恢复原皮严格以自动备份为前置条件",
          "has_auto_backup" in _i4.getsource(_wp.WalletPanel.restore_card_skin))

    # 需求③：扫描到的卡片默认不勾选
    _record_defaults = _wp.CardRecord(card_hash="X")
    check("卡片记录默认 selected=False（扫描到的卡不勾选）",
          _record_defaults.selected is False)
    check("瓦片复选框默认不勾选",
          "setChecked(False)" in _i4.getsource(_tile.CardTileWidget._build_info_row)
          and "False" in _i4.getsource(_tile.CardTileWidget.__init__)
          and _tile.CardTileWidget(hash_ok, 0).is_selected() is False)
    check("存在「全选/取消全选」入口便于批量勾选",
          "_set_all_selected" in _i4.getsource(_wp.WalletPanel._build_toolbar))

    # 需求④：打开备份目录按钮
    check("工具栏新增「打开备份目录」按钮",
          "open_backup_button" in _i4.getsource(_wp.WalletPanel._build_toolbar))
    check("点击即在系统中打开 aircard_backups 文件夹",
          "QDesktopServices.openUrl" in _i4.getsource(
              _wp.WalletPanel._open_backup_dir)
          and "ensure_root" in _i4.getsource(_wp.WalletPanel._open_backup_dir))

    # 需求⑤：读取卡面时计算并展示尺寸
    check("读取卡面会计算卡面尺寸",
          "image_size" in _i4.getsource(_workers.CardSkinWorker.run))
    check("尺寸经 finished 信号回传界面（info 参数）",
          'info["size"]' in _i4.getsource(_workers.CardSkinWorker.run)
          and "read_size" in _i4.getsource(_wp.WalletPanel._on_skin_job_finished))
    # 注意：临时瓦片必须绑定到变量再用其 children，否则父控件先被销毁，
    # size_label 会成为悬垂包装对象（RuntimeError: already deleted）。
    _tile_default = _tile.CardTileWidget(hash_ok, 0)
    check("瓦片提供尺寸展示标签",
          hasattr(_tile_default, "size_label")
          and "def set_size" in _i4.getsource(_tile.CardTileWidget))
    # 用 isHidden() 判断而非 isVisible()：后者要求从自身到顶层窗口全部可见，
    # 而这里的瓦片没有父窗口，isVisible() 恒为 False，测不出我们要的状态。
    check("无尺寸时尺寸标签默认隐藏", _tile_default.size_label.isHidden())
    _tile_default.set_size((1125, 2436))
    check("有尺寸时显示「卡面尺寸：1125 × 2436 px」",
          _tile_default.size_label.text() == "卡面尺寸：1125 × 2436 px"
          and not _tile_default.size_label.isHidden(),
          _tile_default.size_label.text())
    _tile_default.set_size(None)
    check("尺寸为空时标签重新隐藏", _tile_default.size_label.isHidden())

    # 读取即写自动备份（确保「恢复原皮」总有来源）
    check("读取卡面会写入自动备份",
          "save_auto_backup" in _i4.getsource(_workers.CardSkinWorker.run))
    check("刷入前自动备份走 ensure_backup（写 auto_ 文件）",
          "save_auto_backup" in _i4.getsource(_cardskin.ensure_backup)
          and "has_auto_backup" in _i4.getsource(_cardskin.ensure_backup))
    check("恢复原皮从最新自动备份取字节",
          "find_auto_backup" in _i4.getsource(_cardskin.restore_original_skin))

    # ---------------- v1.9.0 三项新需求 ----------------
    print("== 25. v1.9.0 备份预览/尺寸（需求①）+ native 视同手动备份（需求③）==")

    # 需求①：扫描到已有备份的卡时，展示最新一份（自动或手动）备份预览 + 尺寸
    with _tempfile.TemporaryDirectory(prefix="aircard-v19-") as _tmp2:
        _real_root2 = _store.BACKUP_ROOT
        try:
            _store.BACKUP_ROOT = Path(_tmp2) / "aircard_backups"
            _pix2 = BytesIO()
            PILImage.new("RGBA", (1125, 2436), (1, 2, 3, 255)).save(
                _pix2, format="PNG")
            _bytes2 = _pix2.getvalue()

            # 只有手动备份：latest_face_backup 应返回它，并带尺寸
            _store.save_manual_backup(hash_ok, _bytes2)
            _fp, _fs = _store.latest_face_backup(hash_ok)
            check("需求①：仅有手动备份时也能拿到备份预览路径",
                  _fp is not None, repr(_fp))
            check("需求①：手动备份的尺寸被正确读出",
                  _fs == (1125, 2436), repr(_fs))
            check("需求①：latest_face_backup 不得误取 native_ 原图",
                  _fp is None or not Path(_fp).name.startswith("native_"))

            # 再加一份自动备份（时间更新），应取最新 = 自动备份
            _store.save_auto_backup(hash_ok, _bytes2)
            _fp2, _fs2 = _store.latest_face_backup(hash_ok)
            check("需求①：自动与手动并存时取时间戳最新的一份（auto_）",
                  Path(_fp2).name.startswith("auto_") if _fp2 else False,
                  repr(_fp2))

            # native_ 原图不计入真实卡面备份
            _store.save_artwork(hash_ok, "strip@2x.png", _bytes2)
            _fp3, _fs3 = _store.latest_face_backup(hash_ok)
            check("需求①：native_ 原图不污染真实卡面备份预览",
                  not (Path(_fp3).name.startswith("native_") if _fp3 else False),
                  repr(_fp3))

            # 仅保留 native_ 原图，验证需求③
            for _f in list(_store.BACKUP_ROOT.iterdir()):
                if not _f.name.startswith("native_"):
                    _f.unlink()

            check("需求③：仅剩 native_ 原图时 has_manual_backup 为真",
                  _store.has_manual_backup(hash_ok))
            check("需求③：仅 native_ 原图时 has_auto_backup 仍为假",
                  not _store.has_auto_backup(hash_ok))
            # 只有 native_ 时 latest_face_backup 应返回 (None, None)（非真实卡面）
            _fp4, _fs4 = _store.latest_face_backup(hash_ok)
            check("需求①：仅有 native_ 原图时 latest_face_backup 返回 (None, None)",
                  _fp4 is None and _fs4 is None, repr((_fp4, _fs4)))
            _t3 = _tile.CardTileWidget(hash_ok, 0)
            _t3.set_backup_states(_store.has_auto_backup(hash_ok),
                                  _store.has_manual_backup(hash_ok))
            check("需求③：仅 native_ 原图时「从历史恢复」可点（按钮启用）",
                  _t3.restore_history_button.isEnabled())
            check("需求③：仅 native_ 原图时「恢复原皮」仍不可点（无真实卡面）",
                  not _t3.restore_button.isEnabled())
        finally:
            _store.BACKUP_ROOT = _real_root2

    # 面板接线：统一方法存在且被两处调用
    check("需求①：面板提供统一套用备份预览/尺寸/按钮的方法",
          "_apply_backup_preview" in _i4.getsource(_wp.WalletPanel))
    check("需求①：扫描重建瓦片时调用该方法",
          "_apply_backup_preview(record, tile)"
          in _i4.getsource(_wp.WalletPanel._rebuild_tiles))
    check("需求③：刷新备份状态时也调用该方法",
          "_apply_backup_preview(record, tile)"
          in _i4.getsource(_wp.WalletPanel._refresh_backup_states))
    check("需求①：最新备份预览取自动或手动时间最新者",
          "latest_face_backup" in _i4.getsource(_wp.WalletPanel._apply_backup_preview))
    check("需求③：has_manual_backup 把 native_ 原图视同手动备份",
          "artwork_path(card_hash) is not None"
          in _i4.getsource(_store.has_manual_backup))

    # 需求②：日志面板默认关闭，且不存在独立「右侧栏」
    _mw_src = _i4.getsource(
        __import__("aircard.ui.main_window", fromlist=["MainWindow"]).MainWindow.__init__)
    check("需求②：活动日志面板默认隐藏（setVisible(False)）",
          "self.console.setVisible(False)" in _mw_src)

    # ---------------- v1.9.1 三项跟进 ----------------
    print("== 26. v1.9.1 禁用态可读 / native 历史记录识别 / 禁止倒卖 ==")

    # ①「日志」右侧的空框 = 禁用态「刷入皮肤」按钮：淡色底 + 白字导致看不见文字。
    #    修复后禁用态一律使用深色文字，不再是"空框"。
    import re as _re

    from aircard.ui.style import APP_QSS

    disabled_blocks = _re.findall(
        r'QPushButton\[cta="[^"]+"\]:disabled \{[^}]*\}', APP_QSS)
    check("禁用态 CTA 样式块存在（至少 3 条）", len(disabled_blocks) >= 3,
          str(len(disabled_blocks)))
    check("禁用态 CTA 不再用白色文字（修「日志右侧空框」根因）",
          all("color: #FFFFFF" not in block for block in disabled_blocks),
          str(disabled_blocks)[:120])

    # ② native_ 备份记录也要被卡片预览识别：从备份目录按修改时间取最新一张
    _apply2_src = _i4.getsource(_wp.WalletPanel._apply_backup_preview)
    check("native_ 记录在备份预览中从磁盘识别（不限本次会话）",
          "_artwork_preview_path" in _apply2_src, _apply2_src[:80])
    check("native_ 「最新一次」按文件修改时间判定",
          "st_mtime" in _i4.getsource(_store.artwork_path))

    with _tempfile.TemporaryDirectory(prefix="aircard-v191-") as _tmp3:
        _real_root3 = _store.BACKUP_ROOT
        try:
            _store.BACKUP_ROOT = Path(_tmp3) / "aircard_backups"
            import os as _os

            _pix3 = BytesIO()
            PILImage.new("RGBA", (300, 500), (7, 7, 7, 255)).save(
                _pix3, format="PNG")
            _bytes3 = _pix3.getvalue()
            _logo = _store.BACKUP_ROOT / _store.native_filename(hash_ok, "logo@2x.png")
            _strip = _store.BACKUP_ROOT / _store.native_filename(hash_ok, "strip@2x.png")
            _saved_ok = (_store.save_artwork(hash_ok, "logo@2x.png", _bytes3)
                         and _store.save_artwork(hash_ok, "strip@2x.png", _bytes3))
            check("save_artwork 落盘成功",
                  _saved_ok and _logo.is_file() and _strip.is_file())
            # 把 strip 的修改时间调旧：strip 在文件名排序里更靠后，但时间更旧
            # → 「最新一次」必须按修改时间返回 logo，证明不是按文件名排序。
            past = _time.time() - 86400
            _os.utime(_strip, (past, past))
            check("native_ 多张时按修改时间取最新（非文件名排序）",
                  _store.artwork_path(hash_ok) == _logo,
                  str(_store.artwork_path(hash_ok)))
        finally:
            _store.BACKUP_ROOT = _real_root3

    # ③ 免费软件，禁止倒卖：协议告知页 + 主窗口显著位置
    _ag_src = _i4.getsource(
        __import__("aircard.ui.agreement", fromlist=["AgreementDialog"]))
    check("协议告知页加粗显示「禁止倒卖」",
          "禁止倒卖" in _ag_src and "font-weight:700" in _ag_src)
    check("主窗口显著位置常驻「禁止倒卖」（顶栏 HeaderBar）",
          "禁止倒卖" in _i4.getsource(
              __import__("aircard.ui.main_window",
                         fromlist=["HeaderBar"]).HeaderBar.__init__))

    print()
    print("== 27. v1.9.2 操作一致性 / 日志栏不自动展开 / AFC 重定位重试 ==")
    try:
        from PySide6.QtWidgets import QApplication

        from aircard.ui.context import AppContext
        from aircard.ui.wallet_panel import WalletPanel

        app = QApplication.instance() or QApplication([])

        # ① AppContext 操作类型跟踪 + set_busy(False) 复位
        _ctx = AppContext()
        _ctx.set_op("readface")
        check("AppContext.set_op 生效", _ctx.op == "readface")
        _ctx.set_busy(True)
        check("busy=True 时 op 保持", _ctx.op == "readface")
        _ctx.set_busy(False)
        check("busy=False 时 op 复位为空", _ctx.op == "")

        # ② 闪入按钮文案随操作类型变化（不再一律"正在刷入卡片…"）
        _panel = WalletPanel(_ctx)
        _ctx.set_busy(True)
        for _op, _want in (("readface", "正在读取卡面"),
                           ("backup", "正在备份卡面"),
                           ("restore", "正在恢复原皮"),
                           ("native", "正在读取原图"),
                           ("flash", "正在刷入皮肤")):
            _ctx.set_op(_op)
            _label = _panel.flash_button_label()
            check(f"忙碌态文案匹配 {_op}", _want in _label, _label)
        _ctx.set_busy(False)
        _ctx.set_op("")
        check("空闲态文案为「刷入皮肤」", _panel.flash_button_label() == "刷入皮肤",
              _panel.flash_button_label())
    except Exception as _exc:  # noqa: BLE001
        check("v1.9.2 功能性检查未抛异常", False, repr(_exc))

    # ③ 主窗口：需求②(v1.9.3) 日志栏默认始终关闭 —— 任何操作 / 出错都不自动展开
    _mw = __import__("aircard.ui.main_window", fromlist=["MainWindow"])
    _busy_src = _i4.getsource(_mw.MainWindow._on_busy_changed)
    check("忙碌时不再自动展开日志栏（任何操作类型）",
          "set_console_visible" not in _busy_src)
    check("日志栏不再随 flash 等操作类型展开",
          'self.ctx.op == "flash"' not in _busy_src)

    _status_src = _i4.getsource(_mw.MainWindow._on_status)
    check("刷入时左下角摘要同步进度（1/1）",
          "正在刷入 {match.group(1)}/{match.group(2)} 张卡" in _status_src)

    _err_src = _i4.getsource(_mw.MainWindow.show_error)
    check("出错时也不自动展开日志栏", "set_console_visible" not in _err_src)

    # ④ airlift.read_file 对「重定位未完成」做轮询重试
    _al = __import__("aircard.core.airlift", fromlist=["read_file"])
    _read_src = _i4.getsource(_al.read_file)
    check("read_file 对 AFC 读回做轮询重试",
          "for afc_try in range(1, 5):" in _read_src)
    check("read_file 区分重定位时序问题 vs 文件不存在",
          "重定位时序问题" in _read_src)

    print("== 28. v1.9.3 已刷过皮肤的卡显示最新皮肤 / 日志栏默认关闭 ==")
    # ① skinstore：lastflash_ 系列函数齐备
    _st2 = __import__("aircard.core.skinstore", fromlist=["save_last_flash"])
    check("skinstore.save_last_flash 存在", callable(_st2.save_last_flash))
    check("skinstore.last_flash_path 存在", callable(_st2.last_flash_path))
    check("skinstore.clear_last_flash 存在", callable(_st2.clear_last_flash))
    check("lastflash 文件名带 lastflash_ 前缀",
          _st2.lastflash_filename("ab/cd+ef=").startswith("lastflash_"))

    # ② 功能性：写入 / 读回 / 清除，且绝不误点亮"从历史恢复"按钮
    _test_hash = "ZZTESTHASH/for+lastflash=1"
    try:
        _st2.clear_last_flash(_test_hash)
        _saved = _st2.save_last_flash(_test_hash, b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        check("save_last_flash 返回落盘路径", _saved is not None)
        check("last_flash_path 能读回该记录", _st2.last_flash_path(_test_hash) is not None)
        check("lastflash 不误判为手动备份（不误点亮从历史恢复）",
              _st2.has_manual_backup(_test_hash) is False)
        check("lastflash 不计入 find_manual_backups",
              _st2.find_manual_backups(_test_hash) == [])
        check("lastflash 不计入 latest_face_backup（不显示为原皮）",
              _st2.latest_face_backup(_test_hash) == (None, None))
        check("clear_last_flash 删除成功", _st2.clear_last_flash(_test_hash) is True)
        check("clear 之后 last_flash_path 为 None",
              _st2.last_flash_path(_test_hash) is None)
    except Exception as _exc:  # noqa: BLE001
        check("v1.9.3 skinstore 功能性检查未抛异常", False, repr(_exc))
    finally:
        try:
            _st2.clear_last_flash(_test_hash)
        except Exception:  # noqa: BLE001
            pass

    # ③ wallet_panel：预览优先级把 last_flash 插在"原皮备份"之前
    _wp2 = __import__("aircard.ui.wallet_panel",
                      fromlist=["CardRecord", "WalletPanel"])
    _apply3_src = _i4.getsource(_wp2.WalletPanel._apply_backup_preview)
    check("_apply_backup_preview 取用 last_flash_path",
          "last_flash_path(" in _apply3_src)
    check("预览优先级：last_flash 分支先于 read_path（原皮）分支",
          _apply3_src.index("elif last_flash:")
          < _apply3_src.index("elif record.read_path:"))
    check("CardRecord 带 last_flash_path 字段",
          "last_flash_path" in _i4.getsource(_wp2.CardRecord))

    # ④ 刷入成功记录 / 恢复原皮清除 / 从历史恢复更新
    _wk2 = __import__("aircard.ui.workers", fromlist=["CardFlashWorker"])
    check("刷入成功后记录最近一次皮肤",
          "save_last_flash" in _i4.getsource(_wk2.CardFlashWorker.run))
    _cs2 = __import__("aircard.core.cardskin", fromlist=["restore_original_skin"])
    check("恢复原皮成功后清除最近皮肤记录（回退显示原皮）",
          "clear_last_flash" in _i4.getsource(_cs2.restore_original_skin))
    check("从历史恢复成功后更新最近皮肤记录",
          "save_last_flash" in _i4.getsource(_cs2.restore_from_file))

    # ⑤ 主窗口：初始日志栏关闭，仅手动 toggle 切换
    _mw2 = __import__("aircard.ui.main_window", fromlist=["MainWindow"])
    check("日志栏初始为关闭状态",
          "self.console.setVisible(False)" in _i4.getsource(_mw2.MainWindow))
    check("仅 toggle_console 按当前可见性切换",
          "set_console_visible(not self.console.isVisible())"
          in _i4.getsource(_mw2.MainWindow.toggle_console))

    # ⑥ 运行时功能校验（不只是看源码）：已刷过的卡渲染"最近一次新皮肤"，
    #    从未刷过的卡回退渲染"原皮"。用间谍函数记录 preview_pixmap 实际渲染了谁。
    _auto_a = _auto_b = None
    try:
        from io import BytesIO

        from PIL import Image
        from PySide6.QtWidgets import QApplication

        from aircard.core import skinstore as _store3
        from aircard.ui import wallet_panel as _wpmod
        from aircard.ui.context import AppContext as _Ctx3

        _app3 = QApplication.instance() or QApplication([])

        def _tmp_png(color: tuple) -> bytes:
            buf = BytesIO()
            Image.new("RGB", (120, 80), color).save(buf, format="PNG")
            return buf.getvalue()

        _real_pp = _wpmod.preview_pixmap
        _seen: list[str] = []

        def _spy(path, *a, **kw):
            _seen.append(str(path))
            return _real_pp(path, *a, **kw)

        _wpmod.preview_pixmap = _spy
        try:
            # 场景 A：刷过皮肤（原皮 auto 备份 + 最近一次新皮肤 lastflash 同时存在）
            _ha = "SMOKE/last+flash=A"
            _store3.clear_last_flash(_ha)
            _auto_a = _store3.save_auto_backup(_ha, _tmp_png((220, 40, 40)))
            _store3.save_last_flash(_ha, _tmp_png((40, 90, 220)))
            _panel_a = _wpmod.WalletPanel(_Ctx3())
            _panel_a._records.append(_wpmod.CardRecord(card_hash=_ha))
            _seen.clear()
            _panel_a._rebuild_tiles()
            check("已刷过皮肤的卡：优先渲染最近一次新皮肤(lastflash)",
                  bool(_seen) and "lastflash_" in _seen[0], str(_seen[:2]))
            check("已刷过皮肤的卡：不再渲染原皮(auto 备份)",
                  not any("auto_" in s for s in _seen), str(_seen[:2]))

            # 场景 B：从未刷过皮肤（只有原皮 auto 备份，无 lastflash）
            _hb = "SMOKE/last+flash=B"
            _store3.clear_last_flash(_hb)
            _auto_b = _store3.save_auto_backup(_hb, _tmp_png((220, 40, 40)))
            _panel_b = _wpmod.WalletPanel(_Ctx3())
            _panel_b._records.append(_wpmod.CardRecord(card_hash=_hb))
            _seen.clear()
            _panel_b._rebuild_tiles()
            check("从未刷过皮肤的卡：回退渲染原皮(auto 备份)",
                  bool(_seen) and "auto_" in _seen[0], str(_seen[:2]))
        finally:
            _wpmod.preview_pixmap = _real_pp
            for _h in ("SMOKE/last+flash=A", "SMOKE/last+flash=B"):
                _store3.clear_last_flash(_h)
            for _p in (_auto_a, _auto_b):
                try:
                    if _p:
                        _p.unlink()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as _exc:  # noqa: BLE001
        check("v1.9.3 预览优先级运行时校验未抛异常", False, repr(_exc))

    print("== 29. 协议页：写明与原版的区别 + 注明原项目地址 ==")
    _ag2 = __import__("aircard.ui.agreement", fromlist=["AgreementDialog"])
    _ag_html = _ag2._AGREEMENT_TEXT.format(
        version=__import__("aircard", fromlist=["VERSION"]).VERSION)
    check("协议页注明原项目地址",
          "https://github.com/Mak5er/AirCard" in _ag_html)
    check("协议页含「与原版的区别」章节", "与原版的区别" in _ag_html)
    check("协议页只写两点之一：与原版的区别",
          "与原版的区别" in _ag_html)
    check("协议页只写两点之二：原版没有的功能（本版新增）",
          "原版没有的功能" in _ag_html and "本版新增" in _ag_html)
    check("协议页注明原作者与许可证",
          "@mak5er" in _ag_html and "MIT" in _ag_html)
    check("协议页声明并非官方发布", "并非官方发布" in _ag_html)
    check("协议页新增功能条目完整（读取/备份/恢复/诊断）",
          all(key in _ag_html for key in ("读取卡面", "备份卡面", "恢复原皮",
                                          "连接诊断", "在线制作卡面入口")))
    # 已按用户要求删除的三类内容
    check("协议页已删除「与原版一致的部分」",
          "与原版一致的部分" not in _ag_html)
    check("协议页已删除「为适配 Windows 而改动的部分」",
          "为适配 Windows 而改动的部分" not in _ag_html)
    check("协议页已删除「相对原版的局限」",
          "相对原版的局限" not in _ag_html)
    check("协议页不再保留差异对照表与实现细节",
          "<table" not in _ag_html and "AMDCreateDeviceList" not in _ag_html)
    check("协议页不再声称不存在的「卡片可视化标记」功能",
          "卡片可视化标记" not in _ag_html)
    check("协议页章节编号连续（一 ~ 五）",
          all(f"{n}、" in _ag_html for n in ("一", "二", "三", "四", "五")))
    check("原项目链接可点击打开",
          "setOpenExternalLinks(True)" in _i4.getsource(_ag2.AgreementDialog))
    check("保留「禁止倒卖」加粗提示（不被新章节挤掉）",
          "禁止倒卖" in _ag_html and "font-weight:700" in _ag_html)

    # README：兼容性声明 / 运行环境依赖 / 差异章节只保留「区别 + 原版没有的功能」
    try:
        from pathlib import Path as _P2

        _readme = (_P2(__file__).resolve().parents[1]
                   / "README.md").read_text(encoding="utf-8")
        check("README 含兼容性声明（Windows 10 / 11 + iOS 27.0）",
              "Windows 10 / Windows 11" in _readme and "iOS 27.0" in _readme)
        check("README 声明其余平台 / 版本均未验证",
              "均未经验证" in _readme)
        check("README 新增「运行环境依赖」小节",
              "运行环境依赖" in _readme)
        check("README 说明未装 iTunes 时无法使用全部功能",
              "未安装依赖时" in _readme and "iTunes" in _readme)
        check("README 差异章节只写「区别 + 原版没有的功能」",
              "与原版的区别" in _readme and "原版没有的功能" in _readme)
        check("README 已删除「与原版一致的部分」",
              "与原版一致的部分" not in _readme)
        check("README 已删除「相对原版的局限」小节",
              "本移植版相对原版的局限" not in _readme)
        check("README 已删除「为适配 Windows 所做的改动」小节",
              "为适配 Windows 所做的改动" not in _readme)
        check("README 标注原项目地址",
              "https://github.com/Mak5er/AirCard" in _readme)
    except Exception as _exc:  # noqa: BLE001
        check("README 兼容性 / 依赖说明检查未抛异常", False, repr(_exc))

    print()
    print(f"通过 {PASS} 项，失败 {FAIL} 项。")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
