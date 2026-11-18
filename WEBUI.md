# Web UI (FastAPI + React/Vite/Tailwind + pywebview)

หน้าจอใหม่แทน customtkinter — ทุกหน้าของโปรแกรมเดิม ออกแบบใหม่ ใช้ `config.ini` ไฟล์เดียวกัน

## เปิดใช้งาน
```
python webui_main.py              หน้าต่างโปรแกรม (เครื่องอื่นใน LAN เปิดได้ที่ http://<ip>:6300)
python webui_main.py --browser    เปิดในเบราว์เซอร์แทน
python webui_main.py --server --local --no-autostart    ทดสอบ: เซิร์ฟเวอร์อย่างเดียว ไม่เปิด bot / Auto / API 6100
```
หน้าเว็บ build ก่อนครั้งแรก/หลังแก้: `cd frontend && npm install && npm run build` (ได้ `webui/static`)

## โครงสร้าง
| ไฟล์ | หน้าที่ |
|---|---|
| `webui/engine.py` | `Engine` — ตัวทำงานหลักแบบไม่มี Tk: ใช้เมธอดชุดเดิมของ `bot_main.App` (รอบรายงาน, Auto, export JMS/DWS, Realtime, ตาราง, ส่ง Feishu, สรุปรายวัน, VIP, DWS Plan, JMS bot, webhook, controller API) และเขียนใหม่เฉพาะเมธอดที่เคยแตะหน้าจอ (log, สถานะ, `after`, save_config, ช่วงวันที่, เริ่ม/หยุด) — เมธอดเดิมเช็ก `is_ui_thread()` แล้วอ่าน config เมื่อไม่ใช่ UI thread, Engine ตอบ False เสมอ |
| `webui/api.py` | REST API ทุกหน้า + เสิร์ฟหน้าเว็บ |
| `webui/schema.py` | รายการช่องตั้งค่า (หน้า ตั้งค่า / สรุปรายวัน) หน้าเว็บวาดฟอร์มจากรายการนี้ |
| `webui/services.py` | เก็บผล VIP AWB ล่าสุด (ห่อ `vip_awb.run`) |
| `webui_main.py` | เปิดเซิร์ฟเวอร์พอร์ต 6300 + หน้าต่าง pywebview + ตัวเลือกไฟล์/โฟลเดอร์ของ Windows |
| `frontend/` | React + Vite + Tailwind (ฟอนต์ Noto Sans Thai) — หน้า: รายงานรายชั่วโมง, ไฟล์ Excel, สรุปรายวัน, VIP AWB, DWS PLAN, JMS USER, สิทธิ์รหัส, ตั้งค่า |

## ต่างจากโปรแกรมเดิม
- **จำสถานะ Auto** (`[WEBUI] auto_on`): เปิดโปรแกรมใหม่แล้ว Auto เปิดต่อเอง ไม่ต้องมีคนกด Start Auto
- ปุ่มสร้างตัวอย่างไม่เปิดหน้าต่าง Explorer บนจอเครื่องรัน (หน้าเว็บมีลิงก์ดาวน์โหลดแทน)
- ช่วงวันที่ที่เลือกเองใช้ได้เฉพาะตอนโปรแกรมเปิดอยู่ (เหมือนเดิม) ปุ่ม "กลับไปตามรอบงาน" คืนค่าอัตโนมัติ
- ยังไม่มีระบบล็อกอิน — ถ้าเปิดให้เครื่องอื่นใน LAN ใช้ ควรเพิ่มรหัสผ่านก่อนใช้จริง
- ห้ามเปิดคู่กับโปรแกรมเดิมบนเครื่องเดียวกัน (ใช้พอร์ต 6100/7000/6200 ชุดเดียวกัน)
