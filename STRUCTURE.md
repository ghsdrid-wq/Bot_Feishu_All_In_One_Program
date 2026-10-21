# STRUCTURE — Bot_Feishu_All_In_One_Program

> ⚠️ **กฎการดูแลไฟล์นี้ (สำคัญ)**
> ทุกครั้งที่แก้ไขโค้ดใน repo นี้ — เพิ่ม/ลบ/ย้ายไฟล์, เปลี่ยนชื่อหรือ logic ของฟังก์ชัน/คลาส, เพิ่ม/แก้ HTTP endpoint, เปลี่ยน config key, หรือเปลี่ยน flow การทำงาน — **ต้องอัปเดตไฟล์ STRUCTURE.md นี้ให้ตรงกับโค้ดเสมอ** เพื่อให้ใช้อ้างอิงหาจุดแก้ไขได้ถูกในอนาคต

## ภาพรวม
GUI (customtkinter, ธีม Nord) ตัวเดียวที่รวมทุกระบบของฝั่ง controller/report เข้าด้วยกันเป็น **"All-In-One"**:
1. **Data Export (ตัวดึงยอด)** — ดึงยอดจาก DB ของ DWS (MySQL/pymysql) และจาก JMS J&T API ออกมาเป็นไฟล์ Excel (openpyxl) แล้วส่งเข้า Feishu
2. **Workbook Manager** — เปิด Excel (win32com) แคปภาพชีตตามช่วงเวลา แล้วส่งรูปเข้า Feishu chat
3. **DWS Plan Controller** — สั่งเปลี่ยนแพลนการคัดแยก (sorting plan) ของเครื่อง DWS หลายเครื่องพร้อมกันผ่าน HTTP
4. **JMS User Bot** — รับคำสั่งภาษาไทยผ่าน Feishu (Lark) แล้วไป reset รหัส / ปลดล็อก user บนระบบ JMS ของ J&T
5. **JMS Access Policy** — จัดการหัวรหัสบล็อกและข้อยกเว้นรายรหัส พร้อม bulk paste/import/export
6. **Auto Scheduler** — ตั้งเวลาให้ pipeline (ดึงยอด → แคปภาพ → ส่ง Feishu) รันเองตามรอบ

รับ event จาก Feishu ผ่าน Flask webhook, คุย LAN กับเครื่อง DWS agent, และต่อ MySQL DB ของ DWS โดยตรง

## วิธีรัน / Entry point
- รัน: `python bot_main.py` → เปิดคลาส `App(ctk.CTk)` (customtkinter, ธีม Nord, หลายหน้าเลื่อนสลับด้วย nav menu ลากจัดลำดับได้)
- มี **single-instance lock** (`acquire_single_instance_lock`) — เปิดซ้ำจะเตือนแล้วปิด
- ตอนเปิด: โหลด/สร้าง `config.ini` (`ensure_config`), start `controller_api` (Flask) พอร์ต **6100** เป็น daemon thread
- กด **START BOT** ในหน้า JMS User / DWS → start Feishu webhook server (waitress) บนพอร์ตจาก `BOT_PORT` (default 7000) ผ่าน `run_feishu_server` → `serve(bot_app, ...)`
- ต้องมี reverse proxy/ngrok ชี้เข้ามาที่ webhook `/feishu_event`

