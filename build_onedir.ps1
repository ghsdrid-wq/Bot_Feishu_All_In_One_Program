$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

$PythonExe = (Get-Command python).Source
$PythonBase = Split-Path -Parent $PythonExe
$DllDir = Join-Path $PythonBase "DLLs"
$TclDir = Join-Path $PythonBase "tcl"
$TclDataDir = Join-Path $TclDir "tcl8.6"
$TkDataDir = Join-Path $TclDir "tk8.6"

$TkinterPyd = Join-Path $DllDir "_tkinter.pyd"
$TclDll = Join-Path $DllDir "tcl86t.dll"
$TkDll = Join-Path $DllDir "tk86t.dll"

foreach ($RequiredPath in @($TkinterPyd, $TclDll, $TkDll, $TclDataDir, $TkDataDir)) {
    if (-not (Test-Path $RequiredPath)) {
        throw "Missing required Tk/Tcl file: $RequiredPath"
    }
}

python -c "import tkinter, _tkinter; print('tkinter ok')"
python -m PyInstaller --version

$Args = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", "AutoReportFeishu",
    "--add-data", "config.ini;.",
    "--add-data", "$TclDataDir;_tcl_data",
    "--add-data", "$TkDataDir;_tk_data",
    "--add-binary", "$TkinterPyd;.",
    "--add-binary", "$TclDll;.",
    "--add-binary", "$TkDll;.",
    "--collect-data", "customtkinter",
    "--collect-data", "tkcalendar",
    "--collect-submodules", "win32com",
    "--hidden-import", "_tkinter",
    "--hidden-import", "babel.numbers",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes",
    "bot_main.py"
)

python -m PyInstaller @Args

$DistDir = Join-Path $ProjectDir "dist\AutoReportFeishu"
Copy-Item -LiteralPath (Join-Path $ProjectDir "config.ini") -Destination (Join-Path $DistDir "config.ini") -Force

$ZipPath = Join-Path $ProjectDir "dist\AutoReportFeishu_onedir.zip"
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path $DistDir -DestinationPath $ZipPath -CompressionLevel Optimal

Write-Host ""
Write-Host "Build complete."
Write-Host "Folder: $DistDir"
Write-Host "EXE:    $(Join-Path $DistDir 'AutoReportFeishu.exe')"
Write-Host "ZIP:    $ZipPath"
