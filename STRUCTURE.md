# STRUCTURE — Bot_Feishu_All_In_One_Program

> ⚠️ **กฎการดูแลไฟล์นี้ (สำคัญ)**
> ทุกครั้งที่แก้ไขโค้ดใน repo นี้ — เพิ่ม/ลบ/ย้ายไฟล์, เปลี่ยนชื่อหรือ logic ของฟังก์ชัน/คลาส, เพิ่ม/แก้ HTTP endpoint, เปลี่ยน config key, หรือเปลี่ยน flow การทำงาน — **ต้องอัปเดตไฟล์ STRUCTURE.md นี้ให้ตรงกับโค้ดเสมอ** เพื่อให้ใช้อ้างอิงหาจุดแก้ไขได้ถูกในอนาคต

## ภาพรวม
GUI (Tkinter) ตัวเดียวที่รวม **2 ระบบ** เข้าด้วยกัน — เป็น "All-In-One" ของฝั่ง controller:
1. **DWS Plan Controller** — สั่งเปลี่ยนแพลนการคัดแยก (sorting plan) ของเครื่อง DWS หลายเครื่องพร้อมกันผ่าน HTTP
2. **JMS User Bot** — รับคำสั่งภาษาไทยผ่าน Feishu (Lark) แล้วไป reset รหัส / ปลดล็อก user บนระบบ JMS ของ J&T

รับ event จาก Feishu ผ่าน Flask webhook และคุย LAN กับเครื่อง DWS agent แต่ละเครื่อง

## วิธีรัน / Entry point
- รัน: `python main.py` → เปิด `ControllerGUI` (Tkinter, 3 แท็บ: DWS Plan / JMS User / Setting)
- ตอนเปิด: สร้าง/โหลด `config.ini`, start `controller_api` (Flask) บนพอร์ต **6100** เป็น daemon thread
- กด **START BOT** ในแท็บ DWS → start Feishu webhook server (waitress) บนพอร์ตจาก `BOT_PORT` (default 7000)
- ต้องมี reverse proxy/ngrok ชี้เข้ามาที่ webhook `/feishu_event`

## โครงสร้างไฟล์
| ไฟล์ | หน้าที่ |
|------|---------|
| `main.py` | **หัวใจหลัก** — Flask `bot_app` (webhook) + คลาส `ControllerGUI` (Tkinter ทั้งหมด) |
| `controller/controller_api.py` | Flask API พอร์ต **6100** (`/status`, `/switch_plan`, `/refresh`) ให้ระบบอื่นสั่ง controller |
| `core/jms_api.py` | เรียก JMS J&T: `search_user`, `reset_app_password`, `reset_jms_password`, `enable_user` (BASE_URL `jmsgw.jtexpress.co.th`) |
| `core/feishu_api.py` | Feishu token + `send_message` (ทางเลือก; main.py มี `reply_feishu_message` ของตัวเอง) |
| `core/config.py` | โหลด/เซฟ config (`get_config`, `save_config`) รองรับ frozen exe |
| `core/logger.py` | `write_log(...)` เขียน log รายวันที่โฟลเดอร์ `logs/` |
| `bot_main.py`, `Botmessage.py`, `Createphoto.py` | **ไฟล์เก่าจากสาย Workbook Manager (Bot-Feishu)** ไม่ได้ถูกเรียกโดย main.py — ดู repo `Bot-Feishu` |

## ฟังก์ชัน/ส่วนสำคัญใน main.py
- `feishu_event()` (route `/feishu_event`) — รับ event, ตรวจ challenge, กัน event ซ้ำ (`processed_events` deque 1000), บังคับ @mention ในกลุ่ม, parse คำสั่งหลายบรรทัด
- `ControllerGUI.handle_jms_command()` — แยกประเภทคำสั่งจาก keyword ภาษาไทย → `APP` / `JMS` / `ENABLE`, ดึงเลข user (regex `\d{8}` หรือ `[0-9A-Z]{10,20}`), วนทำทีละคน
- `ControllerGUI.switch_plan()` / `switch_single_client()` — ยิง POST `/switch_plan` ไปทุกเครื่อง DWS แบบขนาน (ThreadPoolExecutor)
- `check_single_client()` + `auto_refresh_loop()` — poll `/status` ทุกเครื่องทุก 5 วิ
- `build_dws_tab` / `build_jms_tab` / `build_setting_tab` — สร้าง UI แต่ละแท็บ

## Config (`config.ini`)
- `[FEISHU]`: `APP_ID`, `APP_SECRET`, `VERIFY_TOKEN`, `BOT_PORT` (7000), `NGROK_URL`, `AUTH_TOKEN` (JMS), `BOT_NAME` (default `BOT_JMSKKN`)
- `[DWS1]..[DWS9-11]`: `IP`, `PORT` ของเครื่อง agent (default subnet `10.30.32.x`, พอร์ต 4000/4001)

## Dependencies / บริการภายนอก
- `flask`, `waitress`, `requests`, `lark_oapi`, `tkinter`
- Feishu OpenAPI (`open.feishu.cn`), JMS J&T (`jmsgw.jtexpress.co.th`)
- เครื่อง DWS agent บน LAN (ดู repo `Bot_Feishu_Main_Controller` / `Bot-Scada_Jms` ฝั่ง agent)

## ข้อควรระวัง
- `AUTH_TOKEN` ของ JMS หมดอายุได้ → ต้องอัปเดตในแท็บ Setting; `save_config` จะ reload `core.jms_api`
- STOP BOT แค่ mark offline — ต้องปิดโปรแกรมจริงเพื่อหยุด webhook server
