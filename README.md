# Scylla — ZMK config with ZMK Studio

Wireless Scylla (4x6+5, 58 keys) on Pro Micro nRF52840 controllers, **no dongle**:
left half is BLE central, right half is peripheral.

The point of this config is **ZMK Studio**: after one flash you remap keys from a
GUI, live, without ever rebuilding firmware again.

## Build

Push this repo to GitHub. The Actions workflow builds on every push and produces
a `firmware` artifact containing:

| file | flash to |
| --- | --- |
| `scylla_left_studio-nice_nano-zmk.uf2` | left half (central, Studio-enabled) |
| `scylla_right-nice_nano-zmk.uf2` | right half (peripheral) |
| `settings_reset-nice_nano-zmk.uf2` | either half, to wipe pairing/settings |

To flash: double-tap the reset button, a `NICENANO` USB drive appears, drag the
`.uf2` onto it.

**First flash order:** `settings_reset` on both halves → then `left` and `right`.

## Using ZMK Studio

1. Install the native app from https://zmk.studio/download (Windows build exists).
2. **Plug the LEFT half into the PC with USB-C.**
   On Windows, Studio's BLE transport is not supported — Windows' Bluetooth stack
   will not hand out GATT access to a device paired as HID. USB is the only route
   here. macOS and Linux can do it over BLE.
3. Connect in the app, then press the **Studio Unlock** chord: hold **Lower**
   (left inner thumb, `&mo 1`) and press the **bottom-left corner key** (the one
   that is `LCTRL` on the base layer). Without unlocking, Studio is read-only.

   Also added on that layer: **Lower + bottom-right corner key** = `&bootloader`,
   so you can enter flash mode without reaching for the reset button.
4. Remap, then Save. Changes are written to the keyboard's flash and survive
   unplugging — the keyboard keeps them on any host it connects to.

Don't want the unlock step? Set `CONFIG_ZMK_STUDIO_LOCKING=n` in
`config/scylla.conf` and reflash the left half once.

### The one gotcha

Once you save anything in Studio, the keyboard runs from flash settings and
**ignores `config/scylla.keymap`**. Later edits to that file do nothing until you
run "Restore Stock Settings" in Studio. Pick one source of truth: either edit the
file and rebuild, or edit in Studio. Studio is the whole point here, so the file
is just the seed/fallback.

## Layout notes

Studio needs the keymap to come from a `zmk,physical-layout`, not a
`chosen zmk,matrix_transform`. The upstream community shield had the layout file
orphaned (it referenced a `&scylla_transform` label that didn't exist and was
never `#include`d), which is why Studio only worked on their dongle build. Fixed
here in `boards/shields/scylla/`:

- `scylla.dtsi` — kscan matrix + `scylla_transform`, no `chosen` transform
- `scylla-layouts.dtsi` — the 58-key `zmk,physical-layout` Studio draws

Shield base adapted from https://github.com/amadeusolofsson/zmk-scylla — its
row/col GPIO mapping is byte-identical to the pin mapping in the working 2023
config at https://github.com/gkstkdduq1/zmk-config, so the wiring is confirmed.

The keymap in `config/scylla.keymap` is ported from that same 2023 config
(QWERTY base + lower + raise), plus the two keys listed above.

## Pro Micro nRF52840 clones (SuperMini etc.)

The build targets `nice_nano//zmk`, which defaults to nice!nano **v2**. Clones are
pin-compatible so keys work fine, but the battery voltage divider differs — the
reported battery percentage may be wrong or pinned. If that matters, the divider
can be redefined in a board overlay; ask and it's a small addition.
