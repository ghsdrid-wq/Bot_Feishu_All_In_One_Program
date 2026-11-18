# Bot_Feishu_All_In_One_Program: ข้อมูลสำหรับ Claude

**ตั้งแต่ 18/11/2026 main คือ Web UI** (FastAPI + React/Vite/Tailwind + pywebview, ดู [WEBUI.md](WEBUI.md)) — หน้าจอ Tk เดิมเก็บไว้ที่ branch `Old-Version`
ตรรกะงานยังอยู่ใน `bot_main.py` (Engine ของ Web UI ใช้เมธอดชุดเดิม) แก้ตรรกะที่นั่นได้ แต่ห้ามให้พึ่งช่องกรอกของ Tk
เครื่องรันใช้ `C:\0Niphonk\Programs\AutoReportWebUIV<x-y-z>\AutoReportWebUI.exe` (shortcut "Auto Report Web UI" บนเดสก์ท็อป) build ด้วย `build_webui.ps1`
`deploy-ssh.ps1` วาง/สลับ Web UI (`AutoReportWebUIV<x-y-z>`) สลับได้ทั้งจาก Web UI ตัวก่อนและจากโปรแกรมเดิม AutoReportFeishu.exe

อ่านก่อนแก้โค้ด: [README.md](README.md) (ใช้งาน, เวอร์ชัน, build, deploy) และ [STRUCTURE.md](STRUCTURE.md) (โครงสร้าง, config key)
**แก้โค้ดแล้วต้องอัปเดต STRUCTURE.md ให้ตรงเสมอ** (กฎที่หัวไฟล์ STRUCTURE.md)

## กติกาของ repo
- ไฟล์ทุกไฟล์เป็น UTF-8 มี BOM และขึ้นบรรทัดแบบ CRLF (`.editorconfig`)
- เลขเวอร์ชันมีที่เดียวคือ `app_version.py` เปลี่ยนด้วย `python tools/bump_version.py patch|minor|major`
  commit ที่ออกเวอร์ชันจะใส่เลขท้ายข้อความ เช่น "... (2.1.17)"
- `git pull` ก่อนเริ่มงานทุกครั้ง (มีคนแก้จากเครื่องอื่นด้วย)
- GitHub: `ghsdrid-wq/Bot_Feishu_All_In_One_Program` branch `main`
- ห้ามขึ้น git: `config.ini` (มี token), `jms_user_policy.json`, `sent_messages.json`, `metrics/store.db`, `Excel/`, `dist/`

## Build
- `.\build_webui.ps1` → `dist\AutoReportWebUIV<x-y-z>` (npm build หน้าเว็บก่อน ถ้ายังไม่มี node_modules จะ `npm ci` เอง) ต้องใช้ Python 3.14 + PyInstaller 6.16 + Node
- build คัดลอก `config.ini`, `metrics_config.yaml`, `jms_user_policy.json`, `store.db` จาก release ล่าสุดใน `dist\` มาให้

## เครื่องรัน (SSH alias `runpc`, IP 10.30.32.163)
- แต่ละเวอร์ชันอยู่คนละโฟลเดอร์ใน `C:\0Niphonk\Programs\AutoReportWebUIV<x-y-z>` (เก็บเวอร์ชันเก่าไว้ข้างกัน รวม `AutoReportFeishuV2-1-18` ตัว Tk เดิม)
- เป็นโปรแกรม GUI รันบนหน้าจอผู้ใช้ (ไม่ใช่ service) ห้ามปิดโดยไม่ได้รับคำสั่ง เพราะส่งรายงานเข้า Feishu ทุกชั่วโมง
- Web UI พอร์ต 6300 (`http://127.0.0.1:6300/api/engine` ใช้เช็คว่าเปิดอยู่ / `scheduler_running` = Auto), controller API 6100, dashboard 6200, Feishu webhook ตาม `[FEISHU] bot_port`
- ดูว่าตัวไหนรันอยู่: `ssh runpc "Get-Process AutoReportWebUI,AutoReportFeishu -ea 0 | Select-Object Path"`

## Deploy (`deploy-ssh.ps1`)
- ไม่ใส่ flag: วางเวอร์ชันใหม่ข้างตัวเก่าและคัดลอกไฟล์ตั้งค่าจากตัวที่รันอยู่ **ไม่แตะโปรแกรมที่รันอยู่** ทำได้เสมอ
- `-Switch`: ปิดตัวเก่าแล้วเปิดตัวใหม่ **ต้องได้รับคำสั่งจากผู้ใช้ทุกครั้ง** ใช้จริงครั้งแรก 18/11/2026 (2.1.17 -> 2.1.18) เจอ 2 บั๊กที่แก้แล้ว:
  บัญชีจาก `$env:USERDOMAIN` ของ SSH map ไม่ได้ (ตอนนี้ใช้เจ้าของโปรเซส/ผู้ใช้หน้าจอ และตรวจก่อนปิดตัวเก่า) และโฟลเดอร์ใหม่ติดสิทธิ์ของ
  ProgramData ทำให้โปรแกรมเซฟ config.ini ไม่ได้ (ตอนนี้ `icacls /reset` ทุกครั้ง) — โฟลเดอร์เวอร์ชันใหม่ทำให้ Windows Firewall ถามสิทธิ์
  บนจอเครื่องรัน ต้องให้คนที่เครื่องรันกดอนุญาตเอง
  - Web UI จำ Auto ใน `[WEBUI] auto_on` เปิดใหม่แล้ว Auto เปิดต่อเอง สลับจากโปรแกรมเดิม (config ไม่มี `[WEBUI]`) จะใส่ `auto_on = true` ให้ (`-NoAuto` = ไม่ใส่)
  - ไม่ยอมปิดช่วง `[TIME] run_minute` ถึง +15 นาที เว้นแต่ใส่ `-Force`
- `-Target <ชื่อโฟลเดอร์>` ใช้วางลงโฟลเดอร์ทดสอบ (ลบทิ้งเองหลังทดสอบ)
- ต้องรันจาก PowerShell (tar ของ Git Bash ใช้ path `C:\` ไม่ได้)
