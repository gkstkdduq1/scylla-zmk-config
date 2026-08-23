"""Register the app to launch at sign-in.

Uses a shortcut in the user's Startup folder rather than a registry Run key:
it needs no elevation, and the user can see and delete it in Explorer or turn
it off in Task Manager's Startup tab like any other program.
"""

import os
import subprocess
import sys

SHORTCUT_NAME = "Scylla Remapper.lnk"


def startup_dir() -> str:
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def shortcut_path() -> str:
    return os.path.join(startup_dir(), SHORTCUT_NAME)


def _launcher():
    """(exe, args) that starts this app with no console window."""
    app_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return pythonw, '"%s" --tray' % app_py


def is_enabled() -> bool:
    return os.path.exists(shortcut_path())


def enable():
    exe, args = _launcher()
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "$s.TargetPath = '%s';"
        "$s.Arguments = '%s';"
        "$s.WorkingDirectory = '%s';"
        "%s"
        "$s.Description = 'Scylla keyboard remapper';"
        "$s.Save()"
    ) % (
        shortcut_path().replace("'", "''"),
        exe.replace("'", "''"),
        args.replace("'", "''"),
        os.path.dirname(os.path.abspath(__file__)).replace("'", "''"),
        ("$s.IconLocation = '%s';" % icon.replace("'", "''"))
        if os.path.exists(icon) else "",
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def disable():
    try:
        os.remove(shortcut_path())
    except FileNotFoundError:
        pass


def toggle() -> bool:
    if is_enabled():
        disable()
    else:
        enable()
    return is_enabled()
