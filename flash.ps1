<#
    flash.ps1 — wait for the nice!nano bootloader drive and drop a .uf2 on it.

    Usage:
        .\flash.ps1 left
        .\flash.ps1 right
        .\flash.ps1 reset

    Then double-tap the reset button on that half. The script does the rest.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('left', 'right', 'reset')]
    [string]$Half
)

$ErrorActionPreference = 'Stop'
$dir = Join-Path $PSScriptRoot 'firmware'

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

Write-Host "  Found $($drive.DeviceID) [$($drive.VolumeName)]" -ForegroundColor Green

# The board reboots the instant the write finishes, so the copy often reports
# an error even though it succeeded. That is expected — ignore it.
try {
    Copy-Item -Path $src -Destination "$($drive.DeviceID)\" -Force
} catch {
    Write-Host "  (copy reported '$($_.Exception.Message.Trim())' — normal, the board rebooted)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Done. The $Half half is rebooting with $file." -ForegroundColor Green
Write-Host ""
