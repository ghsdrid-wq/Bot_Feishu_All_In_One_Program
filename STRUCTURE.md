# STRUCTURE — Bot_Feishu_All_In_One_Program

> ⚠️ **กฎการดูแลไฟล์นี้ (สำคัญ)**
> ทุกครั้งที่แก้ไขโค้ดใน repo นี้ — เพิ่ม/ลบ/ย้ายไฟล์, เปลี่ยนชื่อหรือ logic ของฟังก์ชัน/คลาส, เพิ่ม/แก้ HTTP endpoint, เปลี่ยน config key, หรือเปลี่ยน flow การทำงาน — **ต้องอัปเดตไฟล์ STRUCTURE.md นี้ให้ตรงกับโค้ดเสมอ** เพื่อให้ใช้อ้างอิงหาจุดแก้ไขได้ถูกในอนาคต

## ภาพรวม
GUI (customtkinter, ธีม Nord) ตัวเดียวที่รวมทุกระบบของฝั่ง controller/report เข้าด้วยกันเป็น **"All-In-One"**:
1. **Data Export (ตัวดึงยอด)** — ดึงยอดจาก DB ของ DWS (MySQL/pymysql) และจาก JMS J&T API ออกมาเป็นไฟล์ Excel (openpyxl) แล้วส่งเข้า Feishu
2. **Workbook Manager** — เปิด Excel (win32com) แคปภาพชีตตามช่วงเวลา แล้วส่งรูปเข้า Feishu chat
3. **DWS Plan Controller** — สั่งเปลี่ยนแพลนการคัดแยก (sorting plan) ของเครื่อง DWS หลายเครื่องพร้อมกันผ่าน HTTP
4. **JMS User Bot** — รับคำสั่งภาษาไทยผ่าน Feishu (Lark) แล้วไป reset รหัส / ปลดล็อก user บนระบบ JMS ของ J&T
5. **Auto Scheduler** — ตั้งเวลาให้ pipeline (ดึงยอด → แคปภาพ → ส่ง Feishu) รันเองตามรอบ

รับ event จาก Feishu ผ่าน Flask webhook, คุย LAN กับเครื่อง DWS agent, และต่อ MySQL DB ของ DWS โดยตรง

## วิธีรัน / Entry point
- รัน: `python bot_main.py` → เปิดคลาส `App(ctk.CTk)` (customtkinter, ธีม Nord, หลายหน้าเลื่อนสลับด้วย nav menu ลากจัดลำดับได้)
- มี **single-instance lock** (`acquire_single_instance_lock`) — เปิดซ้ำจะเตือนแล้วปิด
- ตอนเปิด: โหลด/สร้าง `config.ini` (`ensure_config`), start `controller_api` (Flask) พอร์ต **6100** เป็น daemon thread
- กด **START BOT** ในหน้า JMS User / DWS → start Feishu webhook server (waitress) บนพอร์ตจาก `BOT_PORT` (default 7000) ผ่าน `run_feishu_server` → `serve(bot_app, ...)`
- ต้องมี reverse proxy/ngrok ชี้เข้ามาที่ webhook `/feishu_event`
- `main.py` เป็น entry point **รุ่นเก่า** (Tkinter ธรรมดา, 3 แท็บ) — ยังอยู่ในrepo แต่ตัวหลักคือ `bot_main.py`

## โครงสร้างไฟล์
| ไฟล์ | หน้าที่ |
|------|---------|
| `bot_main.py` | **หัวใจหลัก (~4600 บรรทัด)** — Flask `bot_app` (webhook) + คลาส `App(ctk.CTk)` รวมทุกหน้า/ทุกระบบ + Data Export (DWS DB + JMS API) + scheduler |
| `Createphoto.py` | เปิด Excel ผ่าน win32com, จัดวันที่/คอลัมน์ตามเวลา, แคปภาพชีต → `run_create(...)` (เรียกจาก `App.run_process`) |
| `Botmessage.py` | อัปโหลดรูปเข้า Feishu แล้วส่งเข้า chat → `run_send(folder, ...)` (เรียกจาก `App.run_process`) |
| `controller/controller_api.py` | Flask API พอร์ต **6100** (`/status`, `/switch_plan`, `/refresh`) ให้ระบบอื่นสั่ง controller |
| `core/jms_api.py` | เรียก JMS J&T: `search_user`, `reset_app_password`, `reset_jms_password`, `enable_user` (BASE_URL `jmsgw.jtexpress.co.th`) |
| `core/feishu_api.py` | Feishu token + `send_message` (ทางเลือก; bot_main.py มี `reply_feishu_message`/`get_tenant_access_token` ของตัวเอง) |
| `core/config.py` | โหลด/เซฟ config (`get_config`, `save_config`) รองรับ frozen exe |
| `core/logger.py` | `write_log(...)` เขียน log รายวันที่โฟลเดอร์ `logs/` |
| `main.py` | **entry point รุ่นเก่า** — `ControllerGUI` (Tkinter) DWS Plan + JMS User เท่านั้น ไม่มี Data Export/Workbook |

## ฟังก์ชัน/ส่วนสำคัญใน bot_main.py
### Feishu webhook (module-level, บน `bot_app`)
- `feishu_event()` (route `/feishu_event`) — รับ event, ตรวจ challenge, กัน event ซ้ำ(`processed_events`), บังคับ @mention ในกลุ่ม (`feishu_bot_mentioned`), แยกคำสั่งหลายบรรทัด (`split_feishu_command_blocks`), จัด intent ด้วย `detect_jms_intent` / `detect_plan_intent`
- `bot_status()` (`/status`), `bot_switch_plan()` (`/switch_plan`) — endpoint ให้ระบบภายนอกเช็ค/สั่งเปลี่ยนแพลน
- `get_tenant_access_token`, `reply_feishu_message`, `send_feishu_chat_message`, `send_system_alert` — คุย Feishu OpenAPI
- `extract_staff_numbers`, `detect_jms_intent` — parse เลข user / ประเภทคำสั่ง JMS

