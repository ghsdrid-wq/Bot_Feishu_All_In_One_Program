# build_onedir.ps1 — สร้าง exe แบบ onedir / windowed / ไม่มี console
#
# ผลลัพธ์คือโฟลเดอร์เดียวที่ก๊อปไปวางเครื่องไหนก็รันได้ ไม่ต้องลง Python
# ไฟล์ที่ผู้ใช้ต้องแก้เอง (config.ini, metrics_config.yaml) และฐานข้อมูล
# store.db อยู่ข้าง ๆ exe ไม่ได้อยู่ใน _internal ซึ่งถูกเขียนทับตอนลงใหม่
#
# เครื่องที่ build ต้องมี Python 3.14 + PyInstaller + pip install -r requirements.txt

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

    # ---- Tk/Tcl (customtkinter ใช้) ----
    "--add-data", "$TclDataDir;_tcl_data",
    "--add-data", "$TkDataDir;_tk_data",
    "--add-binary", "$TkinterPyd;.",
    "--add-binary", "$TclDll;.",
    "--add-binary", "$TkDll;.",
    "--hidden-import", "_tkinter",
    "--collect-data", "customtkinter",
    "--collect-data", "tkcalendar",
    "--hidden-import", "babel.numbers",

    # ---- Excel COM ----
    "--collect-submodules", "win32com",
    "--hidden-import", "pythoncom",
    "--hidden-import", "pywintypes",

    # ---- ชั้น dashboard / metrics ----
    # เทมเพลต Jinja กับ schema.sql ถูกอ่านจากดิสก์ตอนรัน การไล่ import
    # มองไม่เห็น ต้องบอกเอง
    "--add-data", "dashboard/templates;dashboard/templates",
    "--add-data", "metrics/schema.sql;metrics",
    "--collect-submodules", "dashboard",
    "--collect-submodules", "metrics",
    "--collect-submodules", "core",
    "--hidden-import", "card_report",

    # ---- เว็บ dashboard ----
    # uvicorn โหลดโมดูลด้วยชื่อเป็นสตริง การไล่ import จึงไม่เจอ
    "--collect-submodules", "uvicorn",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.lifespan.on",

    # ---- แคปรูป ----
    # playwright มี node driver ติดมาด้วย ต้อง collect-all ไม่ใช่แค่ submodules
    "--collect-all", "playwright",

    "bot_main.py"
)

# PyInstaller เขียน progress ลง stderr ซึ่ง PowerShell ถือว่าเป็น error
# ถ้าปล่อยให้ ErrorActionPreference=Stop ทำงาน จะหยุดตั้งแต่บรรทัดแรก
# ทั้งที่ build ยังไม่ได้เริ่ม จึงดูผลจาก exit code แทน
$ErrorActionPreference = "Continue"
python -m PyInstaller @Args
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
$ErrorActionPreference = "Stop"

$DistDir = Join-Path $ProjectDir "dist\AutoReportFeishu"

# ---- ไฟล์ที่ผู้ใช้แก้เอง วางข้าง exe ----
# ไม่ใส่เข้า _internal เพราะจะถูกเขียนทับทุกครั้งที่ลงใหม่ และผู้ใช้หาไม่เจอ
foreach ($Name in @("config.ini", "metrics_config.yaml")) {
    $Src = Join-Path $ProjectDir $Name
    if (Test-Path $Src) {
        Copy-Item -LiteralPath $Src -Destination (Join-Path $DistDir $Name) -Force
    }
}

# ---- browser ของ Playwright ----
# เครื่องปลายทางไม่ได้ลง playwright ไว้ ถ้าไม่ก๊อปไปด้วยจะแคปรูป dashboard ไม่ได้
# render.py จะเห็นโฟลเดอร์นี้แล้วชี้ PLAYWRIGHT_BROWSERS_PATH มาที่นี่เอง
$BrowserSrc = Join-Path $env:LOCALAPPDATA "ms-playwright"
if (Test-Path $BrowserSrc) {
    Write-Host "Copying Playwright browsers (ใช้เวลาสักครู่)..."
    Copy-Item -LiteralPath $BrowserSrc -Destination (Join-Path $DistDir "ms-playwright") -Recurse -Force
} else {
    Write-Warning "ไม่พบ $BrowserSrc - เครื่องปลายทางจะแคปรูป dashboard ไม่ได้"
    Write-Warning "แก้โดยรัน: python -m playwright install chromium แล้ว build ใหม่"
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Folder: $DistDir"
Write-Host "EXE:    $(Join-Path $DistDir 'AutoReportFeishu.exe')"
