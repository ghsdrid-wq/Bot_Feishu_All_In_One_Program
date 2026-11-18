"""รายการช่องตั้งค่าทั้งหมด (ตรงกับหน้า ตั้งค่า / สรุปรายวัน ของโปรแกรมเดิม)

หน้าเว็บวาดฟอร์มจากรายการนี้เอง — เพิ่มช่องใหม่แค่เพิ่มบรรทัดที่นี่
ชนิด: text, secret (ไม่ส่งค่าจริงไปหน้าเว็บ), path, folder, bool, number, select, time, sections
"""
from __future__ import annotations

import daily_summary


def f(section, key, label, kind="text", hint="", default="", options=None):
    return {"section": section, "key": key.lower(), "label": label, "type": kind, "hint": hint,
            "default": default, "options": options or []}


SETTINGS_GROUPS = [
    {"id": "paths", "title": "โฟลเดอร์", "fields": [
        f("PATH", "output_dir", "ตำแหน่งเก็บรูป", "folder", "รูปตารางที่แคปจาก Excel / Pillow"),
        f("DWS_JMS", "raw_path", "ตำแหน่งเก็บ Excel (ไฟล์ดิบ)", "folder", "ว่าง = ใช้ตำแหน่งเก็บรูป"),
        f("PATH", "autopacking_dir", "โฟลเดอร์ AutoPacking", "folder"),
    ]},
    {"id": "rawfiles", "title": "ไฟล์ดิบ", "fields": [
        f("DWS_JMS", "name_dws", "ชื่อไฟล์ DWS9-11", default="DWS9-11.xlsx"),
        f("DWS_JMS", "name_auto", "ชื่อไฟล์ PDA AUTO", default="DWSXAUTOPDA.xlsx"),
        f("DWS_JMS", "name_dwspda", "ชื่อไฟล์ PDA DWS", default="DWSPDA.xlsx"),
        f("DWS_JMS", "name_realtime_db", "ชื่อไฟล์ Realtime DB", default="RealtimeDB.xlsx"),
        f("DWS_JMS", "send_dws_file", "ส่งไฟล์ DWS9-11 เข้า Feishu", "bool"),
        f("DWS_JMS", "send_auto_file", "ส่งไฟล์ PDA AUTO เข้า Feishu", "bool"),
        f("DWS_JMS", "send_dwspda_file", "ส่งไฟล์ PDA DWS เข้า Feishu", "bool"),
        f("DWS_JMS", "send_realtime_file", "ส่งไฟล์ Realtime DB เข้า Feishu", "bool"),
    ]},
    {"id": "jms", "title": "JMS และฐานข้อมูล DWS", "fields": [
        f("FEISHU", "jms_token", "JMS Token", "secret", "วาง token ใหม่เมื่อหมดอายุ"),
        f("DWS_JMS", "db_host", "DB Host", default="10.30.32.10"),
        f("DWS_JMS", "db_port", "DB Port", "number", default="3306"),
        f("DWS_JMS", "db_user", "DB User", default="root"),
        f("DWS_JMS", "db_password", "DB Password", "secret"),
        f("DWS_JMS", "db_name", "DB Name", default="dwsdb_thailand"),
    ]},
    {"id": "feishu", "title": "Feishu", "fields": [
        f("FEISHU", "app_id", "App ID", hint="ใช้ร่วมกันทั้งส่งรายงาน, webhook, DWS PLAN และ JMS USER"),
        f("FEISHU", "app_secret", "App Secret", "secret"),
        f("FEISHU", "chat_id", "Chat ID (กลุ่มรายงานหลัก)"),
        f("FEISHU", "bot_name", "ชื่อบอท", default="BOT_JMSKKN"),
        f("FEISHU", "bot_port", "พอร์ต webhook", "number", default="7000"),
        f("FEISHU", "verify_token", "Verify Token", "secret"),
        f("FEISHU", "ngrok_url", "NGROK URL"),
        f("FEISHU", "bot_autostart", "เปิด BOT DWS PLAN เองตอนเปิดโปรแกรม", "bool", default="true"),
        f("FEISHU", "jms_autostart", "เปิด BOT JMS USER เองตอนเปิดโปรแกรม", "bool", default="true"),
        f("FEISHU", "plan_day_name", "ชื่อแพลนกลางวัน", default="DWSA"),
        f("FEISHU", "plan_night_name", "ชื่อแพลนกลางคืน", default="DWSB"),
    ]},
    {"id": "report", "title": "รายงานรายชั่วโมง", "fields": [
        f("TIME", "date_roll_hour", "เปลี่ยนวันของรอบงานตอน (ชั่วโมง)", "number", default="13"),
        f("EXPORTS", "send_as_card", "ส่งรูปเป็นการ์ด", "bool"),
    ]},
]

