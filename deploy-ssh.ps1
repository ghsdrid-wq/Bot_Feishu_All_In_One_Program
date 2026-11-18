# ส่ง Auto Report (Web UI) เวอร์ชันใน app_version.py ไปวางบนเครื่องรันผ่าน SSH (ใช้ SSH key ไม่ต้องใช้รหัสผ่าน)
#
#   powershell -ExecutionPolicy Bypass -File deploy-ssh.ps1                  วางเวอร์ชันใหม่ไว้ข้างตัวเก่า (ไม่แตะตัวที่รันอยู่)
#   powershell -ExecutionPolicy Bypass -File deploy-ssh.ps1 -Build           build (build_webui.ps1) ก่อนแล้ววาง
#   powershell -ExecutionPolicy Bypass -File deploy-ssh.ps1 -Switch          วาง + ปิดตัวเก่า + เปิดตัวใหม่บนหน้าจอผู้ใช้
#
# - วางที่ C:\0Niphonk\Programs\AutoReportWebUIV<เวอร์ชัน>\AutoReportWebUI.exe (โฟลเดอร์ละเวอร์ชัน)
# - ตัวที่รันอยู่ = AutoReportWebUI.exe หรือโปรแกรมเดิม AutoReportFeishu.exe (Tk) ก็ได้ สลับจากตัวเดิมมา Web UI ได้เลย
# - ไฟล์ของผู้ใช้ (config.ini, metrics_config.yaml, jms_user_policy.json, store.db, sent_messages.json,
#   vip_awb.db = ข้อมูลสะสมของ VIP AWB ถ้าไม่ติดไป รอบแรกของเวอร์ชันใหม่ต้องดึงย้อนหลังใหม่ ~30 นาที)
#   ไม่ส่งจากเครื่องนี้ แต่คัดลอกจากโฟลเดอร์ที่รันอยู่บนเครื่องรัน ตอน -Switch คัดลอกซ้ำหลังปิดตัวเก่า ให้ได้ข้อมูลล่าสุด
# - -Switch ไม่ยอมปิดโปรแกรมช่วงนาทีที่รอบรายงานรายชั่วโมงกำลังทำงาน ([TIME] run_minute ถึง +15 นาที) ใช้ -Force ข้ามได้
# - Web UI จำสถานะ Auto ไว้ใน [WEBUI] auto_on เปิดใหม่แล้ว Auto เปิดต่อเอง
#   สลับจากโปรแกรมเดิม (ไม่จำ Auto) config ยังไม่มี [WEBUI]: ใส่ auto_on = true ให้ (ใช้ -NoAuto ถ้าไม่ต้องการ)
# - เวอร์ชันเดียวกับที่รันอยู่: ต้องใช้ -Switch (ปิดก่อน แล้วเปลี่ยนเฉพาะไฟล์โปรแกรม เก็บของเดิมไว้ใน _previous\)
# - -Switch เปิดตัวใหม่ด้วยบัญชีเจ้าของโปรแกรมที่รันอยู่ (หรือผู้ใช้หน้าจอ) หาไม่ได้ = หยุดก่อนปิดตัวเก่า
#   ตัวใหม่ไม่ตอบที่พอร์ต 6300 ภายใน 90 วินาที = ปิดตัวใหม่แล้วเปิดตัวเดิมกลับ (เวอร์ชันเดียวกัน = คืนไฟล์จาก _previous\)
#   สำเร็จ = สร้าง/อัปเดต shortcut "Auto Report Web UI" บน Desktop ของผู้ใช้ให้ชี้ตัวใหม่
# - ทุกครั้งรีเซ็ตสิทธิ์โฟลเดอร์ปลายทางให้รับจาก Programs (icacls /reset) ไม่งั้นโปรแกรมเซฟ config.ini ไม่ได้
# - รันจาก PowerShell เท่านั้น (tar ของ Git Bash ใช้ path แบบ C:\ ไม่ได้)
param(
    [string]$SshHost = 'runpc',
    [string]$RemoteRoot = 'C:\0Niphonk\Programs',
    [string]$Target,          # ชื่อโฟลเดอร์ปลายทาง (ค่าเริ่มต้น AutoReportWebUIV<เวอร์ชัน>) ใช้ทดสอบได้
    [switch]$Build,
    [switch]$Switch,
    [switch]$Force,
    [switch]$NoAuto           # สลับจากโปรแกรมเดิม: ไม่ใส่ [WEBUI] auto_on = true ให้
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if ($Build) {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'build_webui.ps1')
    if ($LASTEXITCODE) { Write-Host 'build ไม่สำเร็จ' -ForegroundColor Red; exit 1 }
}
Push-Location $root
try { $version = (& python -c "from app_version import APP_VERSION; print(APP_VERSION)").Trim() } finally { Pop-Location }
if ($version -notmatch '^\d+\.\d+\.\d+$') { Write-Host "อ่านเวอร์ชันจาก app_version.py ไม่ได้: '$version'" -ForegroundColor Red; exit 1 }
$folder = 'AutoReportWebUIV' + $version.Replace('.', '-')
if (-not $Target) { $Target = $folder }
$local = Join-Path $root "dist\$folder"
if (-not (Test-Path (Join-Path $local 'AutoReportWebUI.exe'))) {
    Write-Host "ไม่พบ $local\AutoReportWebUI.exe — build ก่อน (ใส่ -Build)" -ForegroundColor Red; exit 1
}
if (-not (Get-ChildItem -LiteralPath $local -Recurse -Filter 'index.html' | Where-Object { $_.DirectoryName -like '*webui\static' })) {
    Write-Host "ไม่พบหน้าเว็บ (webui\static\index.html) ใน $local — build ใหม่ (ใส่ -Build)" -ForegroundColor Red; exit 1
}

