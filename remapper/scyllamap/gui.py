"""The remap editor window.

You pick the key by PRESSING it on the keyboard, and you pick what goes there by
PRESSING that too. No dropdown hunting.

Identifying which physical key was pressed is the hard part - ZMK's Studio RPC
has no key-event notification, so the keyboard never tells us "position 34 was
pressed". We work around it: temporarily paint all positions with distinct probe
keycodes, read which one arrives, then discard. Because Studio stages edits until
an explicit save, the probe never touches saved settings.

All keyboard I/O goes through a Worker. Every RPC blocks until the keyboard
replies, so doing it inline would freeze the window.

Over USB the serial port is exclusive, so the window connects only while it is
visible and releases the port when hidden - otherwise ZMK Studio could never
reach the keyboard. BLE has no such conflict.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rpc             # noqa: E402
import keycodes as kc  # noqa: E402
import labels          # noqa: E402
import worker          # noqa: E402
import picker          # noqa: E402
import firmware        # noqa: E402

BEHAVIOR_KEY_PRESS = 2
FIRMWARE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "firmware")
FIRMWARE_DIR = os.path.normpath(FIRMWARE_DIR)
UNIT = 0.46          # px per physical-layout unit (100 units == 1u key)
PAD = 14

BG = "#1b1d21"
KEY_BG = "#2a2e34"
KEY_SEL = "#3d6fd6"
KEY_TARGET = "#c8781e"
KEY_HOVER = "#343a42"
FG = "#e6e6e6"
DIM = "#8b9199"
OK_C = "#7fd67f"
WARN_C = "#e0a76c"
ERR_C = "#e06c6c"


def read_batteries(conn):
    """-> [(label, percent)] with the halves named, or [] if unavailable.

    Only BLE carries this: the split central proxies its peripherals' levels as
    extra Battery Service instances, and the Studio RPC has no battery request
    at all, so a USB connection cannot see it.
    """
    read = getattr(conn.transport, "read_batteries", None)
    if read is None:
        return []
    out = []
    for raw_label, percent in read():
        if percent is None:
            continue
        if raw_label and "peripheral" in raw_label.lower():
            name = "오른쪽"
        elif raw_label:
            name = raw_label
        else:
            name = "왼쪽"
        out.append((name, percent))
    return out


class Snapshot:
    """Everything one refresh needs, fetched on the worker in one go."""

    def __init__(self, conn, catalog=None):
        self.catalog = catalog or labels.Catalog(conn)
        self.keymap = conn.get_keymap()
        pls = conn.get_physical_layouts()
        self.layout = pls.layouts[pls.active_layout_index]
        self.dirty = conn.has_unsaved_changes()
        self.batteries = read_batteries(conn)


class EditorWindow(tk.Tk):
    def __init__(self, on_hide=None):
        super().__init__()
        self.title("Scylla Remapper")
        self.configure(bg=BG)
        self.geometry("760x560")
        self._on_hide = on_hide

        self.worker = worker.Worker(self)
        self.conn = None
        self.catalog = None
        self.keymap = None
        self.layout = None
        self.layer_index = 0
        self.selected = None
        self.hovered = None
        self.mode = "idle"        # idle | probe | capture
        self.busy = False
        self._probe_map = {}
        self._key_items = {}
        self._rect_of = {}
        self._retry_job = None
        self._want_ble = False

        self._build_ui()
        self.bind_all("<KeyPress>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self.hide)

    # -- ui -----------------------------------------------------------------

    def _build_ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=PAD, pady=(PAD, 6))

        self.status = tk.Label(top, text="", bg=BG, fg=FG, anchor="w",
                               font=("Segoe UI", 10))
        self.status.pack(side="left")

        self.battery = tk.Label(top, text="", bg=BG, fg=DIM, anchor="w",
                                font=("Segoe UI", 10))
        self.battery.pack(side="left", padx=(14, 0))

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

        self.btn_pick = tk.Button(bar, text="다른 기능…", command=self.pick_behavior,
                                  state="disabled")
        self.btn_pick.pack(side="left", padx=(6, 0))

        self.btn_update = tk.Button(bar, text="펌웨어 업데이트",
                                    command=self.update_firmware)
        self.btn_update.pack(side="left", padx=(14, 0))

        self.btn_ble = tk.Button(bar, text="블루투스로 연결",
                                 command=self.connect_ble)
        self.btn_ble.pack(side="right")
        self.btn_usb = tk.Button(bar, text="USB로 연결", command=self.connect_usb)
        self.btn_usb.pack(side="right", padx=(0, 6))

        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, height=330)
        self.canvas.pack(fill="both", expand=True, padx=PAD, pady=10)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))

        self.detail = tk.Label(self, text="", bg=BG, fg=FG, anchor="w",
                               font=("Segoe UI", 10, "bold"))
        self.detail.pack(fill="x", padx=PAD)

        self.hint = tk.Label(self, text="", bg=BG, fg=DIM, anchor="w",
                             justify="left", font=("Segoe UI", 10))
        self.hint.pack(fill="x", padx=PAD, pady=(2, PAD))

    def _set_status(self, text, color=FG):
        self.status.config(text=text, fg=color)

    def _set_hint(self, text, color=DIM):
        self.hint.config(text=text, fg=color)

    def _set_busy(self, on, note=None):
        self.busy = on
        state = "disabled" if on else "normal"
        for b in (self.btn_probe, self.btn_pick, self.btn_usb,
                  self.btn_ble, self.btn_update):
            b.config(state=state)
        if on and note:
            self._set_status(note, WARN_C)

    # -- show / hide --------------------------------------------------------

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        if self.conn is None and not self.busy:
            self.autoconnect()

    def autoconnect(self):
        """USB if the cable is in, otherwise BLE. USB is faster and always
        reachable; BLE needs the keyboard to be on this machine's profile."""
        if rpc.find_ports():
            self.connect_usb()
        else:
            self.connect_ble()

    def hide(self):
        if self.mode == "probe":
            self._end_probe()
        self.withdraw()
        self.disconnect()
        if self._on_hide:
            self._on_hide()

    def disconnect(self):
        if self._retry_job is not None:
            self.after_cancel(self._retry_job)
            self._retry_job = None
        conn, self.conn = self.conn, None
        self.catalog = None
        self.keymap = None
        if conn:
            self.worker.submit(conn.close)
        self.canvas.delete("all")
        self._key_items.clear()

    # -- connecting ---------------------------------------------------------

    def connect_usb(self):
        self._want_ble = False
        self.disconnect()
        ports = rpc.find_ports()
        if not ports:
            self._set_status("키보드를 찾는 중…", WARN_C)
            self._set_hint("왼쪽 반쪽을 USB로 연결하세요. 연결되면 자동으로 잡습니다.\n"
                           "무선으로 쓰시려면 [블루투스로 연결]을 누르세요.")
            self._retry_soon()
            return
        self._open(lambda: rpc.Connection.open_serial(ports[0].device))

    def connect_ble(self):
        self._want_ble = True
        self.disconnect()
        self._set_busy(True, "블루투스 장치를 찾는 중… (최대 8초)")
        self._set_hint("키보드가 이 PC에 페어링되어 있어야 합니다.")
        self.worker.submit(lambda: rpc.find_ble_devices(timeout=8.0),
                           self._ble_found, self._fail)

    def _ble_found(self, devices):
        self._set_busy(False)
        if not devices:
            self._set_status("블루투스로 키보드를 찾지 못했습니다.", ERR_C)
            self._set_hint(
                "이 PC에 키보드가 페어링되어 있는지 확인하세요. 그리고 키보드에서 "
                "해당 BT 프로파일을 선택한 상태여야 합니다.\n"
                "이미 연결되어 광고를 멈춘 상태라면 검색에 안 잡힐 수 있습니다.")
            return
        addr, name = self._best_ble(devices)
        self._open(lambda: rpc.Connection.open_ble(addr, name))

    def _best_ble(self, devices):
        """Windows lists every paired BLE device, mice and headsets included.
        Prefer one whose name looks like this keyboard."""
        for addr, name in devices:
            low = (name or "").lower()
            if "scylla" in low or "zmk" in low:
                return addr, name
        return devices[0]

    def _open(self, factory):
        self._set_busy(True, "연결 중…")

        def job():
            conn = factory()
            info = conn.device_info()
            locked = conn.lock_state() != 1
            snap = None if locked else Snapshot(conn)
            return conn, info, locked, snap

        self.worker.submit(job, self._opened, self._fail)

    def _opened(self, result):
        conn, info, locked, snap = result
        self._set_busy(False)
        self.conn = conn
        self._set_status("%s @ %s (%s) - %s"
                         % (info.name, conn.describe(), conn.kind,
                            "잠김" if locked else "편집 가능"),
                         WARN_C if locked else OK_C)
        if locked:
            self._set_hint("키보드에서 Studio Unlock 키를 누르세요 "
                           "(Lower + 좌하단 코너키). 누르면 자동으로 인식합니다.")
            self.after(700, self._poll_lock)
            return
        self._adopt(snap)

    def _fail(self, exc):
        self._set_busy(False)
        self.conn = None
        self._set_status("연결 실패: %s" % exc, ERR_C)
        if self._want_ble:
            self._set_hint("Windows가 HID로 페어링된 장치의 GATT 접근을 막는 "
                           "경우가 있습니다. USB로도 시도해보세요.")
        else:
            self._set_hint("ZMK Studio가 켜져 있으면 닫아주세요. "
                           "포트는 한 프로그램만 쓸 수 있습니다.\n계속 재시도합니다.")
            self._retry_soon()

    def _retry_soon(self):
        if self._retry_job is not None:
            self.after_cancel(self._retry_job)
        self._retry_job = self.after(1500, self._retry)

    def _retry(self):
        self._retry_job = None
        if self.conn is not None or self.busy or self._want_ble:
            return
        if self.state() == "withdrawn":
            return
        self.connect_usb()

    def _poll_lock(self):
        if self.conn is None or self.busy:
            return
        self.worker.submit(
            lambda: self.conn.lock_state() == 1,
            lambda unlocked: self._adopt_if_unlocked(unlocked),
            lambda _e: None)

    def _adopt_if_unlocked(self, unlocked):
        if not unlocked:
            self.after(700, self._poll_lock)
            return
        self._set_status("편집 가능", OK_C)
        self.refresh()

    # -- refresh ------------------------------------------------------------

    def refresh(self):
        if self.conn is None:
            return
        self.worker.submit(lambda: Snapshot(self.conn, self.catalog),
                           self._adopt, self._fail)

    def _adopt(self, snap):
        if snap is None:
            return
        self.catalog = snap.catalog
        self.keymap = snap.keymap
        self.layout = snap.layout
        names = [L.name or ("Layer %d" % i)
                 for i, L in enumerate(self.keymap.layers)]
        self.layer_box["values"] = names
        if self.layer_index >= len(names):
            self.layer_index = 0
        self.layer_box.current(self.layer_index)
        for b in (self.btn_probe, self.btn_pick):
            b.config(state="disabled" if self.busy else "normal")
        state = "normal" if snap.dirty else "disabled"
        self.btn_save.config(state=state)
        self.btn_discard.config(state=state)
        self._dirty = snap.dirty
        self._show_batteries(snap.batteries)
        self.draw()
        if self.mode == "idle":
            self._set_hint("바꿀 키를 캔버스에서 클릭하거나, "
                           "[키 눌러서 선택]을 누르고 키보드에서 그 키를 누르세요.")

    def _link_text(self):
        """What this machine can actually observe about the link.

        Which endpoint the keyboard is *sending to* is not knowable from here -
        the RPC has no endpoint request, and its subsystems are fixed in ZMK
        itself, so a module cannot add one. When both links are up, the status
        report key is the only way to tell. Everything else is visible.
        """
        cable = bool(rpc.find_ports())
        wireless = self.conn is not None and self.conn.kind == "BLE"
        if cable and wireless:
            return "USB·BLE 둘 다 연결됨 (출력 쪽은 상태 키로 확인)", WARN_C
        if cable:
            return "USB 케이블 연결됨", OK_C
        if wireless:
            return "블루투스로 연결됨", OK_C
        return "", DIM

    def _show_batteries(self, batteries):
        if not batteries:
            link, colour = self._link_text()
            if self.conn is not None and self.conn.kind == "USB":
                link = (link + "   ·   " if link else "") +                        "배터리는 블루투스로 연결해야 보입니다"
            self.battery.config(text=link, fg=colour)
            return
        order = {"왼쪽": 0, "오른쪽": 1}
        items = sorted(batteries, key=lambda b: order.get(b[0], 9))
        worst = min(p for _n, p in items)
        colour = ERR_C if worst <= 15 else (WARN_C if worst <= 30 else DIM)
        link, _lc = self._link_text()
        text = "  ".join("%s %d%%" % (n, p) for n, p in items)
        self.battery.config(text=(text + "   ·   " + link) if link else text,
                            fg=colour)

    # -- drawing ------------------------------------------------------------

    def draw(self):
        self.canvas.delete("all")
        self._key_items.clear()
        self._rect_of.clear()
        if not self.layout or not self.keymap:
            return
        layer = self.keymap.layers[self.layer_index]
        for pos, k in enumerate(self.layout.keys):
            x0 = k.x * UNIT + 10
            y0 = k.y * UNIT + 10
            x1 = x0 + k.width * UNIT - 3
            y1 = y0 + k.height * UNIT - 3
            rect = self.canvas.create_rectangle(x0, y0, x1, y1,
                                                fill=self._fill_for(pos),
                                                outline="#0f1113", width=1)
            text = self.canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                                           text=self._label(layer, pos),
                                           fill=FG, font=("Segoe UI", 8),
                                           width=k.width * UNIT - 6)
            self._key_items[rect] = pos
            self._key_items[text] = pos
            self._rect_of[pos] = rect

    def _fill_for(self, pos):
        if pos == self.selected:
            return KEY_TARGET if self.mode == "capture" else KEY_SEL
        if pos == self.hovered:
            return KEY_HOVER
        return KEY_BG

    def _label(self, layer, pos):
        if pos >= len(layer.bindings):
            return "?"
        return self.catalog.label(layer.bindings[pos], self.keymap.layers)

    def _set_hover(self, pos):
        if pos == self.hovered:
            return
        prev, self.hovered = self.hovered, pos
        for p in (prev, pos):
            if p is not None and p in self._rect_of:
                self.canvas.itemconfig(self._rect_of[p], fill=self._fill_for(p))
        if pos is None or not self.keymap:
            self.detail.config(text="")
            return
        layer = self.keymap.layers[self.layer_index]
        if pos < len(layer.bindings):
            self.detail.config(
                text="%d번  %s" % (pos, self.catalog.describe(
                    layer.bindings[pos], self.keymap.layers)))

    def _on_motion(self, evt):
        item = self.canvas.find_withtag("current")
        self._set_hover(self._key_items.get(item[0]) if item else None)

    # -- interaction --------------------------------------------------------

    def _on_layer(self, _evt=None):
        self.layer_index = self.layer_box.current()
        self.draw()

    def _on_click(self, evt):
        if self.mode == "probe" or not self.keymap or self.busy:
            return
        item = self.canvas.find_closest(evt.x, evt.y)
        if not item:
            return
        pos = self._key_items.get(item[0])
        if pos is None:
            return
        self._begin_capture(pos)

    def _begin_capture(self, pos):
        self.selected = pos
        self.mode = "capture"
        self.draw()
        self._set_hint("이제 이 자리에 넣을 키를 누르세요.   (Esc = 취소)", WARN_C)

    def start_probe(self):
        if self.conn is None or self.busy:
            return
        if getattr(self, "_dirty", False):
            messagebox.showwarning(
                "저장 안 된 변경사항",
                "키보드에 저장되지 않은 변경사항이 있습니다.\n\n"
                "키 탐색은 키맵을 임시로 덮어썼다가 되돌리는 방식이라, "
                "저장 안 된 편집이 함께 사라집니다.\n\n"
                "먼저 [저장] 또는 [되돌리기]를 눌러주세요.")
            return

        base_id = self.keymap.layers[0].id
        count = len(self.layout.keys)
        self._probe_map = {kc.PROBE_USAGES[p]: p for p in range(count)}
        self._set_busy(True, "탐색 준비 중… (%d개 키)" % count)

        def job():
            for pos in range(count):
                self.conn.set_binding(base_id, pos, BEHAVIOR_KEY_PRESS,
                                      kc.encode(kc.PROBE_USAGES[pos]))
            return True

        self.worker.submit(job, self._probe_ready, self._probe_failed)

    def _probe_ready(self, _ok):
        self._set_busy(False)
        self.mode = "probe"
        self.focus_force()
        self._set_status("탐색 중", WARN_C)
        self._set_hint("키보드에서 바꾸고 싶은 키를 누르세요.   (Esc = 취소)\n"
                       "이 창의 포커스를 유지하세요 - 지금 키맵은 임시 상태입니다.",
                       WARN_C)

    def _probe_failed(self, exc):
        self._set_busy(False)
        self.mode = "idle"
        self.worker.submit(self.conn.discard_changes, lambda _r: self.refresh(),
                           lambda _e: None)
        messagebox.showerror("탐색 실패", str(exc))

    def _end_probe(self, then=None):
        self.mode = "idle"
        self._set_busy(True, "키맵 복구 중…")

        def done(_r):
            self._set_busy(False)
            self.refresh()
            if then:
                then()

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror(
                "복구 실패",
                "임시 키맵을 되돌리지 못했습니다: %s\n\n"
                "저장되지 않은 상태라, 키보드 전원을 껐다 켜면 복구됩니다." % exc)

        self.worker.submit(self.conn.discard_changes, done, failed)

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
            self._end_probe(then=lambda: self._picked(pos))
            return "break"

        if self.mode == "capture":
            if self.busy:
                return "break"
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

    def _picked(self, pos):
        self._begin_capture(pos)
        self._set_hint("%d번 자리를 선택했습니다. 이제 넣을 키를 누르세요."
                       "   (Esc = 취소)" % pos, WARN_C)

    def _apply(self, param1):
        layer = self.keymap.layers[self.layer_index]
        pos = self.selected
        self.mode = "idle"
        self.selected = None
        self._set_busy(True, "쓰는 중…")

        def job():
            return self.conn.set_binding(layer.id, pos,
                                         BEHAVIOR_KEY_PRESS, param1)

        def done(resp):
            self._set_busy(False)
            if resp != 0:
                messagebox.showerror(
                    "쓰기 거부됨",
                    "키보드가 응답 코드 %d 를 반환했습니다." % resp)
                return
            self.refresh()
            self._set_hint("%s 레이어 %d번 자리를 %s 로 바꿨습니다. "
                           "[저장]을 눌러야 키보드에 기록됩니다."
                           % (layer.name, pos, kc.key_label(param1)), OK_C)

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror("쓰기 실패", str(exc))

        self.worker.submit(job, done, failed)

    # -- assigning a non-keypress behavior -----------------------------------

    def pick_behavior(self):
        """Assign something you cannot express by pressing a key.

        Output toggle, BLE profile select, layer moves - none of these can be
        demonstrated on the keyboard, so they need a list.
        """
        if self.conn is None or self.busy or not self.keymap:
            return
        if self.selected is None:
            self._set_hint("먼저 바꿀 키를 고르세요. "
                           "캔버스에서 클릭하거나 [키 눌러서 선택]을 쓰세요.", WARN_C)
            return
        choice = picker.ask(self, self.catalog, list(self.keymap.layers))
        if choice is None:
            return
        behavior_id, param1, param2 = choice
        self._apply_binding(behavior_id, param1, param2)

    def _apply_binding(self, behavior_id, param1, param2):
        layer = self.keymap.layers[self.layer_index]
        pos = self.selected
        self.mode = "idle"
        self.selected = None
        self._set_busy(True, "쓰는 중…")

        def job():
            return self.conn.set_binding(layer.id, pos, behavior_id, param1, param2)

        def done(resp):
            self._set_busy(False)
            if resp != 0:
                reason = {1: "위치가 잘못됨", 2: "그 기능을 쓸 수 없음",
                          3: "파라미터가 맞지 않음"}.get(resp, "코드 %d" % resp)
                messagebox.showerror("쓰기 거부됨", "키보드가 거부했습니다: %s" % reason)
                return
            self.refresh()
            self._set_hint("%s 레이어 %d번 자리를 %s 로 바꿨습니다. "
                           "[저장]을 눌러야 키보드에 기록됩니다."
                           % (layer.name, pos, self.catalog.name(behavior_id)), OK_C)

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror("쓰기 실패", str(exc))

        self.worker.submit(job, done, failed)

    # -- firmware ------------------------------------------------------------

    def update_firmware(self):
        """Fetch the published build, then guide the user through flashing.

        Entering the bootloader cannot be automated - the RPC has no reboot
        request - but the keymap's &bootloader key does it without touching the
        reset button.
        """
        if self.busy:
            return
        self._set_busy(True, "릴리스 확인 중…")
        self.worker.submit(firmware.latest_release, self._release_found,
                           self._firmware_failed)

    def _release_found(self, release):
        self._set_busy(False)
        here = firmware.local_version(FIRMWARE_DIR)
        current = " (내려받은 버전과 같습니다)" if here == release["tag"] else ""
        if not messagebox.askyesno(
                "펌웨어 업데이트",
                "최신 빌드: %s%s\n\n"
                "내려받고 왼쪽 반쪽에 올릴까요?\n"
                "파일을 받은 뒤 부트로더 진입을 안내합니다." % (release["tag"], current)):
            return
        self._set_busy(True, "내려받는 중…")

        def job():
            firmware.sync(FIRMWARE_DIR, release,
                          progress=lambda msg: None)
            return release

        self.worker.submit(job, self._downloaded, self._firmware_failed)

    def _downloaded(self, release):
        self._set_busy(False)
        if not messagebox.askyesno(
                "부트로더 진입",
                "%s 를 내려받았습니다.\n\n"
                "이제 키보드에서 부트로더 키를 누르세요:\n"
                "  Lower + 오른쪽 아래 맨 끝 키\n\n"
                "(리셋 버튼 두 번 누르기도 동일합니다.)\n\n"
                "[예]를 누르면 드라이브가 나타날 때까지 기다립니다."
                % release["tag"]):
            return
        self._set_busy(True, "부트로더 드라이브를 기다리는 중… (최대 90초)")
        self.worker.submit(lambda: firmware.wait_for_bootloader(90.0),
                           self._bootloader_ready, self._firmware_failed)

    def _bootloader_ready(self, drive):
        if drive is None:
            self._set_busy(False)
            self._set_status("부트로더 드라이브를 찾지 못했습니다.", ERR_C)
            self._set_hint("부트로더 키가 안 먹으면 리셋 버튼을 빠르게 두 번 누르세요.")
            return
        self._set_busy(True, "%s 에 쓰는 중…" % drive)

        def done(name):
            self._set_busy(False)
            self._set_status("플래싱 완료: %s" % name, OK_C)
            self._set_hint("키보드가 재부팅됩니다. 다시 연결되면 [USB로 연결] 또는 "
                           "[블루투스로 연결]을 누르세요.")
            self.disconnect()

        self.worker.submit(lambda: firmware.flash(FIRMWARE_DIR, "left", drive),
                           done, self._firmware_failed)

    def _firmware_failed(self, exc):
        self._set_busy(False)
        self._set_status("펌웨어 작업 실패: %s" % exc, ERR_C)

    # -- persistence --------------------------------------------------------

    def save(self):
        if self.conn is None:
            return
        self._set_busy(True, "저장 중…")

        def done(res):
            self._set_busy(False)
            if res.WhichOneof("result") == "err":
                messagebox.showerror("저장 실패", "오류 코드 %d" % res.err)
                return
            self.refresh()
            self._set_hint("키보드에 저장했습니다.", OK_C)

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror("저장 실패", str(exc))

        self.worker.submit(self.conn.save_changes, done, failed)

    def discard(self):
        if self.conn is None:
            return
        self._set_busy(True, "되돌리는 중…")

        def done(_r):
            self._set_busy(False)
            self.refresh()
            self._set_hint("마지막 저장 시점으로 되돌렸습니다.")

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror("되돌리기 실패", str(exc))

        self.worker.submit(self.conn.discard_changes, done, failed)

    def shutdown(self):
        conn = self.conn
        if conn and self.mode == "probe":
            try:
                conn.discard_changes()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        self.conn = None
        self.worker.stop()
        self.destroy()


def main():
    win = EditorWindow()
    win.protocol("WM_DELETE_WINDOW", win.shutdown)
    win.show()
    win.mainloop()


if __name__ == "__main__":
    main()
