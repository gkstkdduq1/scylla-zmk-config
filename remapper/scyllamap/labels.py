"""Turn a BehaviorBinding into human text.

Nothing here hardcodes what a behavior means. The keyboard ships parameter
metadata over RPC - every constant carries a name like "Select Profile" or
"BLE Output" - so we match the binding's parameters against that metadata and
render whatever the firmware says. A build with extra behaviors labels itself
correctly with no changes here.

The abbreviation tables below only shorten text to fit on a key cap. Anything
missing from them falls back to the firmware's own wording, so they can never
make a label wrong - only longer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keycodes as kc  # noqa: E402

# Behavior display-name -> key cap abbreviation.
SHORT_BEHAVIOR = {
    "Bluetooth": "BT",
    "Output Selection": "OUT",
    "Bootloader": "BOOT",
    "Studio Unlock": "UNLOCK",
    "Transparent": "▽",
    "None": "×",
    "Caps Word": "CAPSWD",
    "Key Repeat": "REPEAT",
    "Grave/Escape": "GRESC",
    "Reset": "RESET",
    "Momentary Layer": "MO",
    "To Layer": "TO",
    "Toggle Layer": "TOG",
    "Sticky Layer": "SL",
    "Sticky Key": "SK",
    "Key Toggle": "KT",
    "Layer-Tap": "LT",
    "Mod-Tap": "MT",
    "Soft Off": "OFF",
}

# Parameter constant name -> key cap abbreviation.
SHORT_CONST = {
    "Select Profile": "",            # the profile number says it
    "Clear Selected Profile": "CLR",
    "Clear All Profiles": "CLR ALL",
    "Next Profile": "NEXT",
    "Previous Profile": "PREV",
    "Disconnect Profile": "DISC",
    "Toggle Outputs": "TOG",
    "USB Output": "USB",
    "BLE Output": "BLE",
    "No Output": "OFF",
}

# Behaviors whose sole parameter is the key itself; the behavior name adds noise.
BARE_KEY_BEHAVIORS = {"Key Press"}


def _initials(text, limit=6):
    words = [w for w in text.replace("-", " ").split() if w]
    if not words:
        return text[:limit]
    if len(words) == 1:
        return words[0][:limit].upper()
    return "".join(w[0] for w in words)[:limit].upper()


class Catalog:
    """Behavior metadata read once from the keyboard."""

    def __init__(self, conn):
        self.behaviors = {}
        for bid in conn.list_behaviors():
            try:
                self.behaviors[bid] = conn.behavior_details(bid)
            except Exception:
                pass

    def name(self, behavior_id):
        d = self.behaviors.get(behavior_id)
        return d.display_name if d else "behavior %d" % behavior_id

    @staticmethod
    def _match(descs, value):
        """Pick the parameter descriptor that applies to `value`."""
        descs = list(descs)
        if not descs:
            return None
        for d in descs:
            if d.WhichOneof("value_type") == "constant" and d.constant == value:
                return d
        for d in descs:
            if d.WhichOneof("value_type") in ("hid_usage", "layer_id", "range"):
                return d
        return None

    def resolve(self, binding):
        """-> (descriptor_for_param1, descriptor_for_param2) or (None, None)."""
        d = self.behaviors.get(binding.behavior_id)
        if not d:
            return None, None
        for ms in d.metadata:
            d1 = self._match(ms.param1, binding.param1)
            if list(ms.param1) and d1 is None:
                continue
            d2 = self._match(ms.param2, binding.param2)
            if list(ms.param2) and d2 is None:
                continue
            return d1, d2
        return None, None

    # -- rendering ---------------------------------------------------------

    def _param_text(self, desc, value, layers, short):
        if desc is None:
            return None
        vt = desc.WhichOneof("value_type")
        if vt == "constant":
            if short:
                if desc.name in SHORT_CONST:
                    return SHORT_CONST[desc.name] or None
                return _initials(desc.name)
            return desc.name
        if vt == "hid_usage":
            return kc.key_label(value)
        if vt == "layer_id":
            for i, layer in enumerate(layers or []):
                if layer.id == value:
                    return layer.name or str(i)
            return str(value)
        if vt == "range":
            return str(value)
        return None

    def label(self, binding, layers=None):
        """Short text for a key cap."""
        name = self.name(binding.behavior_id)
        d1, d2 = self.resolve(binding)

        if name in BARE_KEY_BEHAVIORS and d1 is not None:
            return kc.key_label(binding.param1)

        head = SHORT_BEHAVIOR.get(name, _initials(name))
        parts = [head]
        for desc, value in ((d1, binding.param1), (d2, binding.param2)):
            text = self._param_text(desc, value, layers, short=True)
            if text:
                parts.append(text)
        return " ".join(parts)

    def describe(self, binding, layers=None):
        """Full sentence for the detail line."""
        name = self.name(binding.behavior_id)
        d1, d2 = self.resolve(binding)
        bits = []
        for desc, value in ((d1, binding.param1), (d2, binding.param2)):
            text = self._param_text(desc, value, layers, short=False)
            if not text:
                continue
            # For a constant the descriptor name IS the value's name, so
            # "name: text" would just say the same thing twice.
            if desc.WhichOneof("value_type") == "constant" or not desc.name:
                bits.append(text)
            else:
                bits.append("%s: %s" % (desc.name, text))
        if not bits:
            return name
        return "%s  (%s)" % (name, ", ".join(bits))
