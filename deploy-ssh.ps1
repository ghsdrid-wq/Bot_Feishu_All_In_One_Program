# ส่ง AutoReportFeishu เวอร์ชันใน app_version.py ไปวางบนเครื่องรันผ่าน SSH (ใช้ SSH key ไม่ต้องใช้รหัสผ่าน)
#
#   powershell -ExecutionPolicy Bypass -File deploy-ssh.ps1                  วางเวอร์ชันใหม่ไว้ข้างตัวเก่า (ไม่แตะตัวที่รันอยู่)
#   powershell -ExecutionPolicy Bypass -File deploy-ssh.ps1 -Build           build ก่อนแล้ววาง
#   powershell -ExecutionPolicy Bypass -File deploy-ssh.ps1 -Switch          วาง + ปิดตัวเก่า + เปิดตัวใหม่บนหน้าจอผู้ใช้
#
# - วางที่ C:\0Niphonk\Programs\AutoReportFeishuV<เวอร์ชัน> (โฟลเดอร์ละเวอร์ชัน เหมือนที่ทำมือ)
# - ไฟล์ของผู้ใช้ (config.ini, metrics_config.yaml, jms_user_policy.json, store.db, sent_messages.json)
#   ไม่ส่งจากเครื่องนี้ แต่คัดลอกจากโฟลเดอร์ที่รันอยู่บนเครื่องรัน ตอน -Switch คัดลอกซ้ำหลังปิดตัวเก่า ให้ได้ข้อมูลล่าสุด
# - -Switch ไม่ยอมปิดโปรแกรมช่วงนาทีที่รอบรายงานรายชั่วโมงกำลังทำงาน ([TIME] run_minute ถึง +15 นาที) ใช้ -Force ข้ามได้
# - หลัง -Switch โหมด Auto ไม่เปิดเอง (โปรแกรมไม่จำสถานะ Auto) ต้องมีคนกด Start Auto ที่หน้า BOT REPORT
#   ส่วน Feishu Bot / JMS bot เปิดเองตาม [FEISHU] bot_autostart / jms_autostart
# - เวอร์ชันเดียวกับที่รันอยู่: ต้องใช้ -Switch (ปิดก่อน แล้วเปลี่ยนเฉพาะไฟล์โปรแกรม เก็บของเดิมไว้ใน _previous\)
param(
    [string]$SshHost = 'runpc',
    [string]$RemoteRoot = 'C:\0Niphonk\Programs',
    [string]$Target,          # ชื่อโฟลเดอร์ปลายทาง (ค่าเริ่มต้น AutoReportFeishuV<เวอร์ชัน>) ใช้ทดสอบได้
    [switch]$Build,
    [switch]$Switch,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

if ($Build) {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'build_onedir.ps1')
    if ($LASTEXITCODE) { Write-Host 'build ไม่สำเร็จ' -ForegroundColor Red; exit 1 }
}
Push-Location $root
try { $version = (& python -c "from app_version import APP_VERSION; print(APP_VERSION)").Trim() } finally { Pop-Location }
if ($version -notmatch '^\d+\.\d+\.\d+$') { Write-Host "อ่านเวอร์ชันจาก app_version.py ไม่ได้: '$version'" -ForegroundColor Red; exit 1 }
$folder = 'AutoReportFeishuV' + $version.Replace('.', '-')
if (-not $Target) { $Target = $folder }
$local = Join-Path $root "dist\$folder"
if (-not (Test-Path (Join-Path $local 'AutoReportFeishu.exe'))) {
    Write-Host "ไม่พบ $local\AutoReportFeishu.exe — build ก่อน (ใส่ -Build)" -ForegroundColor Red; exit 1
}

# ---------- แพ็กเฉพาะไฟล์โปรแกรม ----------
$operatorFiles = @('config.ini', 'metrics_config.yaml', 'jms_user_policy.json', 'store.db', 'sent_messages.json')
$pkg = Join-Path $env:TEMP "feishu-deploy-$([guid]::NewGuid().ToString('N')).tar"
$excludes = @($operatorFiles + 'logs' | ForEach-Object { "--exclude=./$_" })
Write-Host "แพ็ก $folder ..."
# cd เข้าไปก่อน (tar ของ Windows รับ path ภาษาไทยใน -C ได้ไม่แน่นอน)
Push-Location $local
try { tar -cf $pkg @excludes . } finally { Pop-Location }
if ($LASTEXITCODE) { Write-Host 'แพ็กไฟล์ไม่สำเร็จ' -ForegroundColor Red; exit 1 }
Write-Host ("ขนาด {0:N0} MB" -f ((Get-Item $pkg).Length / 1MB))

