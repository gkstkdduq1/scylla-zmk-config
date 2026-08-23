"""Client for the ZMK Studio RPC protocol, over USB serial or BLE.

Each message is one frame:

    SOF (0xAB) | escaped protobuf bytes | EOF (0xAD)

Any payload byte equal to 0xAB / 0xAC / 0xAD is prefixed with ESC (0xAC) and
written literally - no XOR. Matches app/src/studio/msg_framing.c in ZMK.

Two transports, same framing:
  * serial - CDC-ACM over USB. Exclusive: ZMK Studio cannot hold the port too.
  * BLE    - the GATT service ZMK exposes when CONFIG_ZMK_STUDIO_TRANSPORT_BLE
             is on, which is the default on any BLE build. Requires the
             keyboard to be paired to this machine on some profile.

Every call here blocks on the reply, so run them off the UI thread.
"""

import asyncio
import os
import queue
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import serial  # noqa: E402
from serial.tools import list_ports  # noqa: E402

import studio_pb2      # noqa: E402
import core_pb2        # noqa: E402
import keymap_pb2      # noqa: E402
import behaviors_pb2   # noqa: E402

SOF = 0xAB
ESC = 0xAC
EOF = 0xAD

ZMK_VID = 0x1D50
ZMK_PID = 0x615E

# app/src/studio/uuid.h
BLE_SERVICE_UUID = "00000000-0196-6107-c967-c5cfb1c2482a"
BLE_RPC_CHRC_UUID = "00000001-0196-6107-c967-c5cfb1c2482a"


class RpcError(RuntimeError):
    pass


def _escape(payload: bytes) -> bytes:
    out = bytearray()
    for b in payload:
        if b in (SOF, ESC, EOF):
            out.append(ESC)
        out.append(b)
    return bytes(out)


class _Deframer:
    """Byte stream -> complete frame payloads."""

    def __init__(self, on_frame):
        self._on_frame = on_frame
        self._buf = bytearray()
        self._in_frame = False
        self._escaped = False

    def feed(self, chunk: bytes):
        for b in chunk:
            if self._escaped:
                self._buf.append(b)
                self._escaped = False
            elif b == ESC:
                if self._in_frame:
                    self._escaped = True
            elif b == SOF:
                self._in_frame = True
                self._buf.clear()
            elif b == EOF:
                if self._in_frame:
                    self._on_frame(bytes(self._buf))
                self._in_frame = False
                self._buf.clear()
            elif self._in_frame:
                self._buf.append(b)


# -- transports -------------------------------------------------------------

def find_ports():
    """Serial ports belonging to a ZMK device.

    Matched strictly by VID/PID. Falling back to "any serial port" would hand
    back the machine's Bluetooth virtual COM ports, and connecting to one of
    those just hangs until the request times out.
    """
    return [p for p in list_ports.comports()
            if p.vid == ZMK_VID and p.pid == ZMK_PID]


class SerialTransport:
    name = "USB"

    def __init__(self, port: str):
        self.port = port
        self._serial = serial.Serial(port, baudrate=115200, timeout=0.1)
        self._stop = threading.Event()
        self._on_bytes = None
        self._thread = None

    def start(self, on_bytes):
        self._on_bytes = on_bytes
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                chunk = self._serial.read(256)
            except Exception:
                # Closing the port from another thread surfaces here in several
                # different shapes depending on the platform; all mean "done".
                break
            if chunk:
                self._on_bytes(chunk)

    def write(self, data: bytes):
        self._serial.write(data)
        self._serial.flush()

    def close(self):
        self._stop.set()
        try:
            self._serial.close()
        except Exception:
            pass

    def describe(self):
        return self.port


class BleTransport:
    """ZMK Studio over its GATT service.

    bleak is asyncio-only, so it gets its own event loop on a private thread and
    the synchronous methods below hand work to it.
    """

    name = "BLE"

    def __init__(self, address: str, label: str = None, timeout: float = 20.0):
        self.address = address
        self.label = label or address
        self._timeout = timeout
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._client = None
        self._on_bytes = None
        self._connect()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout=None):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout or self._timeout)

    def _connect(self):
        from bleak import BleakClient
        self._client = BleakClient(self.address, timeout=self._timeout)
        # bleak's WinRT backend scans for the device before connecting, but a
        # bonded ZMK keyboard stops advertising, so the scan never finds it.
        # Handing it the address up front skips that step; WinRT can then reach
        # the device directly because Windows already has it paired.
        try:
            self._client._backend._device_info = int(
                self.address.replace(":", "").replace("-", ""), 16)
        except Exception:
            pass
        self._call(self._client.connect())
        services = self._client.services
        if services.get_characteristic(BLE_RPC_CHRC_UUID) is None:
            try:
                self._call(self._client.disconnect())
            except Exception:
                pass
            raise RpcError(
                "이 장치에 ZMK Studio GATT 서비스가 없습니다. "
                "Studio 지원 펌웨어인지 확인하세요.")

    def start(self, on_bytes):
        self._on_bytes = on_bytes

        def handler(_chrc, data: bytearray):
            on_bytes(bytes(data))

        self._call(self._client.start_notify(BLE_RPC_CHRC_UUID, handler))

    def write(self, data: bytes):
        # The RPC characteristic is "write" and "indicate" - not
        # write-without-response - so every chunk must be acknowledged.
        mtu = getattr(self._client, "mtu_size", 23) or 23
        chunk = max(20, mtu - 3)
        for i in range(0, len(data), chunk):
            self._call(self._client.write_gatt_char(
                BLE_RPC_CHRC_UUID, data[i:i + chunk], response=True))

    def read_batteries(self):
        """-> [(label, percent)] for every battery service the keyboard exposes.

        A split central proxies its peripherals' levels as extra Battery
        Service instances. The peripheral ones carry a 0x2901 user description
        such as "Peripheral 0"; the one without it is this half.
        """
        out = []
        for service in self._client.services:
            if not service.uuid.lower().startswith("0000180f"):
                continue
            for ch in service.characteristics:
                if not ch.uuid.lower().startswith("00002a19"):
                    continue
                try:
                    value = self._call(self._client.read_gatt_char(ch))
                except Exception:
                    continue
                label = None
                for desc in ch.descriptors:
                    if desc.uuid.lower().startswith("00002901"):
                        try:
                            raw = self._call(
                                self._client.read_gatt_descriptor(desc.handle))
                            label = bytes(raw).decode("utf-8", "replace").strip()
                        except Exception:
                            pass
                out.append((label, value[0] if value else None))
        return out

    def close(self):
        try:
            self._call(self._client.disconnect(), timeout=5)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    def describe(self):
        return self.label


