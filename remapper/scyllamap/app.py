"""Entry point: tray icon plus the remap window.

Run with --tray to start hidden in the notification area (what the Startup
shortcut does). Without it the window opens right away.

Tkinter must own the main thread, so pystray runs on a worker and its menu
actions are marshalled back with `after(0, ...)`.
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui        # noqa: E402
import startup    # noqa: E402

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")


def _icon_image():
    from PIL import Image, ImageDraw
    if os.path.exists(ICON_PATH):
        try:
            return Image.open(ICON_PATH)
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 14, 60, 50), radius=7, fill=(61, 111, 214, 255))
    for row in range(2):
        for col in range(5):
            x = 11 + col * 9
            y = 21 + row * 11
            d.rectangle((x, y, x + 6, y + 7), fill=(255, 255, 255, 235))
    d.rectangle((24, 43, 46, 46), fill=(255, 255, 255, 235))
    return img


class TrayApp:
    def __init__(self, start_hidden: bool):
        self.window = gui.EditorWindow()
        self.window.protocol("WM_DELETE_WINDOW", self.window.hide)
        if start_hidden:
            self.window.withdraw()
        else:
            self.window.show()

        self.icon = None
        threading.Thread(target=self._run_tray, daemon=True).start()

    # -- tray ---------------------------------------------------------------

    def _run_tray(self):
        import pystray
        from pystray import MenuItem as Item

        menu = pystray.Menu(
            Item("열기", self._open, default=True),
            pystray.Menu.SEPARATOR,
            Item("Windows 시작 시 실행",
                 self._toggle_startup,
                 checked=lambda _i: startup.is_enabled()),
            pystray.Menu.SEPARATOR,
            Item("종료", self._quit),
        )
        self.icon = pystray.Icon("scylla_remapper", _icon_image(),
                                 "Scylla Remapper", menu)
        self.icon.run()

    def _open(self, _icon=None, _item=None):
        self.window.after(0, self.window.show)

    def _toggle_startup(self, _icon=None, _item=None):
        try:
            startup.toggle()
        except Exception as exc:
            self.window.after(0, lambda: self._error(exc))
        if self.icon:
            self.icon.update_menu()

    def _error(self, exc):
        from tkinter import messagebox
        messagebox.showerror("시작 프로그램 등록 실패", str(exc))

    def _quit(self, _icon=None, _item=None):
        if self.icon:
            self.icon.stop()
        self.window.after(0, self.window.shutdown)

    def run(self):
        self.window.mainloop()


def main():
    TrayApp(start_hidden="--tray" in sys.argv).run()


if __name__ == "__main__":
    main()
