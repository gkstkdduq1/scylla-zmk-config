# Scylla Remapper

Press-to-remap for a ZMK keyboard. You pick the key by **pressing it**, and you
pick what goes there by **pressing that too**. No dropdown hunting.

```powershell
pip install -r requirements.txt
.\run.ps1      # open the window
.\tray.ps1     # start hidden in the notification area
```

**Close ZMK Studio first.** The serial port is exclusive — only one app can hold
it at a time. For the same reason the tray app connects only while its window is
open and releases the port when you close it, so leaving it resident does not
lock Studio out.

## Tray and startup

`tray.ps1` puts an icon in the notification area. Left-click opens the window;
right-click gives **열기 / Windows 시작 시 실행 / 종료**.

The startup toggle writes a shortcut to your Startup folder:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Scylla Remapper.lnk
```

A shortcut rather than a registry Run key — it needs no elevation, and it shows
up in Explorer and in Task Manager's Startup tab where you would expect to find
it. It launches `pythonw.exe`, so there is no console window.

## How it works

ZMK Studio's RPC protocol has exactly two notifications,
`unsaved_changes_status_changed` and `lock_state_changed`. There is no key-event
notification, so the keyboard never tells a host app "position 34 was pressed".
That is why Studio itself makes you click the key on screen.

The way around it is to make the keyboard tell us anyway:

1. Temporarily write a **distinct probe keycode to every one of the 58
   positions** on the base layer (A–Z, 0–9, F1–F23 — all distinct Windows
   virtual keys).
2. You press the key you want to change. Exactly one probe code arrives, which
   identifies the position with no ambiguity.
3. `discard_changes` restores everything.

Studio stages edits until an explicit save, so the probe never touches saved
settings. If the app dies mid-probe, power-cycling the keyboard restores it —
nothing was ever written to flash.

Because a discard is all-or-nothing, the probe refuses to run while there are
unsaved changes. Save or revert them first; the app tells you when this happens.

## Protocol notes

Learned from the ZMK sources and verified against a live keyboard:

- Transport is CDC-ACM serial. One frame is
  `SOF(0xAB) | escaped protobuf | EOF(0xAD)`. Payload bytes equal to
  `0xAB/0xAC/0xAD` get an `ESC(0xAC)` prefix and are written literally — no XOR.
- A key press parameter is `(mods << 24) | (usage_page << 16) | usage_id`.
  Page `0x07` is the keyboard page. Confirmed by reading a live keymap:
  ESC → `0x00070029`, TAB → `0x0007002B`, Q → `0x00070014`.
- Behavior ids are assigned per firmware build, so the app reads them at
  runtime rather than hardcoding. On this build Key Press is `2`.

## Files

| file | what |
| --- | --- |
| `scyllamap/rpc.py` | framing, request/response correlation, subsystem wrappers |
| `scyllamap/keycodes.py` | HID usage tables, Windows VK translation, probe set |
| `scyllamap/gui.py` | the remap window |
| `scyllamap/labels.py` | renders bindings from the firmware's own metadata |
| `scyllamap/app.py` | tray icon, entry point |
| `scyllamap/startup.py` | Startup-folder shortcut |
| `proto/` | ZMK Studio `.proto` files, vendored from zmk-studio-messages |

`*_pb2.py` are generated. To regenerate after updating `proto/`:

```powershell
python -m grpc_tools.protoc -Iproto --python_out=scyllamap proto\*.proto
```

## Limits

Key cap text comes from the keyboard, not from a table here: every behavior
parameter arrives with a name (`Select Profile`, `BLE Output`, ...), so a
firmware with extra behaviors labels itself. The abbreviation tables in
`labels.py` only shorten text to fit a key cap and fall back to the firmware's
own wording, so they cannot make a label wrong.

Assigns **Key Press** bindings only — that is what "press the key you want"
means. Layer-taps, mod-taps, combos and macros still need ZMK Studio or a
firmware rebuild. Modifiers held during capture become implicit mods, so
Ctrl+C captures as `LCTRL(C)`.