# ---------- แพ็กเฉพาะไฟล์โปรแกรม ----------
# SQLite (store.db, vip_awb.db) ต้องไปพร้อม -wal/-shm: ปิดโปรแกรมด้วย Stop-Process ข้อมูลล่าสุดยังค้างใน -wal
$operatorFiles = @('config.ini', 'metrics_config.yaml', 'jms_user_policy.json', 'sent_messages.json',
                   'store.db', 'store.db-wal', 'store.db-shm', 'vip_awb.db', 'vip_awb.db-wal', 'vip_awb.db-shm')
$pkg = Join-Path $env:TEMP "webui-deploy-$([guid]::NewGuid().ToString('N')).tar"
$excludes = @($operatorFiles + 'logs' + 'VipAwb' | ForEach-Object { "--exclude=./$_" })
Write-Host "แพ็ก $folder ..."
# cd เข้าไปก่อน (tar ของ Windows รับ path ภาษาไทยใน -C ได้ไม่แน่นอน)
Push-Location $local
try { tar -cf $pkg @excludes . } finally { Pop-Location }
if ($LASTEXITCODE) { Write-Host 'แพ็กไฟล์ไม่สำเร็จ' -ForegroundColor Red; exit 1 }
Write-Host ("ขนาด {0:N0} MB" -f ((Get-Item $pkg).Length / 1MB))

# ---------- สคริปต์ที่รันบนเครื่องรัน ----------
$remoteScript = @'
param([string]$Pkg, [string]$RemoteRoot, [string]$Target, [string]$OperatorFiles, [int]$Switch, [int]$Force, [int]$NoAuto)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$operator = $OperatorFiles -split ','
$dest = Join-Path $RemoteRoot $Target
$exeName = 'AutoReportWebUI.exe'
$exe = Join-Path $dest $exeName

