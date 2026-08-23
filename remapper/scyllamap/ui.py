"""Small canvas-drawn widget set.

Tk's stock widgets carry a 1990s bevel that no amount of option-setting removes,
so anything with a visible edge is drawn on a Canvas instead: rounded buttons,
battery gauges, connection chips, layer tabs. Everything shares one palette so
the window reads as a single surface rather than a stack of grey boxes.
"""

import ctypes
import tkinter as tk

# -- palette ---------------------------------------------------------------

BG = "#15171c"          # window
SURFACE = "#1d2027"     # panels
SURFACE_HI = "#262a33"  # keys, inputs
SURFACE_HOVER = "#30353f"
BORDER = "#2c313a"
TEXT = "#e9ecf1"
TEXT_DIM = "#98a0ad"
TEXT_FAINT = "#6b7280"
ACCENT = "#4c8dff"
ACCENT_DIM = "#2f5dad"
TARGET = "#f0902b"
OK = "#46d17f"
WARN = "#ffb454"
ERR = "#ff6b6b"

FONT = "Segoe UI"
F_BODY = (FONT, 10)
F_SMALL = (FONT, 9)
F_TINY = (FONT, 8)
F_TITLE = (FONT, 13, "bold")
F_LABEL = (FONT, 9, "bold")


def dark_titlebar(window):
    """Ask Windows for the dark window chrome.

    Tk draws the client area only; without this the title bar stays white and
    the window looks like two different applications stacked together.
    """
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):   # 20 on current builds, 19 on older ones
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value),
                    ctypes.sizeof(value)) == 0:
                return True
    except Exception:
        pass
    return False


def key_font(unit):
    """Key cap text scaled to how big the keys ended up."""
    if unit >= 0.70:
        return (FONT, 10)
    if unit >= 0.46:
        return (FONT, 9)
    return F_TINY


def round_rect_points(x0, y0, x1, y1, r, steps=5):
    """Point list for a rounded rectangle, for create_polygon."""
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    if r == 0:
        return [x0, y0, x1, y0, x1, y1, x0, y1]

    def arc(cx, cy, start, end):
        pts = []
        for i in range(steps + 1):
            t = start + (end - start) * i / steps
            # Quarter turns only, so a cheap parametric circle is plenty.
            import math
            pts.extend((cx + r * math.cos(t), cy + r * math.sin(t)))
        return pts

    import math
    pts = []
    pts += arc(x1 - r, y0 + r, -math.pi / 2, 0)
    pts += arc(x1 - r, y1 - r, 0, math.pi / 2)
    pts += arc(x0 + r, y1 - r, math.pi / 2, math.pi)
    pts += arc(x0 + r, y0 + r, math.pi, math.pi * 3 / 2)
    return pts


def rounded(canvas, x0, y0, x1, y1, r, **kw):
    return canvas.create_polygon(round_rect_points(x0, y0, x1, y1, r),
                                 smooth=False, **kw)


# -- button ----------------------------------------------------------------

class Button(tk.Canvas):
    """Flat rounded button with hover and disabled states."""

    def __init__(self, parent, text, command=None, width=None, primary=False,
                 height=30, bg=None, **kw):
        self._text = text
        self._command = command
        self._primary = primary
        self._enabled = True
        self._hover = False
        self._parent_bg = bg or parent.cget("bg")

        font = F_SMALL
        pad = 22
        if width is None:
            probe = tk.Label(parent, text=text, font=font)
            width = probe.winfo_reqwidth() + pad
            probe.destroy()

        super().__init__(parent, width=width, height=height, bg=self._parent_bg,
                         highlightthickness=0, bd=0, **kw)
        self._cw, self._ch = width, height
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _colors(self):
        if not self._enabled:
            return SURFACE, TEXT_FAINT, BORDER
        if self._primary:
            return (ACCENT if not self._hover else "#5f9bff"), "#ffffff", ""
        return ((SURFACE_HOVER if self._hover else SURFACE_HI), TEXT, BORDER)

    def _draw(self):
        self.delete("all")
        fill, fg, outline = self._colors()
        rounded(self, 1, 1, self._cw - 1, self._ch - 1, 7,
                fill=fill, outline=outline or fill, width=1)
        self.create_text(self._cw / 2, self._ch / 2, text=self._text,
                         fill=fg, font=F_SMALL)

    def _on_enter(self, _e):
        self._hover = True
        self._draw()

    def _on_leave(self, _e):
        self._hover = False
        self._draw()

    def _on_click(self, _e):
        if self._enabled and self._command:
            self._command()

    def config_state(self, enabled: bool):
        if enabled != self._enabled:
            self._enabled = enabled
            self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()


# -- connection chip --------------------------------------------------------

