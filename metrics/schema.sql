-- =====================================================================
-- schema.sql — fact store ของ Dashboard (SQLite)
--
-- หลักการ: ทุกแหล่ง (AutoPacking / DWS1-8 / DWS9-11 / JMS) ยุบลงตาราง
-- เดียวกันที่ grain = (วันรอบงาน, ชั่วโมง, จุดปล่อยพัสดุ 1 จุด)
-- แล้วค่อย pivot ตอน query — ไม่เก็บ raw รายชิ้น
-- =====================================================================

PRAGMA journal_mode = WAL;      -- ให้ FastAPI อ่านพร้อม ingest เขียนได้
PRAGMA synchronous = NORMAL;

-- ---------------------------------------------------------------------
-- dim_station — master list ของ "จุดปล่อยพัสดุ" ทุกจุดที่ควรมีในตาราง
-- จำเป็นเพราะ raw จะ "ไม่มีแถว" เวลาเครื่องไม่ทำงาน (ไม่ใช่แถวที่มีค่า 0)
-- ต้อง LEFT JOIN กับตารางนี้เสมอ ไม่งั้นแถวหายจากรายงาน
-- ---------------------------------------------------------------------
-- in_service แยกจาก active โดยตั้งใจ:
--   active     = 0 คือเลิกใช้ถาวร ไม่ต้องแสดงในตารางแล้ว
--   in_service = 0 คือ "ยังไม่ได้ใช้งาน แต่อาจได้ใช้ในอนาคต"
--                ยังแสดงในตาราง (เป็น '-') แต่ไม่นับเป็นของเสีย ไม่แจ้งเตือน
CREATE TABLE IF NOT EXISTS dim_station (
    source       TEXT NOT NULL,   -- 'AUTOPACK' | 'DWS' | 'PDA'
    line         TEXT NOT NULL,   -- 'AP1','AP2' | 'DWS'
    station      TEXT NOT NULL,   -- 'AP1-01'.. | 'DWS_01'..'DWS_11'
    display_name TEXT NOT NULL,   -- ชื่อที่โชว์บน dashboard
    sort_order   INTEGER NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    in_service   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, station)
);

-- ---------------------------------------------------------------------
-- fact_hourly — ยอดต่อ 1 จุด ต่อ 1 ชั่วโมง
--
-- status สำคัญมาก: แยก 3 กรณีที่รายงาน Excel เดิมรวมเป็น "0" หมด
--   'ok'       มีข้อมูล ยอด > 0
--   'zero'     มีข้อมูล แต่ยอด = 0 (เครื่องเปิดอยู่ ไม่มีของ)
--   'downtime' มีข้อมูลว่าเครื่องหยุด (จาก Remark/online_minutes)
--   'no_data'  ยังไม่ถึงชั่วโมงนั้น หรืออ่านไฟล์ไม่ได้ -> แสดงเป็น '-'
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_hourly (
    business_date   TEXT    NOT NULL,  -- 'YYYY-MM-DD' วันรอบงาน (ตัดที่ 12:00)
    shift           TEXT    NOT NULL,  -- 'A' | 'B'
    hour_start      INTEGER NOT NULL,  -- 0-23 ชั่วโมงจริงของข้อมูล
    source          TEXT    NOT NULL,
    line            TEXT    NOT NULL,
    station         TEXT    NOT NULL,
    qty             INTEGER NOT NULL DEFAULT 0,  -- Loading Num / จำนวนสแกน
    valid_qty       INTEGER NOT NULL DEFAULT 0,  -- Loading Valid Num / สแกนสำเร็จ
    weight_kg       REAL    NOT NULL DEFAULT 0,
    online_minutes  INTEGER,                     -- จาก Duration of Feeder Online
    status          TEXT    NOT NULL DEFAULT 'no_data',
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (business_date, source, station, hour_start)
);

CREATE INDEX IF NOT EXISTS ix_fact_hourly_date
    ON fact_hourly (business_date, source);

-- ---------------------------------------------------------------------
-- fact_error — พัสดุที่ตก error แยกตามชนิด
--
-- line ต้องอยู่ใน PK ด้วย: AutoPacking มีช่อง error ของ AP1 และ AP2 แยกกัน
-- ถ้าไม่ใส่ line สองสายจะชนกันแล้วทับกันเอง ยอด error หายไปราวครึ่งหนึ่ง
--   AUTOPACK: error_type = Sort Source (最大循环件 / 无分拣计划)
--             station    = ช่อง error (264 -> '1号异常口', 352 -> '2号异常口')
--   DWS     : error_type = 分拣状态 (回流 / 未知)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_error (
    business_date TEXT    NOT NULL,
    shift         TEXT    NOT NULL,
    hour_start    INTEGER NOT NULL,
    source        TEXT    NOT NULL,
    line          TEXT    NOT NULL,
    station       TEXT    NOT NULL,
    error_type    TEXT    NOT NULL,
    qty           INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT    NOT NULL,
    PRIMARY KEY (business_date, source, line, station, error_type, hour_start)
);

CREATE INDEX IF NOT EXISTS ix_fact_error_date
    ON fact_error (business_date, source);

-- ---------------------------------------------------------------------
-- machine_health — สถานะแหล่งข้อมูลรายเครื่อง (DWS1-8 อ่านจาก share)
-- แยก 2 สัญญาณ เพราะ mtime สดไม่ได้แปลว่าเครื่องเดิน:
-- เจอเคส dws2.xlsx mtime=05:00 วันนี้ แต่ข้อมูลข้างในหยุดที่ 21:29 เมื่อวาน
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS machine_health (
    station         TEXT PRIMARY KEY,
    path            TEXT,
    reachable       INTEGER NOT NULL DEFAULT 0,  -- เปิดไฟล์ได้ไหม
    file_mtime      TEXT,                        -- exporter ยังเขียนอยู่ไหม
    last_data_time  TEXT,                        -- เครื่องยิงพัสดุล่าสุดเมื่อไร
    row_count       INTEGER,
    health          TEXT NOT NULL,               -- 'online'|'idle'|'stale'|'offline'
    detail          TEXT,
    checked_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- ingest_log — รอบการดึงข้อมูล ไว้ debug ว่ายอดมาจากไฟล์ไหน
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    source        TEXT NOT NULL,
    business_date TEXT,
    files_read    INTEGER DEFAULT 0,
    rows_written  INTEGER DEFAULT 0,
    warnings      TEXT,
    ok            INTEGER DEFAULT 0
);

-- ---------------------------------------------------------------------
-- v_station_daily — ยอดรวม/วัน/จุด พร้อม Effective Time ตามกติกา threshold
-- (ค่า min_qty ถูก bind มาจาก metrics_config.yaml ตอน query — ที่นี่ default 0)
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_station_daily AS
SELECT
    business_date,
    source,
    line,
    station,
    SUM(qty)                                        AS total_qty,
    SUM(CASE WHEN shift = 'A' THEN qty ELSE 0 END)  AS qty_shift_a,
    SUM(CASE WHEN shift = 'B' THEN qty ELSE 0 END)  AS qty_shift_b,
    MAX(qty)                                        AS peak_qty,
    SUM(weight_kg)                                  AS total_weight_kg,
    SUM(CASE WHEN status = 'downtime' THEN 1 ELSE 0 END) AS downtime_hours
FROM fact_hourly
GROUP BY business_date, source, line, station;