# running app: the Web UI first, else the old Tk program
$proc = Get-Process -Name AutoReportWebUI -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $proc) { $proc = Get-Process -Name AutoReportFeishu -ErrorAction SilentlyContinue | Select-Object -First 1 }
$oldExe = if ($proc) { $proc.Path } else { $null }
$cur = if ($oldExe) { Split-Path -Parent $oldExe } else { $null }
$same = $cur -and ((Resolve-Path $cur).Path.TrimEnd('\') -ieq $dest.TrimEnd('\'))
$fromOld = $proc -and ($proc.ProcessName -ieq 'AutoReportFeishu')
Write-Host "Running now : $(if ($oldExe) { $oldExe } else { '(not running)' })"
Write-Host "Target      : $dest"

if ($same -and -not $Switch) {
    Write-Host 'This version is the one running now. Use -Switch to stop it and replace its program files.' -ForegroundColor Red
    exit 1
}

function Get-RunMinute([string]$dir) {
    $m = 5
    $ini = Join-Path $dir 'config.ini'
    if (Test-Path $ini) {
        $sec = ''
        foreach ($line in Get-Content $ini -Encoding UTF8) {
            if ($line -match '^\s*\[(.+)\]') { $sec = $Matches[1].Trim(); continue }
            if ($sec -eq 'TIME' -and $line -match '^\s*run_minute\s*=\s*(\d+)') { $m = [int]$Matches[1] }
        }
    }
    return $m
}

if ($Switch -and $cur -and -not $Force) {
    $runMinute = Get-RunMinute $cur
    $now = (Get-Date).Minute
    $since = ($now - $runMinute + 60) % 60
    if ($since -le 15) {
        Write-Host "Hourly report window ($('{0:00}' -f $runMinute)-$('{0:00}' -f (($runMinute + 15) % 60)) past the hour). Try again later or use -Force." -ForegroundColor Red
        exit 1
    }
}

# ---------- desktop user (resolve BEFORE stopping anything) ----------
# The SSH session's $env:USERDOMAIN\$env:USERNAME is not a mappable account here (deploy 18/11/2026:
# "No mapping between account names and security IDs" after the old version was already stopped).
# Use the owner of the running app, else the console user, and check it maps to a SID first.
function Get-DesktopUser {
    $candidates = @()
    if ($proc) {
        $o = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.Id)" | Invoke-CimMethod -MethodName GetOwner
        if ($o.User) { $candidates += "$($o.Domain)\$($o.User)" }
    }
    $console = (Get-CimInstance Win32_ComputerSystem).UserName
    if ($console) { $candidates += $console }
    foreach ($e in Get-CimInstance Win32_Process -Filter "Name='explorer.exe'") {
        $o = $e | Invoke-CimMethod -MethodName GetOwner
        if ($o.User) { $candidates += "$($o.Domain)\$($o.User)" }
    }
    foreach ($c in $candidates) {
        try {
            $sid = ([Security.Principal.NTAccount]$c).Translate([Security.Principal.SecurityIdentifier])
            return [pscustomobject]@{ Name = $c; Sid = $sid.Value }
        } catch {}
    }
    return $null
}
$desktopUser = $null
if ($Switch) {
    $desktopUser = Get-DesktopUser
    if (-not $desktopUser) {
        Write-Host 'Cannot find the logged-on desktop user to start the new version. Nothing was stopped.' -ForegroundColor Red
        exit 1
    }
    Write-Host "Desktop user: $($desktopUser.Name)"
}

# ---------- unpack ----------
$work = Join-Path (Split-Path $Pkg) 'x'
New-Item -ItemType Directory -Force -Path $work | Out-Null
tar -xf $Pkg -C $work
if ($LASTEXITCODE) { throw 'tar extract failed' }
if (-not (Test-Path (Join-Path $work $exeName))) { throw "Package has no $exeName" }

function Copy-Operator([string]$from, [string]$to, [bool]$overwrite) {
    if (-not $from -or ($from.TrimEnd('\') -ieq $to.TrimEnd('\'))) { return }
    # SQLite: before a .db is (re)copied, drop the target's -wal/-shm. A -wal left from an earlier copy
    # (staged while the old version was still writing) would otherwise be replayed onto the newer .db.
    foreach ($db in ($operator | Where-Object { $_ -like '*.db' })) {
        if ((Test-Path (Join-Path $from $db)) -and ($overwrite -or -not (Test-Path (Join-Path $to $db)))) {
            foreach ($sfx in '-wal', '-shm') {
                Remove-Item -LiteralPath (Join-Path $to "$db$sfx") -Force -ErrorAction SilentlyContinue
            }
        }
    }
    foreach ($n in $operator) {
        $s = Join-Path $from $n; $d = Join-Path $to $n
        if ((Test-Path $s) -and ($overwrite -or -not (Test-Path $d))) {
            Copy-Item -LiteralPath $s -Destination $d -Force
            Write-Host "  carried $n from $(Split-Path -Leaf $from)"
        }
    }
}

# The old Tk program does not remember Auto. Its config has no [WEBUI] section, so the Web UI would
# open with Auto off and the hourly report would stop until someone pressed Start Auto.
function Set-AutoOn([string]$dir) {
    $ini = Join-Path $dir 'config.ini'
    if (-not (Test-Path $ini)) { return }
    $text = [IO.File]::ReadAllText($ini)
    if ($text -match '(?m)^\s*\[WEBUI\]') { return }
    $nl = if ($text.EndsWith("`n")) { '' } else { "`r`n" }
    [IO.File]::AppendAllText($ini, "$nl`r`n[WEBUI]`r`nauto_on = true`r`n", [Text.UTF8Encoding]::new($false))
    Write-Host '  config.ini: added [WEBUI] auto_on = true (Auto starts by itself)'
}

function Stop-ById([int]$id, [string]$what) {
    Write-Host "Stopping $what (PID $id)..."
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30 -and (Get-Process -Id $id -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) { throw "$what did not stop." }
}
function Stop-App { if ($proc) { Stop-ById $proc.Id $proc.ProcessName } }

# start on the logged-on user's desktop (SSH runs in session 0: use a one-shot interactive task)
function Start-OnDesktop([string]$path) {
    $taskName = 'AutoReportWebUI-deploy-start'
    $action = New-ScheduledTaskAction -Execute $path -WorkingDirectory (Split-Path -Parent $path)
    $principal = New-ScheduledTaskPrincipal -UserId $desktopUser.Name -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Start-Sleep -Seconds 3
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$programItems = Get-ChildItem -LiteralPath $work -Force
$prev = Join-Path $dest '_previous'
if ($same) {
    Stop-App
    Remove-Item -LiteralPath $prev -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $prev | Out-Null
    foreach ($it in $programItems) {
        $d = Join-Path $dest $it.Name
        if (Test-Path -LiteralPath $d) { Move-Item -LiteralPath $d -Destination (Join-Path $prev $it.Name) }
        Move-Item -LiteralPath $it.FullName -Destination $d
    }
    Write-Host "Replaced program files in place (old ones in _previous\)."
} else {
    if (Test-Path -LiteralPath $dest) {
        # existing folder of this version that is not running: replace program files, keep its operator files
        foreach ($it in $programItems) {
            $d = Join-Path $dest $it.Name
            if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }
            Move-Item -LiteralPath $it.FullName -Destination $d
        }
        Write-Host 'Updated program files in the existing folder.'
    } else {
        Move-Item -LiteralPath $work -Destination $dest
        Write-Host 'Created new version folder.'
    }
    Copy-Operator $cur $dest $false
    if ($Switch) {
        Stop-App
        Copy-Operator $cur $dest $true   # latest config / store.db / vip_awb.db / sent_messages after the old one stopped
    }
}
if ($fromOld -and -not $NoAuto) { Set-AutoOn $dest }

# The package is unpacked under C:\ProgramData and moved here, so it keeps that ACL (Users: read only)
# and the app could not save config.ini ("Access is denied", deploy 18/11/2026).
# Reset to inherit from the Programs folder like the older versions (Authenticated Users: Modify).
icacls $dest /reset /T /Q | Out-Null
if ($LASTEXITCODE) { Write-Host "Warning: could not reset permissions of $dest (icacls exit $LASTEXITCODE)" -ForegroundColor Yellow }

if (-not $Switch) {
    Write-Host ''
    Write-Host "Staged. The running version was not touched. Run deploy-ssh.ps1 -Switch to switch (or close the old one and open $exe)." -ForegroundColor Green
    exit 0
}

# ---------- start the new version and wait for the Web UI ----------
function Wait-WebUI([string]$path, [int]$seconds) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt $seconds) {
        Start-Sleep -Seconds 3
        $p = Get-Process -Name AutoReportWebUI -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $path } | Select-Object -First 1
        if (-not $p) { continue }
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:6300/api/engine' -UseBasicParsing -TimeoutSec 3
            if ($r.StatusCode -eq 200) { return $p }
        } catch {}
    }
    return $null
}

