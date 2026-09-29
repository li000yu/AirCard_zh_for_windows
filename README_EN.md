[简体中文](README.md) | **English**

![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-blue)
![Python](https://img.shields.io/badge/Python-3.13%2B-blue)
![iOS](https://img.shields.io/badge/iOS-27.0-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-v1.9.6-orange)

# AirCard for Windows 🎴

> **Apple Wallet card skinning and Lock Screen passcode keypad theming tool — Windows edition**
> Built on the `airlift` (AirTraffic sync escape) vulnerability. **No jailbreak required.**
> This project is a **Windows port + Chinese localisation** of
> [Mak5er/AirCard](https://github.com/Mak5er/AirCard) (the macOS original).
> The core write pipeline is identical to the original; this port additionally provides
> card-art read-back, backup and restore (see "Differences from the macOS original").

> [!IMPORTANT]
> **Compatibility notice — please read before using**
> - This software has been tested on **Windows 10 / Windows 11** only.
> - The iOS device used for testing runs **iOS 27.0**.
> - **No other operating system, platform, or iOS / device version has been verified.**
>   Whether it works elsewhere is for you to test — **use at your own risk.**

---

## 📖 Introduction

AirCard lets you replace the artwork of Apple Pay / Wallet cards and the artwork of the
Lock Screen passcode keypad on a **non-jailbroken iPhone**, from a **Windows** PC.

It works by abusing the `airlift` exploit: the same AirTraffic sync channel that iTunes uses
to push media to a device can also be used to write files into protected containers —
including the Wallet pass container (`/Passes/Cards/<hash>.pkpass/`) and the passcode
keypad asset cache. AirCard wraps that capability in a native GUI, so no Python, no
command line and no jailbreak are needed.

- Upstream exploit: [airlift](https://github.com/0xjohnnydev/airlift) by [@0xjohnnydev](https://github.com/0xjohnnydev)
- macOS original: [Mak5er/AirCard](https://github.com/Mak5er/AirCard) by [@mak5er](https://github.com/mak5er)
- This port is **not an official release** and has no affiliation with, or endorsement from,
  the original author.

---

## ✨ Core features

- 🎨 **Card skinning** — assign custom artwork to Apple Pay / Wallet cards (artwork,
  texture, bank logo).
- 🔢 **Passcode themes (`.passthm`)** — flash the keypad images from a theme bundle straight
  into the Lock Screen keyboard.
- 🧩 **Theme maker** — auto-slice one portrait poster into a seamless keypad grid, or
  customise each key individually.
- 🔍 **Interactive framing** — pan / zoom the image inside a key in real time, with a live
  iPhone preview.
- ✏️ **Edit existing themes** — drop a Cowabunga / Nugget `.passthm` bundle into the maker,
  edit it, then export or flash it.
- ⚡ **Single-card or batch** — skin cards one by one, or apply one image to several cards at
  once.
- 📱 **Zero-effort card identification** — tap a card in the iPhone Wallet and its hash is
  captured live.
- 🗂️ **Card-art backup & restore** — read the current art back, back it up to disk, and
  restore the original or any historical version.
- 🩺 **Connection diagnostics** — one click produces a report on Apple component status,
  device identification and sync-channel handshake, to debug "device not detected" problems.
- 📦 **Portable** — a single green (installation-free) `AirCard.exe`. No Python, no command
  line. Connecting a device still requires iTunes or the "Apple Mobile Device Support"
  component on this PC (see "Runtime dependencies").

---

## 📦 Installation & running

> [!NOTE]
> **System requirements**
> - Windows 10 / 11 (64-bit)
> - **iTunes** or the **Apple Mobile Device Support** component installed (see below)
> - An iPhone connected by cable, **unlocked and trusting this computer**

### Steps

1. Download the packaged archive from
   [Releases](https://github.com/li000yu/AirCard_zh_for_windows/releases).
2. Extract it anywhere you like.
3. **Install iTunes first** (or make sure "Apple Mobile Device Support" is present).
4. **Reboot once** after installation so the services and drivers take effect.
5. Run `AirCard.exe`. No installation of the program itself is required.

### Runtime dependencies

This software **depends on Apple's official Windows device support components** to talk to
an iPhone. If neither iTunes nor "Apple Mobile Device Support" is installed:

- The app **still starts normally**, and purely local features (theme maker, `.passthm`
  import/export) **work fine**;
- but **every feature that needs the iPhone is unavailable**.

Install **one** of the following (do **not** mix the apple.com iTunes build with the
Microsoft Store build — version conflicts will occur):

| Software | Where to get it |
| --- | --- |
| **iTunes from the release archive** (recommended) | Included in the archive downloaded from [Releases](https://github.com/li000yu/AirCard_zh_for_windows/releases) — install it before running `AirCard.exe` |

### Verify the dependencies

1. After installation, **reboot once**.
2. Confirm that `C:\Program Files\Common Files\Apple\Mobile Device Support\` exists and
   contains the required DLLs.
3. The first time you connect the iPhone by cable, **unlock it and tap "Trust This Computer"**.

### What stops working without the dependencies

| Feature | Without dependencies |
| --- | --- |
| Scan cards / capture card hash | ❌ Unavailable |
| Flash card skin (single / batch) | ❌ Unavailable |
| Read card art | ❌ Unavailable |
| Back up card art | ❌ Unavailable |
| Restore original art / restore from history | ❌ Unavailable |
| Flash passcode theme (`.passthm`) | ❌ Unavailable |
| Theme maker (poster slicing / per-key) | ✅ Works (purely local) |
| Import, edit and export `.passthm` bundles | ✅ Works (purely local) |
| Online card-art designer shortcut | ✅ Works |

> [!WARNING]
> Even with the dependencies installed, device features also fail if this PC has **never
> synced with that iPhone through iTunes / Apple Devices**, if the device is **locked**, if
> you never tapped **"Trust This Computer"**, or if an **MDM / configuration profile** is
> installed.

---

## 🚀 Usage examples

### Example 1 — Give an Apple Wallet card a custom skin

1. Connect the iPhone over USB (unlocked, trusted).
2. In AirCard, open the **Apple Wallet cards** tab and click **Scan cards**.
3. On the iPhone:
   - **Double-press the side button** to bring up Apple Pay;
   - authenticate with **Face ID**;
   - **tap your card** (or tap it once more) — it is identified live.
4. Click any card tile (or drag an image straight onto the tile) to pick its skin.
5. Click **Flash skin**.
6. **Force-quit the Wallet app** from the iPhone app switcher (or reboot) to see the new art.

### Example 2 — Flash a Lock Screen passcode theme (`.passthm`)

1. Switch to the **Passcode theme (.passthm)** tab at the top.
2. Drag any `.passthm` file into the window, or click **Choose .passthm file…**.
3. AirCard parses the theme and shows an interactive preview on the keypad (0–9, `*`, `#`).
4. Click **Flash passcode theme**.
5. **Reboot the iPhone** to reload the Lock Screen cache and see the custom keys.

> [!TIP]
> **All languages / bold text support:** AirCard automatically expands and flashes the key
> assets for every system language, and generates both standard and **bold** cache bitmaps
> (`--white` and `--white-bold`), so the theme applies regardless of the iOS language or the
> Accessibility bold-text setting.

### Example 3 — Make your own theme

- **Poster slicing** — import one portrait poster; the app slices it seamlessly into the 10
  keys on a 915×1148 grid.
- **Per-key customisation** — choose an image, scale and pan for each key individually
  (drag directly on the phone preview).
- Right-click any key to clear it; **Apply slice result** fills every key from the poster
  slice at once.

### Example 4 — Back up and restore card art

1. After a scan, click **Read card art** on a card tile to pull the current art into the UI.
2. Click **Back up card art** to save it into the `aircard_backups/` folder next to
   `AirCard.exe`.
3. Later, use **Restore original art** to roll back to the factory art, or **Restore from
   history** to pick any previously saved PNG.

### Troubleshooting: no cards detected

The scanner uses the iPhone's unified logging service (including Info/Debug events). On some
iOS versions the legacy log service may show Wallet activity without emitting the resource
lookup messages that contain card identifiers (observed on parts of iOS 18.x; **this build
has only been verified on iOS 27.0**).

Open the **Log** drawer and confirm that `Connected to the unified device log stream`
appears, then double-press the side button, authenticate and tap / switch cards. If log
reading stalls, reconnect and unlock the iPhone and scan again. Values that iOS redacts as
`<private>` cannot be recovered by the scanner.

---

## ⚙️ Configuration notes

AirCard has **no settings dialog and no config file to edit**. The only configuration is
where it stores state, plus one command-line switch:

| Item | Location / usage |
| --- | --- |
| Card-art backups | `aircard_backups/` **next to `AirCard.exe`** (created automatically). When run from source, this is the project root. |
| Saved card hashes | `%USERPROFILE%\.aircard_cards.json` — a plain JSON array of hash strings, kept compatible with the macOS original's store. |
| Card metadata | `%USERPROFILE%\.aircard_card_meta.json` — discovery order / timestamp sidecar. |
| Legacy store | `%USERPROFILE%\.lumicards_cards.json` — read for migration only. |
| Skip the agreement dialog | `AirCard.exe --no-agreement`, or set the environment variable `AIRCARD_NO_AGREEMENT=1`. Intended for automated testing. |

Behaviour worth knowing:

- **Reset on every launch** — the card list from the previous session is cleared at startup;
  scan again each time.
- **Cards that have been flashed show their newest skin** — the preview shows the most
  recently flashed skin; only never-flashed cards show the factory art.
- **Flashing and scanning are mutually exclusive** — the app stops the log stream before any
  device read/write, because a busy log stream saturates the USB channel and makes writes fail.
- **Deleting `aircard_backups/` deletes your restore points.** Back that folder up if you
  care about the original art.

---

## 🛠️ Build from source

```bat
git clone <this repository>
cd AirCard-Win
build.bat
```

- `build.bat` runs a syntax self-check, then PyInstaller. Output: `dist\AirCard.exe`.
- Packaging configuration lives in `AirCard.spec` (single file, no console, embedded icon
  and resources).
- Requirements: Python 3.13+, PySide6 ≥ 6.11, Pillow ≥ 12, PyInstaller ≥ 6.22.

### Run in development mode

```bat
python AirCard.py
:: or
python -m aircard
```

### Tests

```bat
python tests\smoke_ui.py        :: offscreen UI smoke test (layout / interaction / dialogs)
python tests\launch_smoke.py    :: real entry-point launch smoke test (catches import errors)
python tests\screenshot_real.py :: real-platform screenshots (verify CJK rendering)
```

---

## 🧩 Differences from the macOS original

Original project: **[https://github.com/Mak5er/AirCard](https://github.com/Mak5er/AirCard)**
(macOS, original author [@mak5er](https://github.com/mak5er), MIT licence).
This is a **Windows port + Chinese localisation**; it is **not an official release** and has
no affiliation with, or authorisation from, the original author.

### Differences

- **Platform**: the original targets macOS (Apple Silicon / Intel universal binary); this
  version targets **Windows 10 / 11 (64-bit)**.
- **UI language**: the original is English; this version ships a **fully Chinese UI**.
- **Distribution**: the original ships as `AirCard.dmg`; this version is a **single portable
  `AirCard.exe`** — no Python, no command line.
- **Runtime dependencies**: the original is self-contained; this version **requires iTunes or
  the "Apple Mobile Device Support" component** on the PC (see "Runtime dependencies").
- **Scope**: this version adds read-back, backup and restore on top of the original
  (see below).

### Features the original does not have

- **Read card art** — the original can only write; this version can read the current art back
  into the UI, which is how you tell "which card is which".
- **Back up card art** — save a card's current art into the local `aircard_backups/` folder
  as a rollback point.
- **Restore original art / restore from history** — write a backed-up art back to the device,
  returning to the factory art or any historical art.
- **Automatic backup before flashing** — the first time a card is flashed, its original art
  is backed up first.
- **Flashed cards show their newest skin** — reopening the app shows the most recently
  flashed skin; only never-flashed cards show the factory art.
- **Connection diagnostics** — one click dumps Apple component status, device identification
  and sync-channel handshake state.
- **Reset on every launch** — the previously scanned card list is cleared at startup.
- **Online card-art designer shortcut** — opens
  <https://tonychenn.cn/tools/carddesign/index.html> in your browser.

---

## 👥 Contributing

Contributions are welcome. Please keep the following in mind:

1. **Fork and branch.** Create a topic branch off `master`, e.g. `feat/xyz` or `fix/xyz`.
2. **Stay close to upstream.** The device write pipeline must stay behaviourally identical to
   the macOS original — do not "improve" the AirTraffic sequence on your own; port it line by
   line.
3. **Keep the UI native.** PySide6 / Qt Widgets only. **No HTML, no Chromium, no web views.**
   Use Pillow instead of macOS-only tools such as `sips`.
4. **Keep the UI Chinese.** All user-visible strings stay in Simplified Chinese; code
   identifiers stay in English.
5. **Do not swallow exceptions.** Every `except` must either handle the error or surface it
   through the reporter callback, so failures remain diagnosable.
6. **Add tests.** Put smoke tests under `tests/` and wire them into `build.bat` as a
   pre-packaging gate.
7. **Run the full gate before opening a PR:**
   ```bat
   python tests\smoke_imports.py
   python tests\smoke_ui.py
   python tests\launch_smoke.py
   build.bat
   ```
8. **Describe your test setup** in the PR: Windows version, iOS version, device model, and
   whether the device actually synced with iTunes before. Untested combinations are the main
   source of regressions here.
9. **Respect the disclaimer.** This project touches private Apple APIs and undocumented
   behaviour. Keep the compatibility notice accurate; never claim support for a
   platform / iOS version that has not actually been verified.

---

## 👥 Credits

- **[@mak5er](https://github.com/mak5er)** (developer) — [GitHub](https://github.com/mak5er) · [Twitter / X](https://x.com/mak5er)
- **[@Lumid-Off](https://github.com/Lumid-Off)** (contributor & developer) — [GitHub](https://github.com/Lumid-Off) · [Twitter / X](https://x.com/LumidOff)
- **[AirLift](https://github.com/0xjohnnydev/airlift)** by **[0xjohnny (@0xjohnnydev)](https://github.com/0xjohnnydev)** — the AirTraffic/ATAirlock sandbox escape and proof of concept that `AirliftFFI` builds on.

---

## 📄 License

Released under the **MIT License**; see [LICENSE](LICENSE) for the full text.

This repository (`AirCard_zh for windows`) is a Windows port of
<https://github.com/Mak5er/AirCard> (original author [@mak5er](https://github.com/mak5er))
and **reuses that project's MIT licence**, retaining the upstream copyright notice in full.

> [!CAUTION]
> **Disclaimer.** This software writes into protected containers on your device through an
> undocumented exploit. It is provided **"AS IS", without warranty of any kind**. Back up
> your device before use. The authors accept no liability for data loss, device malfunction,
> voided warranty, or any other consequence of using it.

---

## ☕ Support

If AirCard helped you, please support the original author's continued development:

- **PayPal**: [Donate via PayPal](https://www.paypal.com/donate/?hosted_button_id=98QRTC2HFRA4Y)
- **TON**: `UQBm9KPhtMw-XVVjirUoa09wzrlyWsbeZhKfefl1Uw-qNZ-r`
- **USDT (TRC20)**: `TDkDMCyjYxgvkWUnQiF5Erk2RyPQMT6G1n`
- **USDT / BNB (BEP20)**: `0x0954dc491c502849d04956ef74634aa5931a08e8`
