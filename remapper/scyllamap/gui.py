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
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rpc             # noqa: E402
import keycodes as kc  # noqa: E402
import labels          # noqa: E402
import worker          # noqa: E402
import picker          # noqa: E402
import firmware        # noqa: E402
import ui              # noqa: E402

BEHAVIOR_KEY_PRESS = 2
FIRMWARE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "firmware"))

PAD = 18
KEY_GAP = 4
MIN_UNIT = 0.34
MAX_UNIT = 0.80


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
        self.configure(bg=ui.BG)
        self.geometry("900x640")
        self.minsize(760, 560)
        self._on_hide = on_hide
        try:
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "icon.ico")
            if os.path.exists(icon):
                self.iconbitmap(icon)
        except Exception:
            pass

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
        self._dirty = False
        self._probe_map = {}
        self._key_items = {}
        self._rect_of = {}
        self._retry_job = None
        self._want_ble = False
        self._unit = 0.46

        self._build_ui()
        ui.dark_titlebar(self)
        self.bind_all("<KeyPress>", self._on_key)
        self.bind("<Configure>", self._on_resize)
        self.protocol("WM_DELETE_WINDOW", self.hide)

    # -- layout -------------------------------------------------------------

    def _build_ui(self):
        header = tk.Frame(self, bg=ui.BG)
        header.pack(fill="x", padx=PAD, pady=(PAD, 0))

        left = tk.Frame(header, bg=ui.BG)
        left.pack(side="left", fill="y")

        self.device_name = tk.Label(left, text="Scylla", bg=ui.BG, fg=ui.TEXT,
                                    font=ui.F_TITLE, anchor="w")
        self.device_name.pack(side="top", anchor="w")

        chips = tk.Frame(left, bg=ui.BG)
        chips.pack(side="top", anchor="w", pady=(6, 0))
        self.chip_link = ui.Chip(chips, bg=ui.BG)
        self.chip_link.pack(side="left")
        self.chip_lock = ui.Chip(chips, bg=ui.BG)
        self.chip_lock.pack(side="left", padx=(6, 0))

        right = tk.Frame(header, bg=ui.BG)
        right.pack(side="right", fill="y")

        gauges = tk.Frame(right, bg=ui.BG)
        gauges.pack(side="top", anchor="e")
        self.gauge_left = ui.BatteryGauge(gauges, "왼쪽", bg=ui.BG)
        self.gauge_left.pack(side="left", padx=(0, 16))
        self.gauge_right = ui.BatteryGauge(gauges, "오른쪽", bg=ui.BG)
        self.gauge_right.pack(side="left")

        actions = tk.Frame(right, bg=ui.BG)
        actions.pack(side="top", anchor="e", pady=(8, 0))
        self.btn_save = ui.Button(actions, "저장", self.save, width=76,
                                  primary=True, bg=ui.BG)
        self.btn_save.pack(side="right")
        self.btn_discard = ui.Button(actions, "되돌리기", self.discard, width=76,
                                     bg=ui.BG)
        self.btn_discard.pack(side="right", padx=(0, 6))

        ui.separator(self).pack(fill="x", padx=PAD, pady=(14, 0))

        toolbar = tk.Frame(self, bg=ui.BG)
        toolbar.pack(fill="x", padx=PAD, pady=12)

        self.tabs = ui.SegmentedTabs(toolbar, self._on_layer, bg=ui.BG)
        self.tabs.pack(side="left")

        self.btn_update = ui.Button(toolbar, "펌웨어 업데이트",
                                    self.update_firmware, bg=ui.BG)
        self.btn_update.pack(side="right")
        self.btn_ble = ui.Button(toolbar, "블루투스", self.connect_ble, bg=ui.BG)
        self.btn_ble.pack(side="right", padx=(0, 6))
        self.btn_usb = ui.Button(toolbar, "USB", self.connect_usb, bg=ui.BG)
        self.btn_usb.pack(side="right", padx=(0, 6))

        board = tk.Frame(self, bg=ui.SURFACE, highlightthickness=1,
                         highlightbackground=ui.BORDER)
        board.pack(fill="both", expand=True, padx=PAD)

        self.canvas = tk.Canvas(board, bg=ui.SURFACE, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda _e: self._set_hover(None))

        footer = tk.Frame(self, bg=ui.BG)
        footer.pack(fill="x", padx=PAD, pady=(12, PAD))

        buttons = tk.Frame(footer, bg=ui.BG)
        buttons.pack(side="left")
        self.btn_probe = ui.Button(buttons, "키 눌러서 선택", self.start_probe,
                                   primary=True, bg=ui.BG)
        self.btn_probe.pack(side="left")
        self.btn_pick = ui.Button(buttons, "다른 기능…", self.pick_behavior,
                                  bg=ui.BG)
        self.btn_pick.pack(side="left", padx=(6, 0))

        texts = tk.Frame(footer, bg=ui.BG)
        texts.pack(side="left", fill="x", expand=True, padx=(16, 0))
        self.detail = tk.Label(texts, text="", bg=ui.BG, fg=ui.TEXT,
                               font=ui.F_LABEL, anchor="w")
        self.detail.pack(fill="x")
        self.hint = tk.Label(texts, text="", bg=ui.BG, fg=ui.TEXT_DIM,
                             anchor="w", justify="left", font=ui.F_SMALL)
        self.hint.pack(fill="x")

        self._set_buttons(False)

    # -- small helpers ------------------------------------------------------

    def _set_hint(self, text, colour=ui.TEXT_DIM):
        self.hint.config(text=text, fg=colour)

    def _set_detail(self, text, colour=ui.TEXT):
        self.detail.config(text=text, fg=colour)

    def _set_buttons(self, editable):
        for b in (self.btn_probe, self.btn_pick):
            b.config_state(bool(editable) and not self.busy)
        for b in (self.btn_usb, self.btn_ble, self.btn_update):
            b.config_state(not self.busy)
        for b in (self.btn_save, self.btn_discard):
            b.config_state(self._dirty and not self.busy)

    def _set_busy(self, on, note=None):
        self.busy = on
        self._set_buttons(self.keymap is not None)
        if note:
            self._set_detail(note, ui.WARN if on else ui.TEXT)

    def _set_link_chip(self):
        cable = bool(rpc.find_ports())
        wireless = self.conn is not None and self.conn.kind == "BLE"
        if cable and wireless:
            self.chip_link.set("USB · 블루투스", ui.WARN)
        elif cable:
            self.chip_link.set("USB", ui.OK)
        elif wireless:
            self.chip_link.set("블루투스", ui.OK)
        else:
            self.chip_link.set("연결 없음", ui.TEXT_FAINT)

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
        self._dirty = False
        if conn:
            self.worker.submit(conn.close)
        self.canvas.delete("all")
        self._key_items.clear()
        self.gauge_left.set(None)
        self.gauge_right.set(None)
        self.chip_lock.set("")
        self._set_link_chip()
        self._set_buttons(False)

    # -- connecting ---------------------------------------------------------

    def connect_usb(self):
        self._want_ble = False
        self.disconnect()
        ports = rpc.find_ports()
        if not ports:
            self._set_detail("키보드를 찾는 중…", ui.WARN)
            self._set_hint("왼쪽 반쪽을 USB로 연결하면 자동으로 잡습니다. "
                           "무선으로 쓰시려면 [블루투스]를 누르세요.")
            self._retry_soon()
            return
        self._open(lambda: rpc.Connection.open_serial(ports[0].device))

    def connect_ble(self):
        self._want_ble = True
        self.disconnect()
        self._set_busy(True, "블루투스 장치를 찾는 중…")
        self._set_hint("키보드가 이 PC에 페어링되어 있어야 합니다.")
        self.worker.submit(lambda: rpc.find_ble_devices(timeout=8.0),
                           self._ble_found, self._fail)

    def _ble_found(self, devices):
        self._set_busy(False)
        if not devices:
            self._set_detail("블루투스로 키보드를 찾지 못했습니다", ui.ERR)
            self._set_hint("이 PC에 페어링되어 있는지, 그리고 키보드가 해당 BT "
                           "프로파일을 선택한 상태인지 확인하세요.")
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
        self.conn = conn
        self._set_busy(False)
        self.device_name.config(text=info.name or "Scylla")
        self._set_link_chip()
        if locked:
            self.chip_lock.set("잠김", ui.WARN)
            self._set_detail("편집하려면 잠금을 풀어야 합니다", ui.WARN)
            self._set_hint("키보드에서 Studio Unlock 키를 누르세요 "
                           "(Lower + 좌하단 코너키). 누르면 자동으로 인식합니다.")
            self.after(700, self._poll_lock)
            return
        self.chip_lock.set("편집 가능", ui.OK)
        self._adopt(snap)

    def _fail(self, exc):
        self._set_busy(False)
        self.conn = None
        self._set_detail("연결 실패", ui.ERR)
        if self._want_ble:
            self._set_hint("%s\nWindows가 GATT 접근을 막는 경우가 있습니다. "
                           "USB로도 시도해보세요." % exc)
        else:
            self._set_hint("%s\nZMK Studio가 켜져 있으면 닫아주세요 — "
                           "포트는 한 프로그램만 쓸 수 있습니다. 계속 재시도합니다." % exc)
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
        self.worker.submit(lambda: self.conn.lock_state() == 1,
                           self._adopt_if_unlocked, lambda _e: None)

    def _adopt_if_unlocked(self, unlocked):
        if not unlocked:
            self.after(700, self._poll_lock)
            return
        self.chip_lock.set("편집 가능", ui.OK)
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
        self._dirty = snap.dirty

        names = [L.name or ("Layer %d" % i)
                 for i, L in enumerate(self.keymap.layers)]
        if self.layer_index >= len(names):
            self.layer_index = 0
        self.tabs.set_items(names, self.layer_index)

        levels = dict(snap.batteries)
        self.gauge_left.set(levels.get("왼쪽"))
        self.gauge_right.set(levels.get("오른쪽"))
        self._set_link_chip()
        self._set_buttons(True)
        self.draw()

        if self.mode == "idle":
            self._set_detail("")
            if self.conn is not None and self.conn.kind == "USB" and not levels:
                self._set_hint("바꿀 키를 클릭하거나 [키 눌러서 선택]을 누르세요.   "
                               "배터리는 블루투스로 연결해야 보입니다.")
            else:
                self._set_hint("바꿀 키를 클릭하거나 [키 눌러서 선택]을 누른 뒤 "
                               "키보드에서 그 키를 누르세요.")

    # -- drawing ------------------------------------------------------------

    def _on_resize(self, evt):
        if evt.widget is self and self.layout:
            self.after_idle(self.draw)

    def _compute_unit(self):
        if not self.layout:
            return self._unit
        width = max(k.x + k.width for k in self.layout.keys)
        height = max(k.y + k.height for k in self.layout.keys)
        cw = max(self.canvas.winfo_width() - 24, 200)
        ch = max(self.canvas.winfo_height() - 24, 150)
        return max(MIN_UNIT, min(MAX_UNIT, cw / width, ch / height))

    def draw(self):
        self.canvas.delete("all")
        self._key_items.clear()
        self._rect_of.clear()
        if not self.layout or not self.keymap:
            return

        self._unit = unit = self._compute_unit()
        board_w = max(k.x + k.width for k in self.layout.keys) * unit
        board_h = max(k.y + k.height for k in self.layout.keys) * unit
        ox = (self.canvas.winfo_width() - board_w) / 2
        oy = (self.canvas.winfo_height() - board_h) / 2

        layer = self.keymap.layers[self.layer_index]
        for pos, k in enumerate(self.layout.keys):
            x0 = ox + k.x * unit
            y0 = oy + k.y * unit
            x1 = x0 + k.width * unit - KEY_GAP
            y1 = y0 + k.height * unit - KEY_GAP
            fill, fg = self._colours_for(pos, layer)
            rect = ui.rounded(self.canvas, x0, y0, x1, y1, 5,
                              fill=fill, outline=ui.BORDER, width=1)
            text = self.canvas.create_text(
                (x0 + x1) / 2, (y0 + y1) / 2,
                text=self._label(layer, pos), fill=fg,
                font=ui.key_font(unit), width=k.width * unit - 8)
            self._key_items[rect] = pos
            self._key_items[text] = pos
            self._rect_of[pos] = rect

    def _colours_for(self, pos, layer):
        if pos == self.selected:
            return (ui.TARGET if self.mode == "capture" else ui.ACCENT), "#ffffff"
        if pos == self.hovered:
            return ui.SURFACE_HOVER, ui.TEXT
        if pos < len(layer.bindings):
            name = self.catalog.name(layer.bindings[pos].behavior_id)
            if name == "Transparent":
                return ui.SURFACE, ui.TEXT_FAINT
            if name != "Key Press":
                # Layer moves, bluetooth, output - tint them so the keys that
                # do something other than type stand out at a glance.
                return ui.SURFACE_HI, ui.ACCENT
        return ui.SURFACE_HI, ui.TEXT

    def _label(self, layer, pos):
        if pos >= len(layer.bindings):
            return "?"
        return self.catalog.label(layer.bindings[pos], self.keymap.layers)

    def _set_hover(self, pos):
        if pos == self.hovered:
            return
        prev, self.hovered = self.hovered, pos
        layer = self.keymap.layers[self.layer_index] if self.keymap else None
        for p in (prev, pos):
            if p is not None and p in self._rect_of and layer:
                self.canvas.itemconfig(self._rect_of[p],
                                       fill=self._colours_for(p, layer)[0])
        if pos is None or not layer or self.mode != "idle":
            return
        if pos < len(layer.bindings):
            self._set_detail("%d번  ·  %s" % (
                pos, self.catalog.describe(layer.bindings[pos],
                                           self.keymap.layers)))

    def _on_motion(self, evt):
        item = self.canvas.find_withtag("current")
        self._set_hover(self._key_items.get(item[0]) if item else None)

    # -- interaction --------------------------------------------------------

    def _on_layer(self, index):
        self.layer_index = index
        self.draw()

    def _on_click(self, evt):
        if self.mode == "probe" or not self.keymap or self.busy:
            return
        item = self.canvas.find_withtag("current")
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
        self._set_detail("%d번 자리" % pos, ui.TARGET)
        self._set_hint("이제 이 자리에 넣을 키를 누르세요.   "
                       "누를 수 없는 기능은 [다른 기능…]   ·   Esc = 취소", ui.WARN)

    def start_probe(self):
        if self.conn is None or self.busy or not self.keymap:
            return
        if self._dirty:
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
        self._set_busy(True, "탐색 준비 중…")

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
        self._set_detail("탐색 중", ui.TARGET)
        self._set_hint("키보드에서 바꾸고 싶은 키를 누르세요.   Esc = 취소\n"
                       "이 창의 포커스를 유지하세요 — 지금 키맵은 임시 상태입니다.",
                       ui.WARN)

    def _probe_failed(self, exc):
        self._set_busy(False)
        self.mode = "idle"
        self.worker.submit(self.conn.discard_changes,
                           lambda _r: self.refresh(), lambda _e: None)
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
                self._set_detail("")
                self._set_hint("취소했습니다.")
            return "break"

        if self.mode == "probe":
            usage = kc.VK_TO_USAGE.get(evt.keycode)
            pos = self._probe_map.get(usage)
            if pos is None:
                return "break"
            self._end_probe(then=lambda: self._begin_capture(pos))
            return "break"

        if self.mode == "capture":
            if self.busy:
                return "break"
            usage = kc.VK_TO_USAGE.get(evt.keycode)
            if usage is None:
                self._set_hint("모르는 키입니다 (VK 0x%02X). 다른 키를 눌러보세요."
                               % evt.keycode, ui.ERR)
                return "break"
            mods = 0
            if not (0xE0 <= usage <= 0xE7):
                if evt.state & 0x0001:
                    mods |= kc.MOD_LSFT
                if evt.state & 0x0004:
                    mods |= kc.MOD_LCTL
                if evt.state & 0x20000:
                    mods |= kc.MOD_LALT
            self._apply_binding(BEHAVIOR_KEY_PRESS, kc.encode(usage, mods=mods), 0)
            return "break"
        return None

    # -- assigning ----------------------------------------------------------

    def pick_behavior(self):
        """Assign something you cannot express by pressing a key.

        Output toggle, BLE profile select, layer moves - none of these can be
        demonstrated on the keyboard, so they need a list.
        """
        if self.conn is None or self.busy or not self.keymap:
            return
        if self.selected is None:
            self._set_hint("먼저 바꿀 키를 고르세요 — 클릭하거나 "
                           "[키 눌러서 선택]을 쓰세요.", ui.WARN)
            return
        choice = picker.ask(self, self.catalog, list(self.keymap.layers))
        if choice is None:
            return
        self._apply_binding(*choice)

    def _apply_binding(self, behavior_id, param1, param2):
        layer = self.keymap.layers[self.layer_index]
        pos = self.selected
        self.mode = "idle"
        self.selected = None
        self._set_busy(True, "쓰는 중…")

        def job():
            return self.conn.set_binding(layer.id, pos, behavior_id,
                                         param1, param2)

        def done(resp):
            self._set_busy(False)
            if resp != 0:
                reason = {1: "위치가 잘못됨", 2: "그 기능을 쓸 수 없음",
                          3: "파라미터가 맞지 않음"}.get(resp, "코드 %d" % resp)
                messagebox.showerror("쓰기 거부됨", "키보드가 거부했습니다: %s" % reason)
                return
            self.refresh()
            self._set_detail("%s 레이어 %d번 변경됨" % (layer.name, pos), ui.OK)
            self._set_hint("[저장]을 눌러야 키보드에 기록됩니다.", ui.OK)

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror("쓰기 실패", str(exc))

        self.worker.submit(job, done, failed)

    # -- firmware -----------------------------------------------------------

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
        same = " (이미 내려받은 버전입니다)" if here == release["tag"] else ""
        if not messagebox.askyesno(
                "펌웨어 업데이트",
                "최신 빌드: %s%s\n\n내려받고 왼쪽 반쪽에 올릴까요?"
                % (release["tag"], same)):
            return
        self._set_busy(True, "내려받는 중…")
        self.worker.submit(lambda: firmware.sync(FIRMWARE_DIR, release),
                           self._downloaded, self._firmware_failed)

    def _downloaded(self, release):
        self._set_busy(False)
        if not messagebox.askyesno(
                "부트로더 진입",
                "%s 를 내려받았습니다.\n\n"
                "이제 키보드에서 부트로더 키를 누르세요:\n"
                "    Lower + 오른쪽 아래 맨 끝 키\n\n"
                "(리셋 버튼을 빠르게 두 번 눌러도 됩니다.)\n\n"
                "[예]를 누르면 드라이브가 나타날 때까지 기다립니다."
                % release["tag"]):
            return
        self._set_busy(True, "부트로더를 기다리는 중…")
        self.worker.submit(lambda: firmware.wait_for_bootloader(90.0),
                           self._bootloader_ready, self._firmware_failed)

    def _bootloader_ready(self, drive):
        if drive is None:
            self._set_busy(False)
            self._set_detail("부트로더 드라이브를 찾지 못했습니다", ui.ERR)
            self._set_hint("부트로더 키가 안 먹으면 리셋 버튼을 빠르게 두 번 누르세요.")
            return
        self._set_busy(True, "%s 에 쓰는 중…" % drive)

        def done(name):
            self._set_busy(False)
            self._set_detail("플래싱 완료 — %s" % name, ui.OK)
            self._set_hint("키보드가 재부팅됩니다. 다시 [USB] 또는 [블루투스]로 "
                           "연결하세요.")
            self.disconnect()

        self.worker.submit(lambda: firmware.flash(FIRMWARE_DIR, "left", drive),
                           done, self._firmware_failed)

    def _firmware_failed(self, exc):
        self._set_busy(False)
        self._set_detail("펌웨어 작업 실패", ui.ERR)
        self._set_hint(str(exc))

    # -- persistence --------------------------------------------------------

    def save(self):
        if self.conn is None or not self._dirty:
            return
        self._set_busy(True, "저장 중…")

        def done(res):
            self._set_busy(False)
            if res.WhichOneof("result") == "err":
                messagebox.showerror("저장 실패", "오류 코드 %d" % res.err)
                return
            self.refresh()
            self._set_detail("키보드에 저장했습니다", ui.OK)

        def failed(exc):
            self._set_busy(False)
            messagebox.showerror("저장 실패", str(exc))

        self.worker.submit(self.conn.save_changes, done, failed)

    def discard(self):
        if self.conn is None or not self._dirty:
            return
        self._set_busy(True, "되돌리는 중…")

        def done(_r):
            self._set_busy(False)
            self.refresh()
            self._set_detail("마지막 저장 시점으로 되돌렸습니다")

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
