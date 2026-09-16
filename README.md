# Bot_Feishu_All_In_One_Program

โปรแกรม GUI (customtkinter ธีม Nord) ตัวเดียวที่รวมงานฝั่ง controller / report ของ DWS + JMS J&T เข้าด้วยกันเป็น **"All-In-One"** ทำงานร่วมกับ Feishu (Lark)

## ความสามารถ
- **Data Export (ตัวดึงยอด)** — ดึงยอดจาก MySQL DB ของ DWS และจาก JMS J&T API ออกมาเป็นไฟล์ Excel แล้วส่งเข้า Feishu
- **Workbook Manager** — เปิด Excel แคปภาพชีตตามช่วงเวลา แล้วส่งรูปเข้า Feishu chat
- **DWS Plan Controller** — สั่งเปลี่ยนแพลนการคัดแยก (sorting plan) ของเครื่อง DWS หลายเครื่องพร้อมกันผ่าน HTTP
- **JMS User Bot** — รับคำสั่งภาษาไทยผ่าน Feishu แล้วไป reset รหัส / ปลดล็อก user บนระบบ JMS
- **Auto Scheduler** — ตั้งเวลาให้ pipeline (ดึงยอด → แคปภาพ → ส่ง Feishu) รันเองตามรอบ

## เริ่มต้นใช้งาน

### 1. ติดตั้ง dependencies
```bash
pip install -r requirements.txt
```
> ต้องมี Microsoft Excel ติดตั้งบนเครื่อง (Workbook Manager ใช้ COM automation ผ่าน win32com) — รันได้เฉพาะ Windows

### 2. ตั้งค่า `config.ini`
สร้างไฟล์ `config.ini` ไว้ที่โฟลเดอร์เดียวกับโปรแกรม (ไฟล์นี้ถูก `.gitignore` ไว้เพราะมี token) ตัวอย่างคีย์หลัก:

```ini
[FEISHU]
app_id =
app_secret =
chat_id =
verify_token = mytoken
bot_port = 7000
auth_token =            ; token ของ JMS
bot_name = BOT_JMSKKN

[TIME]
run_minute = 5
start_hour = 15
end_hour = 12

[DWS_JMS]
dws_url =
dws_token =
jms_token =
name_dws = DWS9-11.xlsx
start_hour = 13:00
end_hour = 23:00

[DWS1]
ip = 10.30.32.32
port = 4000
; ... [DWS2]..[DWS9-11] ตามเครื่อง agent
```

### 3. รันโปรแกรม
```bash
python bot_main.py
```
- ตอนเปิด: โหลด/สร้าง `config.ini` และ start controller API (Flask) พอร์ต **6100**
- กด **START BOT** ในหน้า JMS User / DWS → เปิด Feishu webhook server (พอร์ตจาก `bot_port`, default 7000)
- ต้องมี reverse proxy / ngrok ชี้เข้ามาที่ webhook `/feishu_event`
- เปิดได้ครั้งละ 1 instance (single-instance lock)

## โครงสร้างโค้ด
ดูรายละเอียดโครงสร้างไฟล์ ฟังก์ชันสำคัญ และ config key ทั้งหมดได้ที่ [STRUCTURE.md](STRUCTURE.md)

| ไฟล์ | หน้าที่ |
|------|---------|
| `bot_main.py` | ตัวหลัก — UI ทุกหน้า + Feishu webhook + Data Export + scheduler |
| `Createphoto.py` | เปิด Excel แคปภาพชีต (`run_create`) |
| `Botmessage.py` | อัปโหลด + ส่งรูปเข้า Feishu (`run_send`) |
| `controller/controller_api.py` | Flask API พอร์ต 6100 (`/status`, `/switch_plan`, `/refresh`) |
| `core/jms_api.py` | เรียก JMS J&T (search / reset / enable user) |
| `core/feishu_api.py`, `core/config.py`, `core/logger.py` | Feishu token, config, log |
| `main.py` | entry point รุ่นเก่า (Tkinter, DWS Plan + JMS User เท่านั้น) |

## Dependencies / บริการภายนอก
- Python: `customtkinter`, `tkcalendar`, `requests`, `flask`, `waitress`, `pywin32`, `Pillow`, `pymysql`, `openpyxl`, `pyinstaller`
- Feishu OpenAPI (`open.feishu.cn`), JMS J&T (`jmsgw.jtexpress.co.th`)
- MySQL DB ของ DWS, เครื่อง DWS agent บน LAN, Microsoft Excel

## หมายเหตุ
- `auth_token` / `jms_token` ของ JMS หมดอายุได้ → อัปเดตในหน้า **ตั้งค่า** แล้วกด START BOT ใหม่ (มีระบบเช็ค token เชิงรุกแจ้งเตือนผ่าน Feishu)
- STOP BOT แค่ mark offline — ต้อง restart โปรแกรมเพื่อหยุด webhook server จริง
