"""Minimal client for the ZMK Studio RPC protocol.

Transport is a CDC-ACM serial port. Each message is one frame:

    SOF (0xAB) | escaped protobuf bytes | EOF (0xAD)

Any payload byte equal to 0xAB / 0xAC / 0xAD is prefixed with ESC (0xAC) and
written literally — no XOR. Matches app/src/studio/msg_framing.c in ZMK.
"""

import os
import sys
import threading
import queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serial  # noqa: E402
from serial.tools import list_ports  # noqa: E402

import studio_pb2  # noqa: E402
import core_pb2  # noqa: E402
import keymap_pb2  # noqa: E402
import behaviors_pb2  # noqa: E402

SOF = 0xAB
ESC = 0xAC
EOF = 0xAD

# ZMK's USB VID/PID.
ZMK_VID = 0x1D50
ZMK_PID = 0x615E


class RpcError(RuntimeError):
    pass


def find_ports():
    """Serial ports that look like a ZMK device, best guess first."""
    ports = list(list_ports.comports())
    zmk = [p for p in ports if p.vid == ZMK_VID and p.pid == ZMK_PID]
    return zmk + [p for p in ports if p not in zmk]


def _escape(payload: bytes) -> bytes:
    out = bytearray()
    for b in payload:
        if b in (SOF, ESC, EOF):
            out.append(ESC)
        out.append(b)
    return bytes(out)


class Connection:
    def __init__(self, port: str, timeout: float = 5.0):
        self.timeout = timeout
        self._serial = serial.Serial(port, baudrate=115200, timeout=0.1)
        self._responses = {}
        self._responses_lock = threading.Lock()
        self._notifications = queue.Queue()
        self._events = {}
        self._next_id = 1
        self._stop = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- framing ------------------------------------------------------------

    def _read_loop(self):
        buf = bytearray()
        in_frame = False
        escaped = False
        while not self._stop.is_set():
            try:
                chunk = self._serial.read(256)
            except (OSError, serial.SerialException):
                break
            for b in chunk:
                if escaped:
                    buf.append(b)
                    escaped = False
                elif b == ESC:
                    if in_frame:
                        escaped = True
                elif b == SOF:
                    in_frame = True
                    buf.clear()
                elif b == EOF:
                    if in_frame:
                        self._dispatch(bytes(buf))
                    in_frame = False
                    buf.clear()
                elif in_frame:
                    buf.append(b)

    def _dispatch(self, payload: bytes):
        resp = studio_pb2.Response()
        try:
            resp.ParseFromString(payload)
        except Exception:
            return
        kind = resp.WhichOneof("type")
        if kind == "notification":
            self._notifications.put(resp.notification)
        elif kind == "request_response":
            rid = resp.request_response.request_id
            with self._responses_lock:
                self._responses[rid] = resp.request_response
                event = self._events.get(rid)
            if event:
                event.set()

    # -- request/response ---------------------------------------------------

    def call(self, **subsystem):
        """Send one Request and wait for its matching RequestResponse.

        Pass exactly one subsystem message, e.g. call(core=core_req).
        """
        with self._responses_lock:
            rid = self._next_id
            self._next_id += 1
            event = threading.Event()
            self._events[rid] = event

        req = studio_pb2.Request(request_id=rid, **subsystem)
        frame = bytes([SOF]) + _escape(req.SerializeToString()) + bytes([EOF])
        self._serial.write(frame)
        self._serial.flush()

        if not event.wait(self.timeout):
            with self._responses_lock:
                self._events.pop(rid, None)
            raise RpcError("timed out waiting for a response (is the keyboard connected?)")

        with self._responses_lock:
            self._events.pop(rid, None)
            rr = self._responses.pop(rid)

        if rr.WhichOneof("subsystem") == "meta":
            meta = rr.meta
            if meta.WhichOneof("response_type") == "simple_error":
                name = {
                    1: "UNLOCK_REQUIRED — press the Studio Unlock key on the keyboard",
                    2: "RPC_NOT_FOUND",
                    3: "MSG_DECODE_FAILED",
                    4: "MSG_ENCODE_FAILED",
                }.get(meta.simple_error, f"error {meta.simple_error}")
                raise RpcError(name)
        return rr

    def notifications(self):
        while True:
            try:
                yield self._notifications.get_nowait()
            except queue.Empty:
                return

    def close(self):
        self._stop.set()
        try:
            self._serial.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- convenience wrappers ----------------------------------------------

    def device_info(self):
        return self.call(core=core_pb2.Request(get_device_info=True)).core.get_device_info

    def lock_state(self):
        return self.call(core=core_pb2.Request(get_lock_state=True)).core.get_lock_state

    def lock(self):
        return self.call(core=core_pb2.Request(lock=True))

    def get_keymap(self):
        return self.call(keymap=keymap_pb2.Request(get_keymap=True)).keymap.get_keymap

    def get_physical_layouts(self):
        return self.call(
            keymap=keymap_pb2.Request(get_physical_layouts=True)
        ).keymap.get_physical_layouts

    def list_behaviors(self):
        return self.call(
            behaviors=behaviors_pb2.Request(list_all_behaviors=True)
        ).behaviors.list_all_behaviors.behaviors

    def behavior_details(self, behavior_id: int):
        return self.call(
            behaviors=behaviors_pb2.Request(
                get_behavior_details=behaviors_pb2.GetBehaviorDetailsRequest(
                    behavior_id=behavior_id
                )
            )
        ).behaviors.get_behavior_details

    def set_binding(self, layer_id: int, key_position: int, behavior_id: int,
                    param1: int = 0, param2: int = 0):
        return self.call(
            keymap=keymap_pb2.Request(
                set_layer_binding=keymap_pb2.SetLayerBindingRequest(
                    layer_id=layer_id,
                    key_position=key_position,
                    binding=keymap_pb2.BehaviorBinding(
                        behavior_id=behavior_id, param1=param1, param2=param2
                    ),
                )
            )
        ).keymap.set_layer_binding

    def has_unsaved_changes(self):
        return self.call(
            keymap=keymap_pb2.Request(check_unsaved_changes=True)
        ).keymap.check_unsaved_changes

    def save_changes(self):
        return self.call(keymap=keymap_pb2.Request(save_changes=True)).keymap.save_changes

    def discard_changes(self):
        return self.call(keymap=keymap_pb2.Request(discard_changes=True)).keymap.discard_changes
