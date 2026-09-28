# AirCard for Windows 🎴

> **Apple Wallet 卡面换肤 与 锁屏密码主题工具（Windows 版 · 全中文界面）**
> 基于 `airlift`（AirTraffic 同步逃逸）漏洞，无需越狱。
> 本项目是 [Mak5er/AirCard](https://github.com/Mak5er/AirCard)（macOS 版）的 **Windows 移植 + 中文化**版本，
> 核心写入流程与原版一致，并额外提供读取卡面、备份与恢复等能力（见文末「与 macOS 原版的区别」）。

> [!IMPORTANT]
> **兼容性声明（使用前请先确认）**
> - 本软件已在 **Windows 10 / Windows 11** 平台上测试通过。
> - 测试所用的 iOS 设备系统版本为 **iOS 27.0**。
> - **除上述组合外，其余操作系统、其他平台，以及其它 iOS / 设备系统版本均未经验证**，
>   能否正常使用需由你自行测试，风险自负。

---

## ✨ 功能

- 🎨 **卡片换肤**：为 Apple Pay / 钱包卡片指定自定义卡面（ artwork、纹理、银行 logo）。
- 🔢 **锁屏密码主题（.passthm）**：将主题包中的按键图直接刷入锁屏键盘。
- 🧩 **主题制作器**：用一张壁纸自动无缝切片（海报切片），或逐键自定义。
- 🔍 **交互式取景**：在按键内实时平移/缩放图片，带 iPhone 实时预览。
- ✏️ **编辑现有主题**：直接把 Cowabunga / Nugget 的 `.passthm` 主题包拖进制作器，改完再导出或刷入。
- ⚡ **单卡 / 批量定制**：逐卡设置皮肤，或一键批量套用同一张图。
- 📱 **零操作卡片识别**：在 iPhone 钱包里点一下卡片，即可实时捕获卡片哈希。
- 📦 **免安装**：单文件绿色版 exe，无需安装 Python、无需命令行（连接设备仍需本机装有 iTunes 或
  「Apple 移动设备支持」组件，见「运行环境依赖」）。

---

## 📦 下载与运行

1. 从Releases中下载打包好的压缩包。
2. 解压后首先安装iTunes，安装完成后再打开aircard.exe即可。

> [!NOTE]
> **系统要求**
> - Windows 10 / 11（64 位）
> - 已安装 **iTunes** 或「Apple 移动设备支持」组件（详见下文）
> - 数据线连接的 iPhone（已解锁并信任此电脑）

---

## ⚙️ 运行环境依赖

### 未安装依赖时，能否使用全部功能？——不能

本软件**依赖 Apple 官方的 Windows 设备支持组件**才能与 iPhone 通信。若本机**未安装 iTunes
或「Apple 移动设备支持」组件**：

- 软件**仍可正常启动**，界面、主题制作器等**纯本地功能可以正常使用**；
- 但**所有需要连接 iPhone 的功能都不可用**。

### 必须安装的软件（任选其一）

| 软件 | 获取途径 |
| --- | --- |
| **Releases中下载压缩包**（推荐） | 从Releases中下载打包好的压缩包，解压后首先安装iTunes，安装完成后再打开aircard.exe即可 |


### 安装要点

1. 安装完成后**重启一次电脑**，确保相关服务与驱动生效。
2. 确认目录 `C:\Program Files\Common Files\Apple\Mobile Device Support\` 存在，
   且其中包含上述三个 DLL。
3. 首次用数据线连接 iPhone 时，需**解锁设备并点击「信任此电脑」**。
4. **不要同时混装**官网版 iTunes 与 Microsoft Store 版，以免组件版本冲突。

### 依赖缺失 / 异常时受影响的功能

| 功能 | 无依赖时 |
| --- | --- |
| 扫描卡片、识别卡片哈希 | ❌ 不可用 |
| 刷入卡片皮肤（单卡 / 批量） | ❌ 不可用 |
| 读取卡面 | ❌ 不可用 |
| 备份卡面 | ❌ 不可用 |
| 恢复原皮 / 从历史恢复 | ❌ 不可用 |
| 刷入锁屏密码主题（`.passthm`） | ❌ 不可用 |
| 主题制作器（海报切片 / 逐键自定义） | ✅ 可用（纯本地处理） |
| 导入、编辑并导出 `.passthm` 主题包 | ✅ 可用（纯本地处理） |
| 在线制作卡面入口 | ✅ 可用 |

> [!WARNING]
> 即使依赖已安装，若本机**从未用 iTunes / Apple Devices 与该 iPhone 同步过**、设备已锁屏、
> 未点「信任此电脑」，或装有 **MDM / 描述文件**，需要连接设备的功能同样会失败。

---

## 🎴 如何自定义 Apple 钱包卡片

1. 用 USB 数据线连接 iPhone（已解锁、已信任）。
2. 在 AirCard 的 **苹果钱包卡片** 页点击 **扫描卡片**。
3. 在 iPhone 上：
   - **连按两次侧边按钮（电源键）** 唤出 Apple Pay；
   - 通过 **面容 ID** 验证；
   - **点按你的卡片**（或再点一次）即可触发实时识别！
4. 点击任意卡片示意（或直接把图片拖到卡片上）设置皮肤。
5. 点击 **刷入皮肤**。
6. 在 iPhone 的多任务里 **强制关闭「钱包」App**（或重启）即可看到新卡面！

### 扫描不到卡片？

扫描器使用 iPhone 的统一日志服务（含 Info/Debug 事件）。在某些系统版本上，
旧版日志服务可能显示钱包活动但不输出包含卡片标识的资源查找消息（该现象在部分
iOS 18.x 上曾被观察到；本软件仅验证过 iOS 27.0，其它版本未经验证）。

打开 **日志** 抽屉，确认出现 `Connected to the unified device log stream`，
然后连按侧边按钮、验证、点按/切换卡片。若日志读取停止，请重新连接并解锁
iPhone 后再次扫描。被 iOS 替换为 `<private>` 的值无法被扫描器还原。

---

## 🔢 如何刷入锁屏密码主题（.passthm）

1. 顶部切换到 **密码主题（.passthm）** 页。
2. 把任意 `.passthm` 文件拖入窗口（或点击 **选择 .passthm 文件…**）。
3. AirCard 会解析主题并在数字键盘（0–9、*、#）上显示可交互预览。
4. 点击 **刷入密码主题**。
5. 重启 iPhone 以重载锁屏缓存，即可看到自定义密码按键！

> [!TIP]
> **全语言 / 粗体字支持**：
> AirCard 会自动为所有系统语言展开并刷入按键素材，同时生成标准与**粗体**
> 两套缓存位图（`--white` 与 `--white-bold`），无论 iOS 语言或辅助功能的
> 粗体文本设置如何都能正常生效。

### 主题制作器

- **海报切片**：导入一张竖版海报，程序按 915×1148 网格无缝切成 10 个按键。
- **逐键自定义**：对每个按键单独选择图片、缩放、平移（直接在手机预览上拖动）。
- 右键任意按键可清除；**采用切片结果** 可把海报切片一次性填入全部按键。

---

## 🛠️ 从源码构建

```bat
git clone <本仓库>
cd AirCard-Win
build.bat
```

- `build.bat` 会自动完成语法自检 → PyInstaller 打包，产物为 `dist\AirCard.exe`。
- 打包配置见 `AirCard.spec`（单文件、无控制台、内嵌图标与资源）。
- 依赖：Python 3.13+、PySide6 ≥ 6.11、Pillow ≥ 12、PyInstaller ≥ 6.22。

### 开发态运行

```bat
python AirCard.py
:: 或
python -m aircard
```

### 测试

```bat
python tests\smoke_ui.py        :: 离屏 UI 冒烟（布局 / 交互 / 对话框）
python tests\launch_smoke.py    :: 真入口启动冒烟（捕获导入/资源错误）
python tests\screenshot_real.py :: 真实平台截图（核对中文渲染）
```

---

## 🧩 与 macOS 原版的区别

原项目：**[https://github.com/Mak5er/AirCard](https://github.com/Mak5er/AirCard)**
（macOS 版，原作者 [@mak5er](https://github.com/mak5er)，MIT 许可证）。
本软件是其 **Windows 移植 + 中文化**版本，**并非官方发布**，与原作者无隶属或授权关系。

### 与原版的区别

- **运行平台**：原版面向 macOS（Apple Silicon / Intel 通用包）；本版面向 **Windows 10 / 11（64 位）**。
- **界面语言**：原版为英文界面；本版为 **全中文界面**。
- **分发形式**：原版为 `AirCard.dmg`；本版为**单文件绿色版 `AirCard.exe`**，无需安装 Python 或命令行。
- **运行依赖**：原版所需组件已内置、开箱即用；本版**需要本机已安装 iTunes 或「Apple 移动设备支持」组件**
  （详见上方「运行环境依赖」）。
- **功能范围**：本版在原版之外**额外提供了读取卡面、备份与恢复等能力**（见下）。

### 原版没有的功能（本版新增）

- **读取卡面**：原版只能写入卡面；本版可把设备上当前的卡面读回界面，用于辨认「哪张卡是哪张」。
- **备份卡面**：把指定卡片的当前卡面保存到本机 `aircard_backups/` 目录，作为回滚依据。
- **恢复原皮 / 从历史恢复**：把备份过的卡面写回设备，可退回出厂卡面或任一历史卡面。
- **刷入前自动备份**：首次刷入某张卡前，自动先备份其原卡面。
- **已刷卡显示最新皮肤**：刷过皮肤的卡，重新打开软件后预览会直接显示最近一次刷入的新皮肤；
  从未刷过皮肤的卡才显示原皮。
- **连接诊断**：一键输出 Apple 组件状态、设备识别与同步通道的诊断报告，便于排查连不上设备的问题。
- **每次启动重置**：每次启动自动清空上次扫描到的卡片列表，不保留历史扫描结果。
- **在线制作卡面入口**：跳转到在线卡面设计工具。

---

## 👥 贡献者 / 鸣谢

- **[@mak5er](https://github.com/mak5er)**（开发者）— [GitHub](https://github.com/mak5er) · [Twitter / X](https://x.com/mak5er)
- **[@Lumid-Off](https://github.com/Lumid-Off)**（贡献者 & 开发者）— [GitHub](https://github.com/Lumid-Off) · [Twitter / X](https://x.com/LumidOff)
- **[AirLift](https://github.com/0xjohnnydev/airlift)** by **[0xjohnny (@0xjohnnydev)](https://github.com/0xjohnnydev)**：AirTraffic/ATAirlock 沙箱逃逸与概念验证，`AirliftFFI` 的基础。

## ☕ 支持

如果 AirCard 对你有帮助，可以支持原作者的后续开发：

- **PayPal**：[Donate via PayPal](https://www.paypal.com/donate/?hosted_button_id=98QRTC2HFRA4Y)
- **TON**：`UQBm9KPhtMw-XVVjirUoa09wzrlyWsbeZhKfefl1Uw-qNZ-r`
- **USDT (TRC20)**：`TDkDMCyjYxgvkWUnQiF5Erk2RyPQMT6G1n`
- **USDT / BNB (BEP20)**：`0x0954dc491c502849d04956ef74634aa5931a08e8`