### คลาส `App(ctk.CTk)` — หน้า UI
- `build_home_page` / `build_workbooks_page` / `build_data_export_page` / `build_dws_plan_page` / `build_jms_user_page` / `build_settings_page` — สร้างแต่ละหน้า
- `render_nav_menu` / `start_nav_drag` / `show_page` — เมนูนำทางลากจัดลำดับเองได้
- `WorkbookCard`, `SheetRow` (คลาสแยก) — การ์ดตั้งค่าไฟล์ Excel / ชีตในหน้า Workbooks

### Data Export pipeline (DWS DB + JMS API)
- `run_dws_jms_task` / `run_dws_jms_process` — orchestrate การดึงยอด
- `run_dws()` — query MySQL DB (`get_dws_db_config`, `_probe_dws_db` ผ่าน pymysql) แปลงเป็น Excel (`_dws_row_from_record`, `autofit_excel_file`)
- `run_jms_auto` / `run_jms_pda` / `run_realtime_db` / `_export_jms` / `_jms_fire_export` / `_jms_collect_export` — ยิง JMS export API แล้วรวมผลเป็นไฟล์
- `send_selected_excel_files_to_feishu` — อัปโหลด+ส่งไฟล์ Excel เข้า Feishu

### Workbook (แคปภาพ) + ส่ง
- `run_process()` — เรียก `run_create(...)` (Createphoto) แคปภาพ แล้ว `run_send(...)` (Botmessage) ส่งรูป

### Scheduler / runtime
- `start_scheduler` / `scheduler_loop` / `stop_scheduler` — Auto รันตามรอบ (`run_minute`, `start_hour`/`end_hour`)
- `run_once` / `run_process` / `worker_loop` / `watchdog` — คุมการรัน manual + กันค้าง
- `run_token_healthcheck` / `notify_it_alert` / `notify_export_token_error` — ตรวจ token เชิงรุกแล้วแจ้ง Feishu

### DWS Plan Controller
- `switch_plan` / `switch_plan_thread` / `switch_single_client` — ยิง POST เปลี่ยนแพลนทุกเครื่องแบบขนาน
- `refresh_status` / `refresh_status_thread` / `render_controller_status` — poll `/status` ทุกเครื่อง

### JMS User Bot
- `start_feishu_bot` / `run_feishu_server` / `stop_feishu_bot` — คุม webhook server (waitress)
- `handle_jms_command` — วนทำทีละ user: `search_user` → reset/enable, สรุปผล success/fail กลับ Feishu

## Config (`config.ini`)
- `[PATH]`: `output_dir`
- `[FEISHU]`: `app_id`, `app_secret`, `chat_id`, `verify_token`, `bot_port` (7000), `ngrok_url`, `auth_token` (JMS), `bot_name` (default `BOT_JMSKKN`)
- `[TIME]`: `run_minute` (5), `start_hour` (15), `end_hour` (12)
- `[DWS_JMS]`: `dws_url`, `dws_token`, `jms_token`, ชื่อไฟล์ export (`name_dws`, `name_auto`, `name_dwspda`, `name_realtime_db`), ช่วงวัน/เวลา (`start_date`, `end_date`, `start_hour`, `end_hour`), `raw_path`, `download_size`, flags `enabled` / `send_*_file`
- `[DWS1]..[DWS9-11]`: `ip`, `port` ของเครื่อง agent (default subnet `10.30.32.x`, พอร์ต 4000/4001)
- `[WORKBOOKS]` / `[EXPORTS]`: `items` — รายการไฟล์ Excel/ชีต ที่จัดการในหน้า Workbooks
- หมายเหตุ: `core/config.ini` เป็นไฟล์แยกของ `core/*` (มี `[FEISHU]`, `[NGROK]`)

## Dependencies / บริการภายนอก
- Python libs (ดู `requirements.txt`): `customtkinter`, `tkcalendar`, `requests`, `flask`, `waitress`, `pywin32`, `Pillow`, `pymysql`, `openpyxl`, `pyinstaller`
- Feishu OpenAPI (`open.feishu.cn`), JMS J&T (`jmsgw.jtexpress.co.th`)
- MySQL DB ของ DWS (ต่อผ่าน pymysql — ตั้งค่าใน `[DWS_JMS]`)
- เครื่อง DWS agent บน LAN (ดู repo `Bot_Feishu_Main_Controller` / `Bot-Scada_Jms` ฝั่ง agent)
- Microsoft Excel (COM automation ผ่าน win32com สำหรับ Workbook Manager)

## ข้อควรระวัง
- `auth_token`/`jms_token` ของ JMS หมดอายุได้ → อัปเดตในหน้า ตั้งค่า; `run_token_healthcheck` จะแจ้ง Feishu ก่อนถึงรอบส่งยอด
- STOP BOT แค่ mark offline — ต้อง restart โปรแกรมจริงเพื่อหยุด webhook server
- Workbook Manager ต้องมี Excel ติดตั้งและปิด dialog ค้างไว้ไม่ได้ (COM จะค้าง) — มี `wait_excel` / `excel_call` กันค้าง
- เปิดโปรแกรมได้ instance เดียว (single-instance lock)
