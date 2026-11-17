# Bot_Feishu_All_In_One_Program

โปรแกรม GUI (customtkinter ธีม Nord) ตัวเดียวที่รวมงานฝั่ง controller / report ของ DWS + JMS J&T เข้าด้วยกันเป็น **"All-In-One"** ทำงานร่วมกับ Feishu (Lark)

## ความสามารถ
- **Data Export (ตัวดึงยอด)** — ดึงยอดจาก MySQL DB ของ DWS และจาก JMS J&T API ออกมาเป็นไฟล์ Excel แล้วส่งเข้า Feishu
- **Workbook Manager** — เปิด Excel แคปภาพชีตตามช่วงเวลา แล้วส่งรูปเข้า Feishu chat
- **DWS Plan Controller** — สั่งเปลี่ยนแพลนการคัดแยก (sorting plan) ของเครื่อง DWS หลายเครื่องพร้อมกันผ่าน HTTP
- **JMS User Bot** — รับคำสั่งภาษาไทยผ่าน Feishu แล้วไป reset รหัส / ปลดล็อก user บนระบบ JMS
- **JMS Access Policy** — จัดการหัวรหัสที่บล็อกและรหัสละเว้นแบบวางหลายรายการ ค้นหา นำเข้า และส่งออก
- **Auto Scheduler** — ตั้งเวลาให้ pipeline (ดึงยอด → แคปภาพ → ส่ง Feishu) รันเองตามรอบ
- **VIP AWB** (หน้า ★ VIP AWB) — ทุกชั่วโมงส่งยอดพัสดุ 8 แบรนด์ VIP (UNIQLO, ADIDAS, ม.กรุงเทพ, VIVO, XIAOMI, WATSONS, AIS, OPPO)
  ที่เข้า KKN_GW พร้อมสถานะ ออกแล้ว / คงค้าง / หลุดสแกน เป็นการ์ด + รูปตารางรายชั่วโมง + Excel
  ส่งอัตโนมัติ: ติ๊กการ์ด VIP AWB ในลำดับการทำงานของหน้า BOT REPORT แล้วเปิด Auto ทดสอบนอกโปรแกรม: `python -m vip_awb --dry-run`

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
- ตั้ง `verify_token` ให้ตรงกับ Feishu Event Subscription; `/feishu_event`, `/switch_plan` และ controller `/refresh` จะปฏิเสธคำขอที่ไม่มี token หรือ token ไม่ตรง
- เปิดได้ครั้งละ 1 instance (single-instance lock)

## การออกเวอร์ชันและ Build

เลขเวอร์ชันมีแหล่งเดียวที่ `app_version.py` ทั้งชื่อในโปรแกรมและชื่อโฟลเดอร์
Build จะอ่านจากไฟล์นี้อัตโนมัติ ห้ามเขียนเลขเวอร์ชันซ้ำในไฟล์อื่น

```powershell
# ดูเวอร์ชันปัจจุบัน
python tools/bump_version.py --show

# งานแก้ไขรอบถัดไป: 2.0.0 -> 2.0.1
python tools/bump_version.py patch

# จากนั้น build
.\build_onedir.ps1
```

ตัวอย่างผลลัพธ์:

- เวอร์ชัน `2.0.0` → `dist\AutoReportFeishuV2-0-0`
- เวอร์ชัน `2.0.1` → `dist\AutoReportFeishuV2-0-1`

ตอน build เวอร์ชันใหม่ ระบบจะนำ `config.ini`, `metrics_config.yaml`,
`jms_user_policy.json` และ `store.db`
จาก release ล่าสุดติดมาด้วยอัตโนมัติ จึงไม่ต้องตั้งค่าระบบใหม่ทุกครั้ง

ใช้ `minor` เมื่อเพิ่มความสามารถชุดใหญ่ และ `major` เมื่อมีการเปลี่ยนที่เข้ากันกับ
เวอร์ชันเดิมไม่ได้ เช่น `python tools/bump_version.py minor`

## Deploy ไปเครื่องรัน (ผ่าน SSH)

เครื่องรันเก็บแต่ละเวอร์ชันไว้ที่ `C:\0Niphonk\Programs\AutoReportFeishuV<เวอร์ชัน>`
ใช้ SSH key (Host `runpc` ใน `%USERPROFILE%\.ssh\config`) ไม่ต้องใช้รหัสผ่าน

