"""Runs keyboard I/O off the UI thread.

Every RPC call blocks until the keyboard replies. Doing that on tkinter's thread
freezes the window - opening the app was showing "not responding" because
connecting alone could sit on a 5 second timeout, and building the behavior
catalog is 19 round trips before the first frame is even drawn.

Jobs run one at a time on a single thread, which also keeps requests to the
keyboard serialised. Callbacks are marshalled back onto the UI thread.
"""

import queue
import threading


class Worker:
    def __init__(self, tk_root):
        self._root = tk_root
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def submit(self, fn, on_done=None, on_error=None):
        """Run fn() on the worker; deliver its result on the UI thread."""
        self._q.put((fn, on_done, on_error))

    def _loop(self):
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                return
            fn, on_done, on_error = item
            try:
                result = fn()
            except Exception as exc:
                if on_error:
                    self._post(on_error, exc)
                continue
            if on_done:
                self._post(on_done, result)

    def _post(self, fn, arg):
        try:
            self._root.after(0, lambda: fn(arg))
        except RuntimeError:
            # Window already destroyed.
            pass

    def stop(self):
        self._stop.set()
        self._q.put(None)
