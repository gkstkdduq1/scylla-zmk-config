"""Press-to-remap GUI for a ZMK keyboard.

The point of this tool: you pick the key by PRESSING it on the keyboard, and you
pick what goes there by PRESSING that too. No dropdown hunting.

Identifying which physical key was pressed is the hard part - ZMK's Studio RPC
has no key-event notification, so the keyboard never tells us "position 34 was
pressed". We work around it: temporarily paint all 58 positions with distinct
probe keycodes, read which one arrives, then discard. Because Studio stages
edits until an explicit save, the probe never touches saved settings.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rpc             # noqa: E402
import keycodes as kc  # noqa: E402

BEHAVIOR_KEY_PRESS = 2
UNIT = 0.46          # px per physical-layout unit (100 units == 1u key)
PAD = 14

BG = "#1b1d21"
KEY_BG = "#2a2e34"
KEY_SEL = "#3d6fd6"
KEY_TARGET = "#c8781e"
FG = "#e6e6e6"
DIM = "#8b9199"
OK_C = "#7fd67f"
WARN_C = "#e0a76c"
ERR_C = "#e06c6c"

LAYER_LABELS = {6: "MO %d", 13: "TO %d", 14: "TOG %d", 11: "SL %d"}
PLAIN_LABELS = {1: "CapsWd", 3: "GrEsc", 4: "Repeat", 9: "None", 12: "Reset",
                16: "Boot", 18: "Unlock", 19: "TRANS", 8: "ModTap", 7: "LT",
                15: "BT", 17: "OUT"}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Scylla Remapper")
        self.configure(bg=BG)
        self.conn = None
        self.keymap = None
        self.layout = None
        self.layer_index = 0
        self.selected = None      # key position
        self.mode = "idle"        # idle | probe | capture
        self._probe_map = {}
        self._key_items = {}

        self._build_ui()
        self.bind_all("<KeyPress>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self.connect)

    # -- ui -----------------------------------------------------------------

    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=PAD, pady=(PAD, 6))

        self.status = tk.Label(top, text="connecting...", bg=BG, fg=FG,
                               anchor="w", font=("Segoe UI", 10))
        self.status.pack(side="left")

        self.btn_save = tk.Button(top, text="저장", command=self.save,
                                  state="disabled", width=9)
        self.btn_save.pack(side="right", padx=(6, 0))
        self.btn_discard = tk.Button(top, text="되돌리기", command=self.discard,
                                     state="disabled", width=9)
        self.btn_discard.pack(side="right")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=PAD)
        tk.Label(bar, text="레이어", bg=BG, fg=DIM).pack(side="left")
        self.layer_box = ttk.Combobox(bar, state="readonly", width=18)
        self.layer_box.pack(side="left", padx=(6, 14))
        self.layer_box.bind("<<ComboboxSelected>>", self._on_layer)

        self.btn_probe = tk.Button(bar, text="키 눌러서 선택",
                                   command=self.start_probe, state="disabled")
        self.btn_probe.pack(side="left")

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, height=330)
        self.canvas.pack(fill="both", expand=True, padx=PAD, pady=10)
        self.canvas.bind("<Button-1>", self._on_click)

        self.hint = tk.Label(self, text="", bg=BG, fg=DIM, anchor="w",
                             justify="left", font=("Segoe UI", 10))
        self.hint.pack(fill="x", padx=PAD, pady=(0, PAD))

    def _set_status(self, text, color=FG):
        self.status.config(text=text, fg=color)

    def _set_hint(self, text, color=DIM):
        self.hint.config(text=text, fg=color)

    # -- connection ---------------------------------------------------------

    def connect(self):
        ports = rpc.find_ports()
        if not ports:
            self._set_status("시리얼 포트를 찾지 못했습니다.", ERR_C)
            self._set_hint("왼쪽 반쪽을 USB로 연결하세요. "
                           "Studio 지원 펌웨어가 올라가 있어야 합니다.")
            return
        port = ports[0].device
        try:
            self.conn = rpc.Connection(port)
            info = self.conn.device_info()
        except Exception as exc:
            self._set_status("연결 실패: %s" % exc, ERR_C)
            return

        locked = self.conn.lock_state() != 1
        self._set_status("%s @ %s - %s" % (info.name, port,
                                           "잠김" if locked else "편집 가능"),
                         WARN_C if locked else OK_C)
        if locked:
            self._set_hint("키보드에서 Studio Unlock 키를 누르세요 "
                           "(Lower + 좌하단 코너키). 누르면 자동으로 인식합니다.")
            self.after(700, self._poll_lock)
            return
        self.refresh()

    def _poll_lock(self):
        try:
            if self.conn.lock_state() == 1:
                self._set_status("편집 가능", OK_C)
                self.refresh()
                return
        except Exception:
            pass
        self.after(700, self._poll_lock)

    def refresh(self):
        self.keymap = self.conn.get_keymap()
        pls = self.conn.get_physical_layouts()
        self.layout = pls.layouts[pls.active_layout_index]
        names = [L.name or ("Layer %d" % i)
                 for i, L in enumerate(self.keymap.layers)]
        self.layer_box["values"] = names
        if self.layer_index >= len(names):
            self.layer_index = 0
        self.layer_box.current(self.layer_index)
        self.btn_probe.config(state="normal")
        self._update_dirty()
        self.draw()
        if self.mode == "idle":
            self._set_hint("바꿀 키를 캔버스에서 클릭하거나, "
                           "[키 눌러서 선택]을 누르고 키보드에서 그 키를 누르세요.")

    def _update_dirty(self):
        try:
            dirty = self.conn.has_unsaved_changes()
        except Exception:
            dirty = False
        state = "normal" if dirty else "disabled"
        self.btn_save.config(state=state)
        self.btn_discard.config(state=state)
        return dirty

    # -- drawing ------------------------------------------------------------

    def draw(self):
        self.canvas.delete("all")
        self._key_items.clear()
        if not self.layout:
            return
        layer = self.keymap.layers[self.layer_index]
        for pos, k in enumerate(self.layout.keys):
            x0 = k.x * UNIT + 10
            y0 = k.y * UNIT + 10
            x1 = x0 + k.width * UNIT - 3
            y1 = y0 + k.height * UNIT - 3
            fill = KEY_BG
            if pos == self.selected:
                fill = KEY_TARGET if self.mode == "capture" else KEY_SEL
            rect = self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill,
                                                outline="#0f1113", width=1)
            label = (self._binding_label(layer.bindings[pos])
                     if pos < len(layer.bindings) else "?")
            text = self.canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                                           text=label, fill=FG,
                                           font=("Segoe UI", 8),
                                           width=k.width * UNIT - 6)
            self._key_items[rect] = pos
            self._key_items[text] = pos

    def _binding_label(self, b):
        if b.behavior_id in (BEHAVIOR_KEY_PRESS, 5, 10):
            return kc.key_label(b.param1)
        if b.behavior_id in LAYER_LABELS:
            return LAYER_LABELS[b.behavior_id] % b.param1
        return PLAIN_LABELS.get(b.behavior_id, "b%d" % b.behavior_id)

    # -- interaction --------------------------------------------------------

    def _on_layer(self, _evt=None):
        self.layer_index = self.layer_box.current()
        self.draw()

    def _on_click(self, evt):
        if self.mode == "probe":
            return
        item = self.canvas.find_closest(evt.x, evt.y)
        if not item:
            return
        pos = self._key_items.get(item[0])
        if pos is None:
            return
        self.selected = pos
        self.mode = "capture"
        self.draw()
        self._set_hint("이제 이 자리에 넣을 키를 누르세요.   (Esc = 취소)", WARN_C)

    def start_probe(self):
        if self._update_dirty():
            messagebox.showwarning(
                "저장 안 된 변경사항",
                "키보드에 저장되지 않은 변경사항이 있습니다.\n\n"
                "키 탐색은 키맵을 임시로 덮어썼다가 되돌리는 방식이라, "
                "저장 안 된 편집이 함께 사라집니다.\n\n"
                "먼저 [저장] 또는 [되돌리기]를 눌러주세요.")
            return
        try:
            base = self.keymap.layers[0]
            self._probe_map = {}
            for pos in range(len(self.layout.keys)):
                usage = kc.PROBE_USAGES[pos]
                self._probe_map[usage] = pos
                self.conn.set_binding(base.id, pos, BEHAVIOR_KEY_PRESS,
                                      kc.encode(usage))
        except Exception as exc:
            try:
                self.conn.discard_changes()
            except Exception:
                pass
            messagebox.showerror("탐색 실패", str(exc))
            return
        self.mode = "probe"
        self.focus_force()
        self._set_hint("키보드에서 바꾸고 싶은 키를 누르세요.   (Esc = 취소)\n"
                       "이 창의 포커스를 유지하세요 - 지금 키맵은 임시 상태입니다.",
                       WARN_C)

    def _end_probe(self):
        try:
            self.conn.discard_changes()
        except Exception as exc:
            messagebox.showerror(
                "복구 실패",
                "임시 키맵을 되돌리지 못했습니다: %s\n\n"
                "저장되지 않은 상태라, 키보드 전원을 껐다 켜면 복구됩니다." % exc)
        self.mode = "idle"
        self.refresh()

    def _on_key(self, evt):
        if self.mode == "idle":
            return None
        if evt.keycode == 0x1B:   # Esc
            if self.mode == "probe":
                self._end_probe()
                self._set_hint("탐색을 취소하고 키맵을 되돌렸습니다.")
            else:
                self.mode = "idle"
                self.selected = None
                self.draw()
                self._set_hint("취소했습니다.")
            return "break"

        if self.mode == "probe":
            usage = kc.VK_TO_USAGE.get(evt.keycode)
            pos = self._probe_map.get(usage)
            if pos is None:
                return "break"
            self._end_probe()
            self.selected = pos
            self.mode = "capture"
            self.draw()
            self._set_hint("%d번 자리를 선택했습니다. 이제 넣을 키를 누르세요."
                           "   (Esc = 취소)" % pos, WARN_C)
            return "break"

        if self.mode == "capture":
            usage = kc.VK_TO_USAGE.get(evt.keycode)
            if usage is None:
                self._set_hint("모르는 키입니다 (VK 0x%02X). 다른 키를 눌러보세요."
                               % evt.keycode, ERR_C)
                return "break"
            mods = 0
            if not (0xE0 <= usage <= 0xE7):
                if evt.state & 0x0001:
                    mods |= kc.MOD_LSFT
                if evt.state & 0x0004:
                    mods |= kc.MOD_LCTL
                if evt.state & 0x20000:
                    mods |= kc.MOD_LALT
            self._apply(kc.encode(usage, mods=mods))
            return "break"
        return None

    def _apply(self, param1):
        layer = self.keymap.layers[self.layer_index]
        pos = self.selected
        try:
            resp = self.conn.set_binding(layer.id, pos,
                                         BEHAVIOR_KEY_PRESS, param1)
        except Exception as exc:
            messagebox.showerror("쓰기 실패", str(exc))
            return
        if resp != 0:
            messagebox.showerror("쓰기 거부됨",
                                 "키보드가 응답 코드 %d 를 반환했습니다." % resp)
            return
        self.mode = "idle"
        self.selected = None
        self.refresh()
        self._set_hint("%s 레이어 %d번 자리를 %s 로 바꿨습니다. "
                       "[저장]을 눌러야 키보드에 기록됩니다."
                       % (layer.name, pos, kc.key_label(param1)), OK_C)

    # -- persistence --------------------------------------------------------

    def save(self):
        try:
            res = self.conn.save_changes()
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc))
            return
        if res.WhichOneof("result") == "err":
            messagebox.showerror("저장 실패", "오류 코드 %d" % res.err)
            return
        self.refresh()
        self._set_hint("키보드에 저장했습니다.", OK_C)

    def discard(self):
        try:
            self.conn.discard_changes()
        except Exception as exc:
            messagebox.showerror("되돌리기 실패", str(exc))
            return
        self.refresh()
        self._set_hint("마지막 저장 시점으로 되돌렸습니다.")

    def _on_close(self):
        # Never leave a probe keymap staged on the keyboard.
        if self.mode == "probe" and self.conn:
            try:
                self.conn.discard_changes()
            except Exception:
                pass
        if self.conn:
            self.conn.close()
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