DS_SECTIONS = [("autopacking", "AutoPacking"), ("auto_pda", "AUTO PDA"), ("pda_bag", "กระสอบซิปแดง"),
               ("dws_pda", "DWS PDA"), ("dws", "DWS"), ("abnormal", "ตาราง Error")]

DS = daily_summary.SECTION
DAILY_GROUPS = [
    {"id": "send", "title": "การส่ง", "fields": [
        f(DS, "chat_id", "Chat ID", hint="ว่าง = ใช้ Chat ID ในหน้าตั้งค่า"),
        f(DS, "auto_send", "ส่งอัตโนมัติทุกวัน (ขณะเปิด Auto)", "bool", default="true"),
        f(DS, "send_time", "เวลาส่ง", "time", "ไม่เกิน 12:59", default="12:00"),
        f(DS, "send_catch_up", "ส่งย้อนหลังถ้าพลาดเวลาส่ง", "bool", default="true"),
        f(DS, "send_late_minutes", "ส่งย้อนหลังได้ภายใน (นาที)", "number", default="30"),
        f(DS, "sections", "ตารางที่ส่ง", "sections", options=[{"value": k, "label": t} for k, t in DS_SECTIONS]),
        f(DS, "fresh_raw_export", "ดึงไฟล์ JMS ของช่วงสรุปใหม่ก่อนสรุป", "bool", default="true"),
    ]},
    {"id": "raw", "title": "ไฟล์ข้อมูลดิบ", "fields": [
        f(DS, "send_raw_file", "ส่งไฟล์ข้อมูลดิบ", "bool", default="true"),
        f(DS, "raw_file_name", "ชื่อไฟล์", hint="{date} = วันที่รอบงาน"),
        f(DS, "raw_file_max_mb", "ขนาดสูงสุด (MB)", "number", "เกิน = บีบ zip (Feishu รับไม่เกิน 30MB)"),
        f(DS, "backup_dir", "โฟลเดอร์บันทึกไฟล์ Excel", "folder"),
    ]},
    {"id": "titles", "title": "หัวข้อ", "fields": [
        f(DS, k, label, hint="{date} {start} {end}") for k, label in [
            ("title_post", "หัวข้อข้อความ"), ("title_autopacking", "AutoPacking"), ("title_auto_pda", "AUTO PDA"),
            ("title_pda_bag", "กระสอบซิปแดง"), ("title_dws_pda", "DWS PDA"), ("title_dws", "DWS"),
            ("title_abnormal", "ตาราง Error")]
    ]},
]
_shown = {fld["key"] for g in DAILY_GROUPS for fld in g["fields"]} | {"last_sent_date"}
DAILY_GROUPS.append({"id": "advanced", "title": "ขั้นสูง", "collapsed": True, "fields": [
    f(DS, k, k, "bool" if v in ("true", "false") else "text", default=v)
    for k, v in daily_summary.DEFAULTS.items() if k not in _shown
]})

SECRET_TYPES = {"secret"}


def read_values(config, groups) -> dict:
    out: dict[str, dict] = {}
    for g in groups:
        for fld in g["fields"]:
            sec, key = fld["section"], fld["key"]
            raw = config.get(sec, key, fallback=fld["default"]) if sec in config else fld["default"]
            if fld["type"] in SECRET_TYPES:
                raw = f"••••{raw[-4:]}" if len(raw) > 4 else ("••••" if raw else "")
            out.setdefault(sec, {})[key] = raw
    return out


def write_values(config, groups, values: dict) -> int:
    """values: {section: {key: value}} — secret ว่าง/ตัวปิดบัง = ไม่เปลี่ยน คืนจำนวนช่องที่เปลี่ยน"""
    allowed = {(fld["section"], fld["key"]): fld for g in groups for fld in g["fields"]}
    changed = 0
    for sec, kv in values.items():
        for key, val in kv.items():
            fld = allowed.get((sec, key.lower()))
            if not fld:
                continue
            val = "" if val is None else str(val)
            if fld["type"] in SECRET_TYPES:
                if not val.strip() or val.startswith("••••"):
                    continue
                val = "".join(val.split())
            if fld["type"] == "bool":
                val = "true" if val.lower() in ("1", "true", "yes", "on") else "false"
            if sec not in config:
                config[sec] = {}
            if config[sec].get(key.lower(), None) != val.strip():
                config[sec][key.lower()] = val.strip()
                changed += 1
            # JMS token เก็บ 3 ที่ให้ทุกส่วนของโปรแกรมเดิมอ่านเจอ
            if (sec, key.lower()) == ("FEISHU", "jms_token"):
                config["FEISHU"]["auth_token"] = val.strip()
                if "DWS_JMS" not in config:
                    config["DWS_JMS"] = {}
                config["DWS_JMS"]["jms_token"] = val.strip()
    return changed
