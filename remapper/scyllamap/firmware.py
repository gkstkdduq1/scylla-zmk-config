"""Fetch published firmware and write it to the bootloader drive.

Actions artifacts need a token even on a public repo, so the build workflow
publishes the .uf2 files as a GitHub release instead - plain public HTTPS that
this app can fetch with no credentials.

The one step that cannot be automated is entering the bootloader. The Studio RPC
has no reboot request, and the Adafruit nRF52 bootloader does not implement the
1200-baud-touch reset trick, so something has to put the board there. The
keymap's &bootloader key does it without reaching for the reset button.
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.request

REPO = "gkstkdduq1/scylla-zmk-config"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO

# Which local file each half wants.
ASSET_FOR = {
    "left": "scylla_left_studio.uf2",
    "right": "scylla_right.uf2",
    "reset": "settings_reset.uf2",
}

BOOTLOADER_VOLUMES = ("NICENANO", "NANOBOOT", "NRF52BOOT")


class FirmwareError(RuntimeError):
    pass


def latest_release(timeout: float = 15.0):
    """-> dict with tag, published, and {name: url} assets."""
    req = urllib.request.Request(
        API_LATEST, headers={"Accept": "application/vnd.github+json",
                             "User-Agent": "scylla-remapper"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise FirmwareError("릴리스 정보를 가져오지 못했습니다: %s" % exc)
    assets = {a["name"]: a["browser_download_url"]
              for a in data.get("assets", [])}
    if not assets:
        raise FirmwareError("릴리스에 펌웨어 파일이 없습니다.")
    return {
        "tag": data.get("tag_name", "?"),
        "published": data.get("published_at", ""),
        "assets": assets,
    }


def download(url: str, dest: str, timeout: float = 60.0):
    req = urllib.request.Request(url, headers={"User-Agent": "scylla-remapper"})
    tmp = dest + ".part"
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    os.replace(tmp, dest)
    return dest


def local_version_file(firmware_dir: str) -> str:
    return os.path.join(firmware_dir, "RELEASE.txt")


def local_version(firmware_dir: str):
    try:
        with open(local_version_file(firmware_dir), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def sync(firmware_dir: str, release=None, progress=None):
    """Download every asset of the latest release into firmware_dir."""
    release = release or latest_release()
    os.makedirs(firmware_dir, exist_ok=True)
    for i, (name, url) in enumerate(sorted(release["assets"].items()), 1):
        if progress:
            progress("내려받는 중 %d/%d: %s" % (i, len(release["assets"]), name))
        download(url, os.path.join(firmware_dir, name))
    with open(local_version_file(firmware_dir), "w", encoding="utf-8") as fh:
        fh.write(release["tag"])
    return release


def find_bootloader_drive():
    """-> drive letter of a mounted nRF52 bootloader, or None."""
    ps = ("Get-CimInstance Win32_LogicalDisk |"
          " Where-Object { $_.DriveType -eq 2 } |"
          " ForEach-Object { $_.DeviceID + '|' + $_.VolumeName }")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return None
    for line in (proc.stdout or "").splitlines():
        device, _, volume = line.strip().partition("|")
        if not re.fullmatch(r"[A-Za-z]:", device):
            continue
        if any(v in volume.upper() for v in BOOTLOADER_VOLUMES):
            return device
    return None


def wait_for_bootloader(timeout: float = 90.0, poll: float = 0.7, cancel=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel and cancel():
            return None
        drive = find_bootloader_drive()
        if drive:
            return drive
        time.sleep(poll)
    return None


def flash(firmware_dir: str, half: str, drive: str):
    name = ASSET_FOR[half]
    src = os.path.join(firmware_dir, name)
    if not os.path.exists(src):
        raise FirmwareError("펌웨어 파일이 없습니다: %s" % src)
    try:
        shutil.copy(src, drive + "\\")
    except OSError:
        # The board reboots the moment the write completes, so the copy call
        # usually reports an error even though the flash succeeded.
        pass
    return name