Start-OnDesktop $exe
$p = Wait-WebUI $exe 90
if (-not $p) {
    Write-Host "New version did not answer on port 6300 within 90 seconds (see $dest\logs\webui_startup.log)." -ForegroundColor Red
    # ---------- roll back to the version that was running ----------
    Get-Process -Name AutoReportWebUI -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $exe } |
        ForEach-Object { Stop-ById $_.Id 'new version' }
    if ($oldExe) {
        if ($same) {
            foreach ($it in Get-ChildItem -LiteralPath $prev -Force) {
                $d = Join-Path $dest $it.Name
                if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }
                Move-Item -LiteralPath $it.FullName -Destination $d
            }
            Write-Host 'Restored the old program files from _previous\.'
        }
        Write-Host "Starting the previous version again: $oldExe" -ForegroundColor Yellow
        Start-OnDesktop $oldExe
        Start-Sleep -Seconds 10
        $back = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $oldExe } | Select-Object -First 1
        if ($back) { Write-Host "Previous version is running again (PID $($back.Id))." -ForegroundColor Yellow }
        else { Write-Host 'Previous version did not start: open it on the run PC by hand.' -ForegroundColor Red }
    }
    exit 1
}

# ---------- desktop shortcut -> new version ----------
try {
    $profileDir = (Get-CimInstance Win32_UserProfile -Filter "SID='$($desktopUser.Sid)'").LocalPath
    $lnk = Join-Path $profileDir 'Desktop\Auto Report Web UI.lnk'
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sc.TargetPath = $exe
    $sc.WorkingDirectory = $dest
    $sc.IconLocation = "$exe,0"
    $sc.Save()
    Write-Host "Shortcut: $lnk -> $exe"
} catch {
    Write-Host "Warning: could not update the desktop shortcut ($($_.Exception.Message))" -ForegroundColor Yellow
}

