"""Dialog for assigning a behavior that you cannot express by pressing a key.

Pressing the key you want is the right gesture for a key press. It is no help
for "toggle the output endpoint" or "select BLE profile 2" - there is nothing to
press. Those need a list.

The list is built entirely from what the keyboard reports. Every behavior ships
its display name, and every parameter ships either a set of named constants, a
layer reference, a HID usage, or a numeric range. Nothing about specific
behaviors is hardcoded, so a firmware with new behaviors populates this dialog
on its own.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keycodes as kc  # noqa: E402

BG = "#1b1d21"
FG = "#e6e6e6"
DIM = "#8b9199"


class BehaviorPicker(tk.Toplevel):
    """Modal chooser. self.result is (behavior_id, param1, param2) or None."""

    def __init__(self, parent, catalog, layers):
        super().__init__(parent)
        self.title("기능 선택")
        self.configure(bg=BG)
        self.geometry("460x430")
        self.transient(parent)
        self.result = None
        self.catalog = catalog
        self.layers = layers
        self._param_widgets = []

        self._names = sorted(
            ((bid, catalog.name(bid)) for bid in catalog.behaviors),
            key=lambda item: item[1].lower())

        tk.Label(self, text="기능", bg=BG, fg=DIM, anchor="w").pack(
            fill="x", padx=14, pady=(14, 2))
        self.listbox = tk.Listbox(self, bg="#2a2e34", fg=FG, height=11,
                                  highlightthickness=0, borderwidth=0,
                                  selectbackground="#3d6fd6",
                                  activestyle="none", font=("Segoe UI", 10))
        for _bid, name in self._names:
            self.listbox.insert("end", name)
        self.listbox.pack(fill="both", expand=True, padx=14)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        self.params = tk.Frame(self, bg=BG)
        self.params.pack(fill="x", padx=14, pady=(10, 0))

        self.note = tk.Label(self, text="", bg=BG, fg=DIM, anchor="w",
                             wraplength=420, justify="left",
                             font=("Segoe UI", 9))
        self.note.pack(fill="x", padx=14, pady=(6, 0))

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=14, pady=12)
        tk.Button(buttons, text="적용", width=10, command=self._accept).pack(side="right")
        tk.Button(buttons, text="취소", width=10, command=self.destroy).pack(
            side="right", padx=(0, 6))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.listbox.selection_set(0)
        self._on_select()
        self.grab_set()
        self.listbox.focus_set()

    # -- parameter widgets ---------------------------------------------------

    def _selected_id(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self._names[sel[0]][0]

    def _on_select(self, _evt=None):
        for widget in self.params.winfo_children():
            widget.destroy()
        self._param_widgets = []

        bid = self._selected_id()
        if bid is None:
            return
        details = self.catalog.behaviors.get(bid)
        sets = list(details.metadata) if details else []
        if not sets:
            self.note.config(text="파라미터 없음.")
            return

        # Only the first parameter set is offered; picking among sets would
        # mean a second chooser for no real gain.
        pset = sets[0]
        self.note.config(
            text="파라미터 조합이 여러 개인 기능입니다. 첫 번째 조합을 사용합니다."
            if len(sets) > 1 else "")

        for index, descs in ((0, list(pset.param1)), (1, list(pset.param2))):
            if not descs:
                continue
            self._add_param(index, descs)

    def _add_param(self, index, descs):
        row = tk.Frame(self.params, bg=BG)
        row.pack(fill="x", pady=3)
        label = descs[0].name or ("파라미터 %d" % (index + 1))
        tk.Label(row, text=label, bg=BG, fg=DIM, width=10, anchor="w").pack(side="left")

        kinds = {d.WhichOneof("value_type") for d in descs}

        if kinds == {"constant"}:
            box = ttk.Combobox(row, state="readonly", width=30,
                               values=[d.name for d in descs])
            box.current(0)
            box.pack(side="left", fill="x", expand=True)
            self._param_widgets.append(
                (index, lambda b=box, d=descs: d[b.current()].constant))
            return

        desc = descs[0]
        kind = desc.WhichOneof("value_type")

        if kind == "layer_id":
            names = [L.name or ("Layer %d" % i) for i, L in enumerate(self.layers)]
            box = ttk.Combobox(row, state="readonly", width=30, values=names)
            box.current(0)
            box.pack(side="left", fill="x", expand=True)
            self._param_widgets.append(
                (index, lambda b=box: self.layers[b.current()].id))
            return

        if kind == "hid_usage":
            entry = tk.Entry(row, bg="#2a2e34", fg=FG, insertbackground=FG,
                             relief="flat", width=30)
            entry.pack(side="left", fill="x", expand=True)
            entry.insert(0, "여기를 누르고 원하는 키를 누르세요")
            state = {"code": 0}

            def on_key(evt, e=entry, s=state):
                usage = kc.VK_TO_USAGE.get(evt.keycode)
                if usage is None:
                    return "break"
                mods = 0
                if not (0xE0 <= usage <= 0xE7):
                    if evt.state & 0x0001:
                        mods |= kc.MOD_LSFT
                    if evt.state & 0x0004:
                        mods |= kc.MOD_LCTL
                    if evt.state & 0x20000:
                        mods |= kc.MOD_LALT
                s["code"] = kc.encode(usage, mods=mods)
                e.delete(0, "end")
                e.insert(0, kc.key_label(s["code"]))
                return "break"

            entry.bind("<Key>", on_key)
            self._param_widgets.append((index, lambda s=state: s["code"]))
            return

        # range, or anything unrecognised: a plain number.
        spin_from, spin_to = 0, 255
        if kind == "range":
            spin_from, spin_to = desc.range.min, desc.range.max
        spin = tk.Spinbox(row, from_=spin_from, to=spin_to, width=10,
                          bg="#2a2e34", fg=FG, insertbackground=FG, relief="flat")
        spin.pack(side="left")
        self._param_widgets.append(
            (index, lambda s=spin: int(s.get() or 0)))

    # -- result --------------------------------------------------------------

    def _accept(self):
        bid = self._selected_id()
        if bid is None:
            return
        params = [0, 0]
        for index, getter in self._param_widgets:
            try:
                params[index] = int(getter())
            except (TypeError, ValueError):
                params[index] = 0
        self.result = (bid, params[0], params[1])
        self.destroy()


def ask(parent, catalog, layers):
    dialog = BehaviorPicker(parent, catalog, layers)
    parent.wait_window(dialog)
    return dialog.result
