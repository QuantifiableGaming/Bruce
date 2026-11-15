# Creates icon shortcuts for the Valorant coach in the coach folder and on the desktop.
# A .bat file cannot have its own icon in Windows. A shortcut (.lnk) can.
# Use -FolderOnly to skip the desktop.
param([switch]$FolderOnly)
$here = $PSScriptRoot
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell

$items = @(
    @{ Name = "Valorant Coach";       Target = "Start Coach.bat"; Icon = "icons\coach.ico"; Note = "Start the Valorant coach" },
    @{ Name = "Valorant Coach Setup"; Target = "Coach Setup.bat"; Icon = "icons\setup.ico"; Note = "Open the Valorant Coach Control Panel" }
)

$places = @($here)
if (-not $FolderOnly -and $desktop -and ($desktop.TrimEnd('\') -ne $here.TrimEnd('\'))) { $places += $desktop }

foreach ($place in $places) {
    foreach ($item in $items) {
        $path = Join-Path $place ($item.Name + ".lnk")
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = Join-Path $here $item.Target
        $link.WorkingDirectory = $here
        $link.IconLocation = (Join-Path $here $item.Icon) + ",0"
        $link.Description = $item.Note
        $link.Save()
        Write-Host "Created: $path"
    }
}

# Ask Windows Explorer to refresh icons, so the new icons show at once.
try {
    Start-Process -FilePath "ie4uinit.exe" -ArgumentList "-show" -WindowStyle Hidden -ErrorAction Stop
} catch { }

Write-Host ""
Write-Host "Use the shortcuts with the crosshair and gear icons to start the coach and the setup."
