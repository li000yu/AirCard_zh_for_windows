"""启动时的全屏协议与风险告知页。

用户必须点击「同意」才能进入主界面；点击「拒绝」则直接退出软件。
该页只在 `app.main()` 里、主窗口创建之前弹出，因此不会干扰离屏自动化测试。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLabel, QPushButton, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)

from .. import VERSION
from .style import ACCENT, ACCENT_SOFT, BLUE_INFO, CONTROL_BG, RED, Type

_AGREEMENT_TEXT = """<h2 style="margin:0 0 6px;">AirCard for Windows · 使用协议与风险告知</h2>
<p style="margin:0 0 14px;color:#6E6E73;">版本 v{version} · 请在进入前仔细阅读以下条款与风险。</p>
<p style="margin:0 0 14px;color:#FF3B30;font-weight:700;font-size:16px;">⚠ 免费软件，禁止倒卖！本软件完全免费，任何形式的出售、捆绑收费、倒卖均属违规行为。</p>

<h3 style="margin:8px 0 4px;">一、软件用途</h3>
<p>本软件是一款在 <b>你本人拥有并控制的 iPhone</b> 上，对 Apple 钱包（Wallet）卡片卡面、以及锁屏密码键盘主题进行个性化修改的辅助工具。其底层基于 <b>airlift（AirTraffic 同步逃逸）</b> 技术，通过设备与电脑之间的私有同步通道读写卡面与主题文件。</p>

<h3 style="margin:8px 0 4px;">二、与原版的区别（本软件是移植版，不是原版）</h3>
<p>本软件是 macOS 原版 AirCard 的 <b>Windows 移植 + 中文化</b> 版本。<br>
<b>原项目地址：<a href="https://github.com/Mak5er/AirCard">https://github.com/Mak5er/AirCard</a></b><br>
原作者：<b>@mak5er</b>　原项目许可证：<b>MIT</b><br>
本移植版<b>并非官方发布</b>，与原作者之间没有隶属、授权或共同维护关系。其核心漏洞利用
（<code>airlift</code> AirTraffic 同步逃逸）源自原项目及其上游，本版仅是在 Windows 上重新实现。</p>

<p style="margin:8px 0 2px;"><b>与原版的区别</b></p>
<ul style="margin:4px 0 4px;padding-left:20px;">
  <li><b>运行平台：</b>原版面向 macOS（Apple Silicon / Intel 通用包）；本版面向 <b>Windows 10 / 11（64 位）</b>。</li>
  <li><b>界面语言：</b>原版为英文界面；本版为 <b>全中文界面</b>。</li>
  <li><b>分发形式：</b>原版为 <code>AirCard.dmg</code>；本版为 <b>单文件绿色版 <code>AirCard.exe</code></b>。</li>
  <li><b>运行依赖：</b>原版所需组件已内置、开箱即用；本版 <b>需要本机已安装 iTunes 或「Apple 移动设备支持」组件</b>，否则所有需要连接 iPhone 的功能均不可用（纯本地功能仍可使用）。</li>
  <li><b>功能范围：</b>本版在原版之外 <b>额外提供了读取卡面、备份与恢复等功能</b>（见下）。</li>
</ul>

<p style="margin:8px 0 2px;"><b>原版没有的功能（本版新增）</b></p>
<ul style="margin:4px 0 4px;padding-left:20px;">
  <li><b>读取卡面：</b>原版只能写入卡面；本版可把设备上当前的卡面读回界面，用于辨认「当前是哪张卡」。</li>
  <li><b>备份卡面：</b>把指定卡片的当前卡面保存备份到本软件下 <code>aircard_backups/</code> 目录，作为回滚依据。</li>
  <li><b>恢复原皮 / 从历史恢复：</b>把备份过的卡面写回设备，可退回出厂卡面或任一历史卡面。</li>
  <li><b>刷入前自动备份：</b>首次刷入某张卡前，自动先备份其原卡面。</li>
  <li><b>已刷卡显示最新皮肤：</b>刷过皮肤的卡，重新打开软件后预览会直接显示最近一次刷入的新皮肤；从未刷过皮肤的卡显示原皮肤。</li>
  <li><b>连接诊断：</b>一键输出 Apple 组件状态、设备识别与同步通道的诊断报告。</li>
  <li><b>每次启动重置：</b>每次启动自动清空上次扫描到的卡片列表，不保留历史扫描结果。</li>
  <li><b>在线制作卡面入口：</b>跳转到在线卡面设计工具。</li>
</ul>