# Auto starts a few seconds after the engine is up
$auto = $null
for ($i = 0; $i -lt 5 -and -not $auto; $i++) {
    Start-Sleep -Seconds 3
    try { $auto = (Invoke-RestMethod -Uri 'http://127.0.0.1:6300/api/engine' -TimeoutSec 5).scheduler_running } catch {}
}
Write-Host ''
Write-Host "Running $exe on the desktop (PID $($p.Id), session $($p.SessionId)), Web UI answers on 6300." -ForegroundColor Green
if ($auto) { Write-Host 'Auto is ON.' -ForegroundColor Green }
else { Write-Host 'Auto is OFF: press Start Auto on the BOT REPORT page if the hourly report should run.' -ForegroundColor Yellow }
exit 0
'@

# ---------- ส่ง + รัน ----------
function Invoke-Remote([string]$cmd) {
    ssh -o BatchMode=yes $SshHost $cmd | Out-Host
    return $LASTEXITCODE
}
$remoteDir = 'C:\ProgramData\AutoReportWebUI-deploy\' + [guid]::NewGuid().ToString('N')
$remoteDirFwd = $remoteDir -replace '\\', '/'
$scriptFile = Join-Path $env:TEMP 'webui-remote-install.ps1'
[IO.File]::WriteAllText($scriptFile, $remoteScript, [Text.UTF8Encoding]::new($true))

$code = 1
try {
    if ((Invoke-Remote "New-Item -ItemType Directory -Force -Path '$remoteDir' | Out-Null") -ne 0) { throw "เชื่อมต่อ $SshHost ไม่ได้" }
    Write-Host 'กำลังส่งไฟล์ไปเครื่องรัน...'
    scp -q -o BatchMode=yes $pkg $scriptFile "${SshHost}:$remoteDirFwd/"
    if ($LASTEXITCODE) { throw 'คัดลอกไฟล์ไปเครื่องรันไม่สำเร็จ' }
    $remoteArgs = "-Pkg '$remoteDir\$(Split-Path -Leaf $pkg)' -RemoteRoot '$RemoteRoot' -Target '$Target' " +
            "-OperatorFiles '$($operatorFiles -join ',')' -Switch $([int][bool]$Switch) -Force $([int][bool]$Force) " +
            "-NoAuto $([int][bool]$NoAuto)"
    $code = Invoke-Remote "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '$remoteDir\webui-remote-install.ps1' $remoteArgs"
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    Invoke-Remote "Remove-Item -LiteralPath '$remoteDir' -Recurse -Force -ErrorAction SilentlyContinue" | Out-Null
    # [IO.File]::Delete: Remove-Item ลบไม่ได้เมื่อ TEMP เป็น path แบบย่อ (เช่น C:\Users\KKNGW~1\...)
    foreach ($f in @($pkg, $scriptFile)) { try { [IO.File]::Delete($f) } catch {} }
}
if ($code -ne 0) { Write-Host 'deploy ไม่สำเร็จ (ดูข้อความด้านบน)' -ForegroundColor Red; exit 1 }
Write-Host "deploy $folder เสร็จ" -ForegroundColor Green