## โครงสร้างไฟล์
| ไฟล์ | หน้าที่ |
|------|---------|
| `bot_main.py` | **หัวใจหลัก (~6000 บรรทัด)** — Flask `bot_app` (webhook) + คลาส `App(ctk.CTk)` รวมทุกหน้า/ทุกระบบ + Data Export (DWS DB + JMS API) + scheduler |
| `Createphoto.py` | เปิด Excel ผ่าน win32com, จัดวันที่/คอลัมน์ตามเวลา, แคปภาพชีต → `run_create(...)` (เรียกจาก `App.run_process`) |
| `daily_summary.py` | **Daily Summary** (ส่งวันละครั้งตามเวลา): คำนวณและวาดทุกตารางเองจากไฟล์ดิบ — AutoPacking (Autoformat A2:AK32), DWS (DWSREALTIME), AUTO PDA / กระสอบซิปแดง / DWS PDA จากไฟล์ JMS และตาราง Error จากชีต Abnormal ของไฟล์รายชั่วโมงที่รวมแล้ว (ตัด Billcode ซ้ำ, % ตามสูตรไฟล์ manual), วาด PNG (Pillow), รวมรูป Excel (AUTOPACKING/DWSREALTIME) เป็น Feishu post ข้อความเดียว แล้วส่งไฟล์ข้อมูลดิบรวมแยก sheet (`run`, `build`, `send`) |
| `Botmessage.py` | อัปโหลดรูปเข้า Feishu แล้วส่งเข้า chat → `run_send(folder, ...)` (เรียกจาก `App.run_process`) |
| `controller/controller_api.py` | Flask API พอร์ต **6100** (`/status`, `/switch_plan`, `/refresh`); endpoint ที่แก้สถานะต้องส่ง `VERIFY_TOKEN` |
| `core/jms_api.py` | เรียก JMS J&T: `search_user`, `reset_app_password`, `reset_jms_password`, `enable_user` (BASE_URL `jmsgw.jtexpress.co.th`) |
| `core/jms_policy.py` | นโยบาย JMS แบบ JSON: normalize, bulk parse, atomic save, exact exemption, prefix block และ fail-closed decision |
| `core/config.py` | โหลด/เซฟ config (`get_config`, `save_config`) รองรับ frozen exe |
| `core/logger.py` | `write_log(...)` เขียน log รายวันที่โฟลเดอร์ `logs/` |
| `metrics/core.py` | โหลด metrics config/SQLite schema; ถ้า `metrics_config.yaml` ข้าง EXE หายจะกู้จากสำเนา read-only ใน `_internal` แบบ atomic และ `schema.sql` มีสำเนาสำรองอีกตำแหน่งใน onedir |

## ฟังก์ชัน/ส่วนสำคัญใน bot_main.py
### Feishu webhook (module-level, บน `bot_app`)
- `feishu_event()` (route `/feishu_event`) — ตรวจ `VERIFY_TOKEN` ก่อนรับ challenge/event, กัน event ซ้ำ(`processed_events`), บังคับ @mention ในกลุ่ม (`feishu_bot_mentioned`), แยกคำสั่งหลายบรรทัด (`split_feishu_command_blocks`), จัด intent ด้วย `detect_jms_intent` / `detect_plan_intent`
- `bot_status()` (`/status`), `bot_switch_plan()` (`/switch_plan`) — เช็คสถานะได้แบบ read-only; การสั่งเปลี่ยนแพลนต้องส่ง `VERIFY_TOKEN` ผ่าน payload, `X-Controller-Token` หรือ Bearer token
- `get_tenant_access_token`, `reply_feishu_message`, `send_feishu_chat_message`, `send_system_alert` — คุย Feishu OpenAPI
- `extract_staff_numbers`, `detect_jms_intent` — parse เลข user / ประเภทคำสั่ง JMS

### คลาส `App(ctk.CTk)` — หน้า UI
- `build_home_page` / `build_workbooks_page` / `build_data_export_page` / `build_dws_plan_page` / `build_jms_user_page` / `build_code_policy_page` / `build_settings_page` — สร้างแต่ละหน้า
- `render_nav_menu` / `start_nav_drag` / `show_page` — เมนูนำทางลากจัดลำดับเองได้
- `WorkbookCard`, `SheetRow` (คลาสแยก) — การ์ดตั้งค่าไฟล์ Excel / ชีตในหน้า Workbooks

### Data Export pipeline (DWS DB + JMS API)
- `run_dws_jms_task` / `run_dws_jms_process` — orchestrate การดึงยอด
- `run_dws()` — query MySQL DB (`get_dws_db_config`, `_probe_dws_db` ผ่าน pymysql) แปลงเป็น Excel (`_dws_row_from_record`, `autofit_excel_file`)
- `run_jms_auto` / `run_jms_pda` / `run_realtime_db` / `_export_jms` / `_jms_fire_export` / `_jms_collect_export` — ยิง JMS export API, ตรวจว่าไฟล์เป็น XLSX จริง, เขียนไฟล์ชั่วคราวที่ยังมีนามสกุล `.xlsx` แล้วแทนไฟล์หลักแบบ atomic เมื่อจบครบเท่านั้น
- `send_selected_excel_files_to_feishu` — อัปโหลด+ส่งไฟล์ Excel เข้า Feishu