<h3 style="margin:8px 0 4px;">三、使用风险（请务必了解）</h3>
<ol style="margin:4px 0 4px;padding-left:20px;">
  <li><b>写回失败风险：</b>修改卡面时，软件会把设备上的卡面文件“移动 → 读回 → 写回”。若中途失败，该卡片可能短暂显示为空卡面。软件已提供「备份卡面」与「恢复原皮 / 从历史恢复」以降低风险，但仍<b>不保证万无一失</b>。</li>
  <li><b>设备同步前置条件：</b>功能依赖 Apple 私有同步通道（AirTraffic）。若本机从未用 iTunes / Apple Devices 与该 iPhone 同步过、设备已锁屏、未点「信任此电脑」，或装有 MDM 描述文件，相关功能将不可用。</li>
  <li><b>合规与责任：</b>请勿将本软件用于你无权修改的设备或他人设备；不当修改金融、票务、门禁等卡片可能违反发卡机构或相关平台条款，由此产生的任何后果由使用者自行承担。</li>
  <li><b>数据与安全：</b>软件会在本机（软件同级目录 <code>aircard_backups/</code>）保存你主动备份的卡面，默认不会上传任何数据；但你仍应自行保管好备份文件。</li>
</ol>

<h3 style="margin:8px 0 4px;">四、免责声明</h3>
<p>本软件按“现状”提供，作者不对任何因使用本软件导致的设备损坏、数据丢失、账号风险或第三方索赔承担责任。本软件与 Apple Inc. 无关，并非官方工具，Apple 亦不对其提供支持。原项目许可证为 MIT，本移植版遵循其条款；与 <code>airlift</code> 相关的漏洞利用仅用于在你本人拥有的设备上做个性化修改。</p>

<h3 style="margin:8px 0 4px;">五、确认</h3>
<p>只有当你<b>已完全理解上述用途、与原版的区别以及全部风险</b>，并确认仅在本人拥有的设备上使用时，才可点击「同意」进入软件。点击「拒绝」将立即退出。</p>
"""


class AgreementDialog(QDialog):
    """铺满窗口的协议与风险告知页。"""

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: N803
        super().__init__(parent)
        self.setWindowTitle("使用协议与风险告知")
        self.setModal(True)
        self.setMinimumSize(720, 560)
        # 仅在首次 show 时最大化一次（用标志位防止 showMaximized 触发
        # 的二次 showEvent 造成递归）。
        self._maximized_once = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部品牌条
        bar = QLabel("\U0001F4B3  AirCard · 使用协议与风险告知")
        bar.setStyleSheet(f"background: {ACCENT_SOFT}; color: {ACCENT}; "
                          f"font-weight: 700; padding: 14px 24px; font-size: 15px;")
        layout.addWidget(bar)

        # 可滚动的协议正文（居中限宽，便于阅读）
        # QTextBrowser 是 QTextEdit 的子类（默认只读），额外支持锚点跳转。
        # 正文里放了原项目地址（https://github.com/Mak5er/AirCard），
        # 允许点击链接在默认浏览器中打开，方便用户自行核对来源。
        # 注意：setOpenExternalLinks 只有 QTextBrowser 有，QTextEdit 没有。
        viewer = QTextBrowser(self)
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        viewer.setOpenExternalLinks(True)
        viewer.setStyleSheet(
            f"background: {CONTROL_BG}; border: none; padding: 8px 4px;")
        viewer.setHtml(_AGREEMENT_TEXT.format(version=VERSION))
        layout.addWidget(viewer, 1)

        # 确认勾选
        confirm_row = QHBoxLayout()
        confirm_row.setContentsMargins(24, 12, 24, 4)
        self.confirm_box = QCheckBox("我已完整阅读、理解并自愿接受上述所有条款与风险")
        self.confirm_box.setFont(Type.body())
        self.confirm_box.toggled.connect(self._on_confirm_toggled)
        confirm_row.addWidget(self.confirm_box)
        confirm_row.addStretch(1)
        layout.addLayout(confirm_row)

        # 底部按钮
        actions = QHBoxLayout()
        actions.setContentsMargins(24, 12, 24, 24)
        actions.setSpacing(12)

        reject = QPushButton("拒绝并退出")
        reject.setProperty("cta", "danger")
        reject.setMinimumWidth(140)
        reject.setCursor(Qt.CursorShape.PointingHandCursor)
        reject.clicked.connect(self.reject)
        actions.addWidget(reject)

        actions.addStretch(1)

        accept = QPushButton("同意并进入")
        accept.setProperty("cta", "true")
        accept.setMinimumWidth(160)
        accept.setCursor(Qt.CursorShape.PointingHandCursor)
        accept.setEnabled(False)        # 必须先勾选确认
        accept.clicked.connect(self.accept)
        self.accept_button = accept
        actions.addWidget(accept)
        layout.addLayout(actions)

    def _on_confirm_toggled(self, checked: bool) -> None:
        self.accept_button.setEnabled(bool(checked))

    def showEvent(self, event) -> None:  # noqa: N802
        """铺满可用屏幕：用 showMaximized() 让系统自动避开任务栏。

        之前手动 resize 到 availableGeometry 并 move 到其左上角，但窗口边框
        （标题栏）会探到任务栏里，把底部的「拒绝 / 同意」按钮挡住一半。
        showMaximized 由窗口管理器负责把窗口限制在「不含任务栏」的可用区域内，
        按钮因此始终完整可见，同时保留「铺满全屏」的观感。
        """
        super().showEvent(event)
        if not self._maximized_once:
            self._maximized_once = True
            self.showMaximized()