# ---------- สคริปต์ที่รันบนเครื่องรัน ----------
$remoteScript = @'
param([string]$Pkg, [string]$RemoteRoot, [string]$Target, [string]$OperatorFiles, [int]$Switch, [int]$Force)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$operator = $OperatorFiles -split ','
$dest = Join-Path $RemoteRoot $Target
$proc = Get-Process -Name AutoReportFeishu -ErrorAction SilentlyContinue | Select-Object -First 1
$cur = if ($proc) { Split-Path -Parent $proc.Path } else { $null }
$same = $cur -and ((Resolve-Path $cur).Path.TrimEnd('\') -ieq $dest.TrimEnd('\'))
Write-Host "Running now : $(if ($cur) { $cur } else { '(not running)' })"
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

# ---------- unpack ----------
$work = Join-Path (Split-Path $Pkg) 'x'
New-Item -ItemType Directory -Force -Path $work | Out-Null
tar -xf $Pkg -C $work
if ($LASTEXITCODE) { throw 'tar extract failed' }
if (-not (Test-Path (Join-Path $work 'AutoReportFeishu.exe'))) { throw 'Package has no AutoReportFeishu.exe' }

function Copy-Operator([string]$from, [string]$to, [bool]$overwrite) {
    if (-not $from -or ($from.TrimEnd('\') -ieq $to.TrimEnd('\'))) { return }
    foreach ($n in $operator) {
        $s = Join-Path $from $n; $d = Join-Path $to $n
        if ((Test-Path $s) -and ($overwrite -or -not (Test-Path $d))) {
            Copy-Item -LiteralPath $s -Destination $d -Force
            Write-Host "  carried $n from $(Split-Path -Leaf $from)"
        }
    }
}

function Stop-App {
    if (-not $proc) { return }
    Write-Host "Stopping AutoReportFeishu (PID $($proc.Id))..."
    Stop-Process -Id $proc.Id -Force
    for ($i = 0; $i -lt 30 -and (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
    if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) { throw 'Old version did not stop.' }
}

$programItems = Get-ChildItem -LiteralPath $work -Force
if ($same) {
    Stop-App
    $prev = Join-Path $dest '_previous'
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
        Copy-Operator $cur $dest $true   # latest config / store.db / sent_messages after the old one stopped
    }
}

if (-not $Switch) {
    Write-Host ''
    Write-Host "Staged. The running version was not touched. Open $dest\AutoReportFeishu.exe to switch, or run deploy-ssh.ps1 -Switch." -ForegroundColor Green
    exit 0
}

# ---------- start on the logged-on user's desktop ----------
$exe = Join-Path $dest 'AutoReportFeishu.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"
$taskName = 'AutoReportFeishu-deploy-start'
$action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $dest
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

$ok = $false
$clock = [Diagnostics.Stopwatch]::StartNew()
while ($clock.Elapsed.TotalSeconds -lt 90) {
    Start-Sleep -Seconds 3
    $p = Get-Process -Name AutoReportFeishu -ErrorAction SilentlyContinue | Where-Object { $_.Path -ieq $exe } | Select-Object -First 1
    if (-not $p) { continue }
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:6100/status' -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
}
if (-not $ok) {
    Write-Host "New version did not answer on port 6100 within 90 seconds. Check $dest\logs and the screen." -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host "Running $exe on the desktop (PID $($p.Id), session $($p.SessionId)), controller answers on 6100." -ForegroundColor Green
Write-Host 'Auto mode is OFF after a restart: someone must press Start Auto on the BOT REPORT page.' -ForegroundColor Yellow
exit 0
'@

# ---------- ส่ง + รัน ----------
function Invoke-Remote([string]$cmd) {
    ssh -o BatchMode=yes $SshHost $cmd | Out-Host
    return $LASTEXITCODE
}
$remoteDir = 'C:\ProgramData\AutoReportFeishu-deploy\' + [guid]::NewGuid().ToString('N')
$remoteDirFwd = $remoteDir -replace '\\', '/'
$scriptFile = Join-Path $env:TEMP 'feishu-remote-install.ps1'
[IO.File]::WriteAllText($scriptFile, $remoteScript, [Text.UTF8Encoding]::new($true))

$code = 1
try {
    if ((Invoke-Remote "New-Item -ItemType Directory -Force -Path '$remoteDir' | Out-Null") -ne 0) { throw "เชื่อมต่อ $SshHost ไม่ได้" }
    Write-Host 'กำลังส่งไฟล์ไปเครื่องรัน...'
    scp -q -o BatchMode=yes $pkg $scriptFile "${SshHost}:$remoteDirFwd/"
    if ($LASTEXITCODE) { throw 'คัดลอกไฟล์ไปเครื่องรันไม่สำเร็จ' }
    $remoteArgs = "-Pkg '$remoteDir\$(Split-Path -Leaf $pkg)' -RemoteRoot '$RemoteRoot' -Target '$Target' " +
            "-OperatorFiles '$($operatorFiles -join ',')' -Switch $([int][bool]$Switch) -Force $([int][bool]$Force)"
    $code = Invoke-Remote "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File '$remoteDir\feishu-remote-install.ps1' $remoteArgs"
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    Invoke-Remote "Remove-Item -LiteralPath '$remoteDir' -Recurse -Force -ErrorAction SilentlyContinue" | Out-Null
    # [IO.File]::Delete: Remove-Item ลบไม่ได้เมื่อ TEMP เป็น path แบบย่อ (เช่น C:\Users\KKNGW~1\...)
    foreach ($f in @($pkg, $scriptFile)) { try { [IO.File]::Delete($f) } catch {} }
}
if ($code -ne 0) { Write-Host 'deploy ไม่สำเร็จ (ดูข้อความด้านบน)' -ForegroundColor Red; exit 1 }
Write-Host "deploy $folder เสร็จ" -ForegroundColor Green