### Workbook (แคปภาพ) + ส่ง
- `run_process()` — เรียก `run_create(...)` (Createphoto) แคปภาพ แล้ว `run_send(...)` (Botmessage) ส่งรูป
- `delete_columns_by_start` หาคอลัมน์ชั่วโมงแรกจากหัวตารางของแต่ละชีต (`hour_header_columns`) คืน `{ชีต: จำนวนคอลัมน์ที่ลบ}` แล้ว `shift_range_left` เลื่อนช่วงรูปตามชีตนั้น — ถ้าหาหัวตารางไม่เจอใช้ตำแหน่งเดิม (12点 = คอลัมน์ C)

### Daily Summary (สรุปยอดประจำวัน)
- หน้า **📋 สรุปรายวัน** (`build_daily_summary_page`) ตั้งค่า `[DAILY_SUMMARY]` ได้ทั้งหมด: Chat ID, ไฟล์ดิบ, เปิด/ปิดและแก้ชื่อหัวข้อ, path ไฟล์ดิบแยก (`custom_paths` + `path_*` ช่องว่าง = ใช้ค่าหน้า ตั้งค่า), รูปจาก Excel แทน และค่าขั้นสูง พร้อมปุ่ม สร้างตัวอย่าง (ไม่ส่ง) / ส่งสรุปตอนนี้ (`start_daily_summary_now`)
- `daily_summary_tick` — ตัวตั้งเวลาแยกจาก pipeline เช็กทุก 30 วินาที ขณะเปิด Auto และติ๊ก `auto_send`: ส่งวันละครั้งเมื่อถึง `send_time` (ชั่วโมง:นาที ค่าเริ่มต้น 12:00) ของรอบงานนั้น (`daily_summary.due_to_send`) ถ้ารอบรายชั่วโมงกำลังรันจะรอ, ติ๊ก `send_catch_up` = ส่งตามได้ไม่เกิน `send_late_minutes` (30) นาทีหลังเวลาส่ง / ไม่ติ๊ก = ส่งเฉพาะตรงเวลา (เผื่อ `ON_TIME_GRACE_MINUTES` 5 นาที) เกินนั้นแจ้งครั้งเดียวให้กดส่งเอง, ส่งไม่สำเร็จลองใหม่ได้ไม่เกิน 3 ครั้งห่างกัน 10 นาที, สำเร็จแล้วจำ `last_sent_date` กันส่งซ้ำ
- ตั้งค่าใน `[DAILY_SUMMARY]` ของ `config.ini` (สร้างให้อัตโนมัติ): `sections`, ชื่อรูป Excel, จำนวนเครื่อง PDA, หัวข้อ (`{date}` `{start}` `{end}`; เวลาตามคอลัมน์ AutoPacking ที่มียอดตั้งแต่ 100 และตัดชั่วโมงท้ายที่รวมกันไม่เกิน `title_end_trim_total` 1000 ออก), `abnormal_file` (ว่าง = ใช้ชีต Abnormal รายชั่วโมง; ใส่ไฟล์/โฟลเดอร์ Abnormal Detail = ใช้ไฟล์นั้นแทน ซึ่งมีพัสดุ Intercept ด้วย), `chat_id`, `send_raw_file`, `raw_file_max_mb`
- ไฟล์ Excel หลักของสรุปคือ `2_DWS & PDA (Network Sharing) - เมนูสแกนเปรียบเทียบ.xlsx` — ช่วงรูปในหน้า Workbooks: DWSREALTIME `A1:AF26`, AUTO PDA `A1:AC50`, DWS PDA `A1:AC15`, PDA BAG `A1:B47`
- ทุกตารางวาดเองจากไฟล์ดิบเป็นค่าเริ่มต้น ตรวจกับค่าที่ Excel คำนวณแล้ว: AutoPacking 918 ช่อง (2026-08-27), DWS 594 ช่อง และ AUTO PDA / DWS PDA / กระสอบแดง (ตรวจกับค่าที่ Excel คำนวณใน 2026-09-11 แล้วตรงทุกช่อง; กระสอบแดงตัดเลขกระสอบซ้ำทั้งไฟล์ก่อนนับ เหมือน PDA BAG DATA; DWS PDA นับเฉพาะแถวที่ไม่มีเลขกระสอบ เหมือน query DWS PDA DATA) ตั้ง `{key}_image` เพื่อใช้รูปจาก Excel แทนได้
- ตารางกระสอบซิปแดงเป็นแถวยาวแบบ manual แต่เติมพื้นขาวด้านขวาให้รูปไม่สูงเกิน 1.2 เท่าของความกว้าง เพราะ Feishu ตัดท้ายรูปที่สูงยาวใน post
- รูป Excel ต้องสร้างในรอบเดียวกันหรือไม่เก่ากว่า `image_max_age_minutes`; ส่วนที่ขาดจะขึ้นในข้อความว่าไม่มีข้อมูลและสถานะเป็น WARNING
- ไฟล์ดิบ: ชีตตาราง `TABLE_SHEETS` (AUTO, DWS = DWSREALTIME, DWS+DWSPDA = DWSREALTIME + ตารางลงรถ PDA, AUTOPDA, กระสอบ, ERROR — เขียนจาก grid เดียวกับรูปด้วย `write_grid_sheet`) แล้วตามด้วย `DATA_SHEETS` ครบทุกชีต ชีตที่ไม่มีข้อมูลของรอบนี้ปล่อยว่าง (`window_file_sheet` กรอง DWS1-8/DWS9-11 ตามรอบ)
- `backup_dir` (หน้าเว็บเรียก "โฟลเดอร์บันทึกไฟล์ Excel"): ทุกครั้งที่ส่งจะ `save_excel_copy` บันทึกไฟล์ Excel ที่ใช้ส่งลงโฟลเดอร์นี้ตรง ๆ ก่อนส่ง (ไม่มีโฟลเดอร์ย่อย ไม่มีรูป) ชีตตารางมีสีแท็บ `TABLE_TAB_COLOR`
- ทดสอบนอกโปรแกรม: `python daily_summary.py --start "2026-10-18 15:00" --end "2026-10-19 10:00" --no-send`

