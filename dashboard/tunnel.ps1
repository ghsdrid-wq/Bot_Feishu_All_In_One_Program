# =====================================================================
# tunnel.ps1 — เปิด dashboard ออกอินเทอร์เน็ตด้วย Cloudflare Tunnel
#
# ใช้ Cloudflare แทน ngrok เพราะ:
#   - ngrok ฟรีเปลี่ยน URL ทุกครั้งที่ restart -> ลิงก์เก่าใน Feishu ตายหมด
#   - Cloudflare ผูก subdomain ถาวรได้ฟรี และไม่ต้องเปิดพอร์ตออกอินเทอร์เน็ต
#
# ⚠ การรันสคริปต์นี้ = เปิดหน้า dashboard ให้เข้าถึงได้จากอินเทอร์เน็ต
#    ป้องกันด้วย token ใน config.ini [DASHBOARD] เท่านั้น
#    ห้ามแชร์ลิงก์ที่มี ?t=... ในที่สาธารณะ
#
# วิธีใช้:
#   .\dashboard\tunnel.ps1              # quick tunnel (URL สุ่มใหม่ทุกครั้ง — ไว้ทดสอบ)
#   .\dashboard\tunnel.ps1 -Named kkn   # ใช้ tunnel ที่ตั้งชื่อไว้ (URL คงที่)
# =====================================================================

param(
    [int]$Port = 6200,
    [string]$Named = ""
)

$ErrorActionPreference = "Stop"

# ---- 1) ตรวจว่ามี cloudflared ไหม ----
$cf = Get-Command cloudflared -ErrorAction SilentlyContinue
if (-not $cf) {
    Write-Host "ไม่พบ cloudflared — ติดตั้งด้วยคำสั่งนี้ก่อน:" -ForegroundColor Yellow
    Write-Host "  winget install --id Cloudflare.cloudflared" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "ถ้า winget ใช้ไม่ได้ ดาวน์โหลดไฟล์เดียวจบได้ที่:"
    Write-Host "  https://github.com/cloudflare/cloudflared/releases/latest"
    exit 1
}

# ---- 2) ตรวจว่า server เปิดอยู่ไหม ----
try {
    $health = Invoke-RestMethod -Uri "http://localhost:$Port/healthz" -TimeoutSec 5
    Write-Host "server ทำงานอยู่ — ข้อมูลล่าสุด $($health.latest_date) (อัปเดต $($health.data_updated))" -ForegroundColor Green
} catch {
    Write-Host "ต่อ http://localhost:$Port ไม่ได้ — เปิด server ก่อน:" -ForegroundColor Yellow
    Write-Host "  python -m dashboard.server" -ForegroundColor Cyan
    exit 1
}

# ---- 3) เปิด tunnel ----
if ($Named) {
    # tunnel แบบตั้งชื่อ: URL คงที่ ต้อง login และสร้างครั้งเดียวก่อน
    #   cloudflared tunnel login
    #   cloudflared tunnel create kkn
    #   cloudflared tunnel route dns kkn dashboard.<โดเมนของคุณ>
    Write-Host "เปิด named tunnel '$Named' -> localhost:$Port" -ForegroundColor Cyan
    cloudflared tunnel run --url "http://localhost:$Port" $Named
} else {
    Write-Host ""
    Write-Host "เปิด quick tunnel — URL จะถูกสุ่มใหม่ทุกครั้งที่รัน" -ForegroundColor Yellow
    Write-Host "ถ้าจะใช้จริงให้ทำ named tunnel เพื่อให้ลิงก์ไม่เปลี่ยน (ดูหมายเหตุในไฟล์นี้)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "เมื่อได้ URL มาแล้ว เอาไปใส่ config.ini ใต้ [DASHBOARD] เป็น public_url" -ForegroundColor Cyan
    Write-Host "แล้วสั่ง 'python -m dashboard.server --print-url' เพื่อได้ลิงก์เต็มพร้อม token" -ForegroundColor Cyan
    Write-Host ""
    cloudflared tunnel --url "http://localhost:$Port"
}