def find_paired_ble():
    """-> [(address, name)] for BLE devices Windows has paired.

    This is the path that matters: once a ZMK keyboard is bonded it stops
    advertising, so scanning will not see it even while it is working fine.
    Windows still lists it, and WinRT can connect to a paired device by address
    without any discovery.
    """
    import re
    import subprocess

    ps = ("Get-PnpDevice | Where-Object { $_.InstanceId -like 'BTHLE\\DEV_*' } |"
          " ForEach-Object { $_.InstanceId + '|' + $_.FriendlyName }")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return []

    out = []
    seen = set()
    for line in (proc.stdout or "").splitlines():
        instance_id, _, name = line.strip().partition("|")
        match = re.search(r"DEV_([0-9A-Fa-f]{12})", instance_id)
        if not match:
            continue
        hexaddr = match.group(1).upper()
        if hexaddr in seen:
            continue
        seen.add(hexaddr)
        addr = ":".join(hexaddr[i:i + 2] for i in range(0, 12, 2))
        out.append((addr, name.strip() or addr))
    return out


def find_ble_devices(timeout: float = 6.0):
    """Paired devices first, then anything advertising the Studio service."""
    found = find_paired_ble()
    known = {a for a, _ in found}

    from bleak import BleakScanner

    async def scan():
        seen = await BleakScanner.discover(timeout=timeout, return_adv=True)
        extra = []
        for addr, (dev, adv) in seen.items():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if BLE_SERVICE_UUID in uuids and addr.upper() not in known:
                extra.append((addr.upper(), dev.name or adv.local_name or addr))
        return extra

    loop = asyncio.new_event_loop()
    try:
        found += loop.run_until_complete(scan())
    except Exception:
        pass
    finally:
        loop.close()
    return found


# -- connection -------------------------------------------------------------

class Connection:
    def __init__(self, transport, timeout: float = 5.0):
        self.transport = transport
        self.timeout = timeout
        self._responses = {}
        self._lock = threading.Lock()
        self._events = {}
        self._notifications = queue.Queue()
        self._next_id = 1
        self._deframer = _Deframer(self._dispatch)
        transport.start(self._deframer.feed)

    @classmethod
    def open_serial(cls, port: str, **kw):
        return cls(SerialTransport(port), **kw)

    @classmethod
    def open_ble(cls, address: str, label: str = None, **kw):
        return cls(BleTransport(address, label), **kw)

    @property
    def kind(self):
        return self.transport.name

    def describe(self):
        return self.transport.describe()

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
            with self._lock:
                self._responses[rid] = resp.request_response
                event = self._events.get(rid)
            if event:
                event.set()

    def call(self, **subsystem):
        """Send one Request and wait for its matching RequestResponse."""
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            event = threading.Event()
            self._events[rid] = event

        req = studio_pb2.Request(request_id=rid, **subsystem)
        frame = bytes([SOF]) + _escape(req.SerializeToString()) + bytes([EOF])
        self.transport.write(frame)

        if not event.wait(self.timeout):
            with self._lock:
                self._events.pop(rid, None)
            raise RpcError("응답 시간 초과 - 키보드 연결을 확인하세요.")

        with self._lock:
            self._events.pop(rid, None)
            rr = self._responses.pop(rid)

        if rr.WhichOneof("subsystem") == "meta":
            meta = rr.meta
            if meta.WhichOneof("response_type") == "simple_error":
                name = {
                    1: "잠겨 있습니다 - 키보드에서 Studio Unlock 키를 누르세요",
                    2: "RPC_NOT_FOUND",
                    3: "MSG_DECODE_FAILED",
                    4: "MSG_ENCODE_FAILED",
                }.get(meta.simple_error, "오류 %d" % meta.simple_error)
                raise RpcError(name)
        return rr

    def notifications(self):
        while True:
            try:
                yield self._notifications.get_nowait()
            except queue.Empty:
                return

    def close(self):
        self.transport.close()

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