### Scheduler / runtime
- `start_scheduler` / `scheduler_loop` / `stop_scheduler` — Auto รันตามรอบ (`run_minute`, `start_hour`/`end_hour`) โดยเทียบเป็นช่องนาที จึงไม่พลาดรอบสุดท้ายจาก loop ที่ตื่นหลังวินาที 00; ถ้าวันที่ตั้งตามรอบงานอัตโนมัติ (`scheduler_follows_shift_dates`) เลยเวลาสิ้นสุดแล้ว Auto **ไม่หยุด** แต่รอวันที่เลื่อน (`date_roll_hour` 13:00) แล้วเริ่มรอบใหม่เอง ช่วงวันที่ที่ผู้ใช้เลือกเองยังจบตามเดิม; เหตุการณ์ Auto บันทึกลง runtime log ระดับ NOTICE (`log_scheduler_event`); `set_locked_date` เลื่อนวันที่ได้แม้ช่องถูกล็อกระหว่าง Auto
- `run_once` / `run_process` / `worker_loop` / `watchdog` — คุมการรัน manual + กันค้าง; `run_generation` กันงานเก่ากลับมาเขียนผลหลังผู้ใช้หยุดแล้วเริ่มรอบใหม่
- `run_token_healthcheck` / `notify_it_alert` / `notify_export_token_error` — ตรวจ token เชิงรุกแล้วแจ้ง Feishu
- กล่อง `Live Log`, `Controller Logs` และ `JMS Logs` เป็น read-only: โปรแกรมปลดล็อกเฉพาะตอนเติม/ล้างข้อความแล้วล็อกกลับทันที ผู้ใช้ยังถมดำ เลื่อน และคัดลอกได้ แต่พิมพ์ทับ วาง หรือลบข้อความไม่ได้

