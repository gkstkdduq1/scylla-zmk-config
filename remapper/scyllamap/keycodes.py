"""HID usage tables and Windows virtual-key translation.

ZMK encodes a key press parameter as:

    param1 = (implicit_mods << 24) | (usage_page << 16) | usage_id

Usage page 0x07 is the standard keyboard page, 0x0C is consumer control.
Verified empirically against a live keymap: ESC -> 0x00070029, TAB -> 0x0007002B.
"""

PAGE_KEY = 0x07
PAGE_CONSUMER = 0x0C

MOD_LCTL = 0x01
MOD_LSFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08
MOD_RCTL = 0x10
MOD_RSFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

MOD_NAMES = [
    (MOD_LCTL, "LCTRL"), (MOD_LSFT, "LSHIFT"), (MOD_LALT, "LALT"), (MOD_LGUI, "LGUI"),
    (MOD_RCTL, "RCTRL"), (MOD_RSFT, "RSHIFT"), (MOD_RALT, "RALT"), (MOD_RGUI, "RGUI"),
]

# HID keyboard usage id -> short display name.
KEY_NAMES = {
    0x00: "None",
    0x28: "ENTER", 0x29: "ESC", 0x2A: "BSPC", 0x2B: "TAB", 0x2C: "SPACE",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\",
    0x33: ";", 0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/",
    0x39: "CAPS",
    0x46: "PSCRN", 0x47: "SLCK", 0x48: "PAUSE",
    0x49: "INS", 0x4A: "HOME", 0x4B: "PGUP", 0x4C: "DEL", 0x4D: "END", 0x4E: "PGDN",
    0x4F: "RIGHT", 0x50: "LEFT", 0x51: "DOWN", 0x52: "UP",
    0x53: "NUMLK", 0x54: "KP/", 0x55: "KP*", 0x56: "KP-", 0x57: "KP+", 0x58: "KPENT",
    0x62: "KP0", 0x63: "KP.",
    0x85: "KP,",
    0x90: "한/영", 0x91: "한자",
    0xE0: "LCTRL", 0xE1: "LSHIFT", 0xE2: "LALT", 0xE3: "LGUI",
    0xE4: "RCTRL", 0xE5: "RSHIFT", 0xE6: "RALT", 0xE7: "RGUI",
}
for _i in range(26):
    KEY_NAMES[0x04 + _i] = chr(ord("A") + _i)
for _i, _d in enumerate("123456789"):
    KEY_NAMES[0x1E + _i] = _d
KEY_NAMES[0x27] = "0"
for _i in range(12):
    KEY_NAMES[0x3A + _i] = "F%d" % (_i + 1)
for _i in range(12):
    KEY_NAMES[0x68 + _i] = "F%d" % (_i + 13)
for _i in range(9):
    KEY_NAMES[0x59 + _i] = "KP%d" % (_i + 1)

CONSUMER_NAMES = {
    0xB5: "NEXT", 0xB6: "PREV", 0xB7: "STOP", 0xCD: "PLAY/PAUSE",
    0xE2: "MUTE", 0xE9: "VOL+", 0xEA: "VOL-",
}

# Windows virtual-key code -> HID keyboard usage id.
VK_TO_USAGE = {
    0x08: 0x2A, 0x09: 0x2B, 0x0D: 0x28, 0x13: 0x48, 0x14: 0x39, 0x1B: 0x29,
    0x20: 0x2C, 0x21: 0x4B, 0x22: 0x4E, 0x23: 0x4D, 0x24: 0x4A,
    0x25: 0x50, 0x26: 0x52, 0x27: 0x4F, 0x28: 0x51,
    0x2C: 0x46, 0x2D: 0x49, 0x2E: 0x4C,
    0x5B: 0xE3, 0x5C: 0xE7,
    0x6A: 0x55, 0x6B: 0x57, 0x6D: 0x56, 0x6E: 0x63, 0x6F: 0x54,
    0x90: 0x53, 0x91: 0x47,
    0xA0: 0xE1, 0xA1: 0xE5, 0xA2: 0xE0, 0xA3: 0xE4, 0xA4: 0xE2, 0xA5: 0xE6,
    0xBA: 0x33, 0xBB: 0x2E, 0xBC: 0x36, 0xBD: 0x2D, 0xBE: 0x37, 0xBF: 0x38,
    0xC0: 0x35, 0xDB: 0x2F, 0xDC: 0x31, 0xDD: 0x30, 0xDE: 0x34,
    # Korean IME keys
    0x15: 0x90, 0x19: 0x91,
}
VK_TO_USAGE[0x30] = 0x27
for _i in range(1, 10):
    VK_TO_USAGE[0x30 + _i] = 0x1E + _i - 1
for _i in range(26):
    VK_TO_USAGE[0x41 + _i] = 0x04 + _i
VK_TO_USAGE[0x60] = 0x62
for _i in range(1, 10):
    VK_TO_USAGE[0x60 + _i] = 0x59 + _i - 1
for _i in range(12):
    VK_TO_USAGE[0x70 + _i] = 0x3A + _i
for _i in range(12):
    VK_TO_USAGE[0x7C + _i] = 0x68 + _i

USAGE_TO_VK = {u: v for v, u in VK_TO_USAGE.items()}


def encode(usage: int, page: int = PAGE_KEY, mods: int = 0) -> int:
    return ((mods & 0xFF) << 24) | ((page & 0xFF) << 16) | (usage & 0xFFFF)


def decode(param: int):
    return (param >> 24) & 0xFF, (param >> 16) & 0xFF, param & 0xFFFF


def key_label(param: int) -> str:
    mods, page, usage = decode(param)
    if page == PAGE_CONSUMER:
        base = CONSUMER_NAMES.get(usage, "0x%02X" % usage)
    else:
        base = KEY_NAMES.get(usage, "0x%02X" % usage)
    if mods:
        prefix = "+".join(n for bit, n in MOD_NAMES if mods & bit)
        return "%s(%s)" % (prefix, base)
    return base


# 58 usages that are all distinct and all reachable as Windows virtual keys.
# Used to temporarily paint every physical position with a unique probe key.
PROBE_USAGES = (
    [0x04 + i for i in range(26)]          # A-Z
    + [0x1E + i for i in range(9)] + [0x27]  # 1-9, 0
    + [0x3A + i for i in range(12)]        # F1-F12
    + [0x68 + i for i in range(11)]        # F13-F23
)
assert len(PROBE_USAGES) >= 58, len(PROBE_USAGES)
assert len(set(PROBE_USAGES)) == len(PROBE_USAGES)
