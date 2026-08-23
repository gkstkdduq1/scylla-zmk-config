# Start hidden in the notification area (same thing the Startup shortcut runs).
$py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python.exe).Source }
Start-Process -FilePath $py -ArgumentList "`"$PSScriptRoot\scyllamap\app.py`"", "--tray"