### DWS Plan Controller
- `switch_plan` / `switch_plan_thread` / `switch_single_client` — ยิง POST เปลี่ยนแพลนทุกเครื่องแบบขนาน
- `refresh_status` / `refresh_status_thread` / `render_controller_status` — poll `/status` ทุกเครื่อง

### JMS User Bot
- `start_feishu_bot` / `run_feishu_server` / `stop_feishu_bot` — คุม webhook server (waitress)
- `handle_jms_command` — snapshot นโยบายหนึ่งครั้งต่อข้อความ แล้ววนทำทีละ user: ตรวจ exact exemption ก่อน prefix block → ค้นหา `staffNo` ที่ตรงรหัสเต็มเท่านั้น → reset/enable → รวมผลทุกรหัสตอบกลับ Feishu รวมถึงกรณี bot offline/token หมดอายุ; ถ้า reply ไม่สำเร็จจะลองส่งเข้าห้องเป็นทางสำรอง
- หน้า `จัดการสิทธิ์รหัส` — แท็บหัวรหัสบล็อก/รหัสละเว้น, ค้นหา, แบ่งหน้า 20 รายการ, bulk paste, import/export และลบหลายรายการ

## Runtime policy (`jms_user_policy.json`)
- เก็บข้าง EXE และไม่ commit เข้า Git
- `blocked_prefixes`: บล็อกทุก USER ที่ขึ้นต้นด้วยค่าในรายการ
- `exempt_codes`: อนุญาตเฉพาะรหัสเต็มที่ตรงกัน และมีสิทธิ์เหนือ prefix block
- ลำดับตัดสินคงที่: policy ผิดพลาด/รหัสไม่ถูกต้อง → block, exact exemption → allow, prefix block → block, นอกนั้น → allow
- ถ้าไฟล์อ่านไม่ได้หรือ JSON เสีย คำสั่งแก้ไข USER จะถูกบล็อกไว้ก่อน (fail closed)
- build จะเก็บไฟล์เดิมข้ามเวอร์ชัน แต่ fresh install จะสร้างไฟล์และย้าย `JMS_USER.blocked_keywords` เดิมให้อัตโนมัติ

## Config (`config.ini`)
- `[PATH]`: `output_dir`
- `[FEISHU]`: `app_id`, `app_secret`, `chat_id`, `verify_token`, `bot_port` (7000), `ngrok_url`, `auth_token` (JMS), `bot_name` (default `BOT_JMSKKN`)
- `[TIME]`: `run_minute` (5), `start_hour` (15), `end_hour` (12)
- `[DWS_JMS]`: `dws_url`, `dws_token`, `jms_token`, ชื่อไฟล์ export (`name_dws`, `name_auto`, `name_dwspda`, `name_realtime_db`), ช่วงเวลา (`start_hour`, `end_hour`; `start_date` / `end_date` ไม่บันทึกแล้ว — วันที่ตามรอบงานเสมอ เลือกเองได้เฉพาะตอนโปรแกรมเปิดอยู่), `raw_path`, `download_size`, flags `enabled` / `send_*_file`
- `[DWS1]..[DWS9-11]`: `ip`, `port` ของเครื่อง agent (default subnet `10.30.32.x`, พอร์ต 4000/4001)
- `[WORKBOOKS]` / `[EXPORTS]`: `items` — รายการไฟล์ Excel/ชีต ที่จัดการในหน้า Workbooks
- หมายเหตุ: `core/config.ini` เป็นไฟล์แยกของ `core/*` (มี `[FEISHU]`, `[NGROK]`)
- Build เก็บ `metrics_config.yaml` สองตำแหน่ง: สำเนาที่ผู้ใช้แก้ได้ข้าง EXE และสำเนากู้คืน read-only ใน `_internal`; ไฟล์เดิมของผู้ใช้มีสิทธิ์สูงสุดและไม่ถูกเขียนทับ

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
