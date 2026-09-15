<#
    flash.ps1 - wait for the nice!nano bootloader drive and drop a .uf2 on it.

    Usage:
        .\flash.ps1 left
        .\flash.ps1 right
        .\flash.ps1 reset
        .\flash.ps1 left -Force    # write even if the serial check objects

    Then double-tap the reset button on that half. The script does the rest.

    Why the serial check: both halves expose the same NICENANO drive, so
    "write to whatever shows up" flashes whichever board happens to be plugged
    in. That went wrong twice - settings_reset.uf2 landed on the right half and
    it stopped typing, and the left half was left without working firmware,
    which read as a Bluetooth problem for days. Each nRF52 reports a stable USB
    serial, so the two can be told apart before anything is written.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('left', 'right', 'reset')]
    [string]$Half,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$dir = Join-Path $PSScriptRoot 'firmware'

# USB serial -> half. To read a board's serial, put it in the bootloader and run:
#   Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_239A' }
# The serial is the last backslash-separated field of the InstanceId.
$serials = @{
    'BFD689AFCDC0A442' = 'left'
    'EE8AB62FD01A3B3D' = 'right'
}

$file = switch ($Half) {
    'left'  { 'scylla_left_studio.uf2' }
    'right' { 'scylla_right.uf2' }
    'reset' { 'settings_reset.uf2' }
}
$src = Join-Path $dir $file
if (-not (Test-Path $src)) { throw "Not found: $src" }

Write-Host ""
Write-Host "  Flashing : $file" -ForegroundColor Cyan
Write-Host "  Target   : $Half half" -ForegroundColor Cyan
Write-Host ""
Write-Host "  >> Double-tap the reset button on the $Half half now." -ForegroundColor Yellow
Write-Host "     (two quick presses, under about half a second apart)"
Write-Host ""
Write-Host "  Waiting for the bootloader drive" -NoNewline

$drive = $null
for ($i = 0; $i -lt 180; $i++) {
    $candidates = Get-CimInstance Win32_LogicalDisk |
        Where-Object { $_.DriveType -eq 2 -and $_.VolumeName -match 'NICENANO|NANOBOOT|NRF52BOOT' }
    if ($candidates) { $drive = $candidates[0]; break }
    Start-Sleep -Milliseconds 500
    if ($i % 4 -eq 0) { Write-Host "." -NoNewline }
}
Write-Host ""

if (-not $drive) {
    Write-Host ""
    Write-Host "  No bootloader drive appeared after 90 s." -ForegroundColor Red
    Write-Host "  Check: is the USB cable a data cable (not charge-only)?"
    Write-Host "  Check: did the double-tap register? Try again, slightly faster."
    exit 1
}

Write-Host "  Found    : $($drive.DeviceID) [$($drive.VolumeName)]" -ForegroundColor Green

# Identify the board before writing. The bootloader enumerates as a composite
# device whose InstanceId ends in the chip serial; its MI_* children carry an
# interface suffix instead, so skip anything carrying &MI_. Matching with
# -like rather than -match keeps the backslashes literal and needs no escaping,
# which is what broke the first version of this check.
$boot = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\VID_239A&PID_*' -and
                    $_.InstanceId -notlike '*&MI_*' })

function Stop-Unless-Forced($message) {
    Write-Host ""
    Write-Host "  STOP: $message" -ForegroundColor Red
    if ($Force) {
        Write-Host "  -Force given, writing anyway." -ForegroundColor Yellow
        return
    }
    Write-Host "  Nothing was written. Re-run with -Force to override." -ForegroundColor Red
    exit 1
}

if ($boot.Count -gt 1) {
    Stop-Unless-Forced "both halves are in the bootloader at once, so the target is ambiguous. Unplug one."
} elseif ($boot.Count -eq 0) {
    Stop-Unless-Forced "the drive is there but no bootloader USB device is, so the serial cannot be read."
} else {
    $serial = $boot[0].InstanceId.Split('\')[-1]
    if ($serials.ContainsKey($serial)) {
        $actual = $serials[$serial]
        Write-Host "  Board    : $actual half (serial $serial)" -ForegroundColor Green
        if ($Half -eq 'reset') {
            Write-Host "  settings_reset wipes bonds and belongs on BOTH halves." -ForegroundColor Yellow
        } elseif ($actual -ne $Half) {
            Stop-Unless-Forced "you asked for the $Half half, but the $actual half is plugged in."
        }
    } else {
        Write-Host "  Board    : unknown serial $serial" -ForegroundColor Yellow
        Stop-Unless-Forced "that serial is not in the table at the top of this script. Add it if the board is new."
    }
}

# The board reboots the instant the write finishes, so the copy often reports
# an error even though it succeeded. That is expected - ignore it.
try {
    Copy-Item -Path $src -Destination "$($drive.DeviceID)\" -Force
} catch {
    Write-Host "  (copy reported '$($_.Exception.Message.Trim())' - normal, the board rebooted)" -ForegroundColor DarkGray
}

# A board that boots its application drops the bootloader and comes back as
# ZMK (VID_1D50). If it does not, the image did not take and the half is dead
# until it is flashed again - which is exactly the failure that went unnoticed
# before, so say so rather than reporting success.
Write-Host "  Verifying" -NoNewline
$app = $null
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    Write-Host "." -NoNewline
    $app = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId -like 'USB\VID_1D50&PID_*' -and
                        $_.InstanceId -notlike '*&MI_*' }
    if ($app) { break }
}
Write-Host ""
Write-Host ""

if ($app) {
    Write-Host "  Done. The $Half half rebooted into $file." -ForegroundColor Green
} else {
    Write-Host "  Wrote $file, but the board never came back as a ZMK device." -ForegroundColor Red
    Write-Host "  It is probably still in the bootloader. Check the .uf2 and flash again." -ForegroundColor Red
}
Write-Host ""