class Chip(tk.Canvas):
    """A pill: coloured dot plus a word."""

    def __init__(self, parent, height=22, bg=None):
        self._parent_bg = bg or parent.cget("bg")
        super().__init__(parent, width=10, height=height, bg=self._parent_bg,
                         highlightthickness=0, bd=0)
        self._ch = height
        self.set("", TEXT_DIM)

    def set(self, text, colour=TEXT_DIM):
        self.delete("all")
        if not text:
            self.configure(width=1)
            return
        probe = tk.Label(self, text=text, font=F_SMALL)
        tw = probe.winfo_reqwidth()
        probe.destroy()
        w = tw + 34
        self.configure(width=w)
        rounded(self, 0, 0, w, self._ch, self._ch / 2,
                fill=SURFACE_HI, outline=BORDER)
        cy = self._ch / 2
        self.create_oval(12, cy - 3.5, 19, cy + 3.5, fill=colour, outline="")
        self.create_text(24, cy, text=text, fill=TEXT, font=F_SMALL, anchor="w")


# -- battery gauge ----------------------------------------------------------

class BatteryGauge(tk.Canvas):
    """Label, rounded bar, percentage. Colour tracks the level."""

    def __init__(self, parent, label, width=150, height=34, bg=None):
        self._parent_bg = bg or parent.cget("bg")
        super().__init__(parent, width=width, height=height,
                         bg=self._parent_bg, highlightthickness=0, bd=0)
        self._label = label
        self._cw, self._ch = width, height
        self._percent = None
        self.draw()

    @staticmethod
    def colour_for(percent):
        if percent is None:
            return TEXT_FAINT
        if percent <= 15:
            return ERR
        if percent <= 35:
            return WARN
        return OK

    def set(self, percent):
        self._percent = percent
        self.draw()

    def draw(self):
        self.delete("all")
        bar_x0, bar_x1 = 0, self._cw - 42
        bar_y = self._ch - 9
        bar_h = 7

        self.create_text(0, 8, text=self._label, fill=TEXT_DIM,
                         font=F_TINY, anchor="w")

        rounded(self, bar_x0, bar_y - bar_h / 2, bar_x1, bar_y + bar_h / 2,
                bar_h / 2, fill=SURFACE_HI, outline="")

        if self._percent is None:
            self.create_text(self._cw, bar_y, text="—", fill=TEXT_FAINT,
                             font=F_SMALL, anchor="e")
            return

        colour = self.colour_for(self._percent)
        filled = bar_x0 + (bar_x1 - bar_x0) * max(0, min(100, self._percent)) / 100
        if filled > bar_x0 + bar_h:
            rounded(self, bar_x0, bar_y - bar_h / 2, filled, bar_y + bar_h / 2,
                    bar_h / 2, fill=colour, outline="")
        elif self._percent > 0:
            self.create_oval(bar_x0, bar_y - bar_h / 2, bar_x0 + bar_h,
                             bar_y + bar_h / 2, fill=colour, outline="")

        self.create_text(self._cw, bar_y, text="%d%%" % self._percent,
                         fill=TEXT, font=F_SMALL, anchor="e")


# -- layer tabs -------------------------------------------------------------

class SegmentedTabs(tk.Canvas):
    """One row of tabs; the active one is filled with the accent colour."""

    def __init__(self, parent, on_select, height=30, bg=None):
        self._parent_bg = bg or parent.cget("bg")
        super().__init__(parent, height=height, bg=self._parent_bg,
                         highlightthickness=0, bd=0)
        self._ch = height
        self._names = []
        self._active = 0
        self._hover = None
        self._rects = []
        self._on_select = on_select
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))

    def set_items(self, names, active=0):
        self._names = list(names)
        self._active = active
        self.draw()

    def set_active(self, index):
        self._active = index
        self.draw()

    def _index_at(self, x):
        for i, (x0, x1) in enumerate(self._rects):
            if x0 <= x <= x1:
                return i
        return None

    def _on_click(self, evt):
        index = self._index_at(evt.x)
        if index is not None and index != self._active:
            self._active = index
            self.draw()
            self._on_select(index)

    def _on_motion(self, evt):
        self._set_hover(self._index_at(evt.x))

    def _set_hover(self, index):
        if index != self._hover:
            self._hover = index
            self.draw()

    def draw(self):
        self.delete("all")
        self._rects = []
        x = 0
        gap = 4
        for i, name in enumerate(self._names):
            probe = tk.Label(self, text=name, font=F_SMALL)
            w = probe.winfo_reqwidth() + 24
            probe.destroy()
            active = i == self._active
            fill = ACCENT if active else (
                SURFACE_HOVER if i == self._hover else SURFACE_HI)
            fg = "#ffffff" if active else TEXT_DIM
            rounded(self, x, 3, x + w, self._ch - 3, 7, fill=fill, outline="")
            self.create_text(x + w / 2, self._ch / 2, text=name, fill=fg,
                             font=F_SMALL)
            self._rects.append((x, x + w))
            x += w + gap
        self.configure(width=max(x, 1))


# -- misc -------------------------------------------------------------------

def separator(parent, bg=None):
    frame = tk.Frame(parent, height=1, bg=BORDER)
    return frame


def panel(parent, **kw):
    return tk.Frame(parent, bg=SURFACE, **kw)