```powershell
.\deploy-ssh.ps1 -Build      # build แล้ววางเวอร์ชันใหม่ไว้ข้างตัวเก่า (ไม่แตะตัวที่รันอยู่)
.\deploy-ssh.ps1 -Switch     # ปิดตัวเก่า แล้วเปิดตัวใหม่บนหน้าจอผู้ใช้
```

- ไม่ส่ง `config.ini`, `metrics_config.yaml`, `jms_user_policy.json`, `store.db`, `sent_messages.json`
  จากเครื่องนี้ แต่คัดลอกจากโฟลเดอร์ที่รันอยู่บนเครื่องรัน ตอน `-Switch` จะคัดลอกซ้ำหลังปิดตัวเก่า
- `-Switch` ไม่ยอมปิดโปรแกรมช่วง `[TIME] run_minute` ถึง +15 นาที (รอบรายงานรายชั่วโมง) ใช้ `-Force` ข้ามได้
- **หลัง `-Switch` โหมด Auto ไม่เปิดเอง** ต้องกด Start Auto ที่หน้า BOT REPORT
- เปิดตัวใหม่ด้วยบัญชีเจ้าของโปรแกรมที่รันอยู่ (หรือผู้ใช้ที่ล็อกอินหน้าจอ) ถ้าหาไม่ได้จะหยุดก่อนปิดตัวเก่า
- ทุกครั้งที่วางไฟล์จะรีเซ็ตสิทธิ์โฟลเดอร์ให้รับจาก `C:\0Niphonk\Programs` (ไม่งั้นโปรแกรมเซฟ `config.ini` ไม่ได้)
- exe อยู่โฟลเดอร์ใหม่ทุกเวอร์ชัน Windows Firewall จะถามสิทธิ์บนจอเครื่องรัน ต้องกดอนุญาตที่เครื่องรันเอง
  (Feishu Bot / JMS bot เปิดเองตาม `bot_autostart` / `jms_autostart`)
- ถ้าเวอร์ชันเดียวกับที่รันอยู่ ต้องใช้ `-Switch` จะเปลี่ยนเฉพาะไฟล์โปรแกรม และเก็บของเดิมไว้ใน `_previous\`

## โครงสร้างโค้ด
ดูรายละเอียดโครงสร้างไฟล์ ฟังก์ชันสำคัญ และ config key ทั้งหมดได้ที่ [STRUCTURE.md](STRUCTURE.md)

| ไฟล์ | หน้าที่ |
|------|---------|
| `bot_main.py` | ตัวหลัก — UI ทุกหน้า + Feishu webhook + Data Export + scheduler |
| `Createphoto.py` | เปิด Excel แคปภาพชีต (`run_create`) |
| `daily_summary.py` | Daily Summary — สรุปยอดข้อความเดียว + ไฟล์ข้อมูลดิบรวม ส่งวันละครั้งตามเวลาที่ตั้ง (หน้า สรุปรายวัน) |
| `Botmessage.py` | อัปโหลด + ส่งรูปเข้า Feishu (`run_send`) |
| `controller/controller_api.py` | Flask API พอร์ต 6100 (`/status`, `/switch_plan`, `/refresh`) |
| `core/jms_api.py` | เรียก JMS J&T (search / reset / enable user) |
| `core/jms_policy.py` | โหลด/บันทึก/ตรวจหัวรหัสบล็อกและรหัสละเว้นแบบ fail-closed |
| `core/config.py`, `core/logger.py` | config, log |

## Dependencies / บริการภายนอก
- Python: `customtkinter`, `tkcalendar`, `requests`, `flask`, `waitress`, `pywin32`, `Pillow`, `pymysql`, `openpyxl`, `pyinstaller`
- Feishu OpenAPI (`open.feishu.cn`), JMS J&T (`jmsgw.jtexpress.co.th`)
- MySQL DB ของ DWS, เครื่อง DWS agent บน LAN, Microsoft Excel

## หมายเหตุ
- `auth_token` / `jms_token` ของ JMS หมดอายุได้ → อัปเดตในหน้า **ตั้งค่า** แล้วกด START BOT ใหม่ (มีระบบเช็ค token เชิงรุกแจ้งเตือนผ่าน Feishu)
- STOP BOT แค่ mark offline — ต้อง restart โปรแกรมเพื่อหยุด webhook server จริง
