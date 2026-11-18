"""FastAPI ของ Web UI — ทุกหน้าของโปรแกรมเดิม สั่งงานผ่าน Engine (ไม่มี Tk)"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import bot_main as legacy
import daily_summary
import feishu_client
import sent_messages
import vip_awb
from core import jms_policy
from vip_awb import core as vip_core

from . import schema
from .engine import PIPELINE_STEPS, REPORT_MODES, Engine
from .services import install_vip_capture, last_vip

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


class Body(BaseModel):
    model_config = {"extra": "allow"}


def create_app(picker=None, autostart: bool = True) -> FastAPI:
    install_vip_capture()
    eng = Engine(autostart=autostart)
    app = FastAPI(title="Auto Report Web UI", version=legacy.APP_VERSION)
    app.state.engine = eng

    def lines(items):
        return [x.strip() for x in str(items or "").split(",") if x.strip()]

    def need(cond, msg, code=400):
        if not cond:
            raise HTTPException(code, msg)

    # ================================================================ ภาพรวม / log
    @app.get("/api/engine")
    def engine_status():
        return eng.snapshot()

    @app.get("/api/logs/{channel}")
    def get_logs(channel: str, after: int = 0):
        need(channel in eng.logs, "ไม่มีช่อง log นี้", 404)
        return eng.logs[channel].since(after)

    @app.delete("/api/logs/{channel}")
    def clear_logs(channel: str):
        need(channel in eng.logs, "ไม่มีช่อง log นี้", 404)
        eng.logs[channel].clear()
        return {"ok": True}

    # ================================================================ BOT REPORT
    @app.get("/api/pipeline")
    def pipeline():
        steps = eng.selected_steps()
        return {
            "steps": [{"key": k, "title": t, "icon": i, "on": bool(steps.get(k))} for k, t, i, _ in PIPELINE_STEPS],
            "report_mode": eng.report_mode(),
            "report_modes": [{"value": k, "label": t} for k, t in REPORT_MODES],
            "summary_header": eng.summary_header_enabled(),
        }

    @app.put("/api/pipeline")
    def set_pipeline(body: Body):
        data = body.model_dump()
        dws = eng.config["DWS_JMS"]
        for key, on in (data.get("steps") or {}).items():
            if any(key == k for k, *_ in PIPELINE_STEPS):
                dws[f"step_{key}"] = "true" if on else "false"
        if data.get("report_mode") in [k for k, _ in REPORT_MODES]:
            dws["report_mode"] = data["report_mode"]
        if "summary_header" in data:
            eng.config["EXPORTS"]["add_summary_header"] = "true" if data["summary_header"] else "false"
        eng.save_config()
        return pipeline()

    @app.put("/api/schedule")
    def set_schedule(body: Body):
        data = body.model_dump()
        t = eng.config["TIME"]
        for key, lo, hi in (("run_minute", 0, 59), ("start_hour", 0, 23), ("end_hour", 0, 23)):
            if key in data:
                v = int(data[key])
                need(lo <= v <= hi, f"{key} ต้องอยู่ระหว่าง {lo}-{hi}")
                t[key] = str(v)
        if data.get("dates") == "auto":
            eng.reset_dates()
        elif data.get("start_date") and data.get("end_date"):
            eng.set_dates(date.fromisoformat(data["start_date"]), date.fromisoformat(data["end_date"]))
        eng.save_config()
        if eng.scheduler_running:
            eng.set_next_run_display(eng.get_next_scheduler_run_time())
        return eng.snapshot()

    @app.post("/api/run/{action}")
    def run_action(action: str):
        if action == "now":
            need(not eng.running, "มีรอบกำลังรันอยู่")
            eng.run_once()
            need(eng.running or not eng.task_queue.empty() or eng.runtime_locked,
                 "เริ่มไม่ได้ — ดูเหตุผลใน Live Log (เช่น ยังไม่ได้ตั้งโฟลเดอร์ / token)")
        elif action == "stop":
            eng.stop_process()
        elif action == "auto-start":
            eng.start_scheduler()
            need(eng.scheduler_running, "เปิด Auto ไม่ได้ — ดูเหตุผลใน Live Log")
        elif action == "auto-stop":
            eng.stop_scheduler()
        else:
            raise HTTPException(404, "ไม่รู้จักคำสั่ง")
        return eng.snapshot()

    # ---- ถอนข้อความ
    @app.get("/api/recall")
    def recall_list():
        rounds = sent_messages.load_rounds(eng.sent_messages_path())[:30]
        return [{"id": r.get("id"), "label": r.get("label", ""), "kind": r.get("kind", ""), "sent_at": r.get("sent_at", ""),
                 "status": sent_messages.round_status(r),
                 "left": len(r.get("messages") or []) - len(r.get("recalled") or [])} for r in rounds]

    @app.post("/api/recall/{round_id}")
    def recall(round_id: str):
        token = eng.get_tenant_access_token_for_file_send()
        done, errors = sent_messages.recall_round(eng.sent_messages_path(), round_id, token, log=eng.write_log)
        eng.write_log(f"ถอนข้อความ: สำเร็จ {done} ข้อความ" + (f" ผิดพลาด {len(errors)}" if errors else ""),
                      level="WARN" if errors else "SUCCESS")
        return {"done": done, "errors": errors}

    # ================================================================ ไฟล์ Excel
    def workbook_list():
        out = []
        exports = [n for n in lines(eng.config["EXPORTS"].get("items")) if f"EXPORT:{n}" in eng.config]
        for key in lines(eng.config["WORKBOOKS"].get("items")):
            sec = f"WORKBOOK:{key}"
            if sec not in eng.config:
                continue
            w = eng.config[sec]
            out.append({
                "key": key, "name": w.get("display_name", key), "path": w.get("path", ""),
                "exists": os.path.isfile(w.get("path", "")),
                "enabled": legacy.as_bool(w.get("enabled", "true"), True),
                "send_excel": legacy.as_bool(w.get("send_excel", "false"), False),
                "group": legacy.export_group_of(w),
                "exports": [{
                    "name": n, "enabled": legacy.as_bool(e.get("enabled", "true"), True),
                    "send_enabled": legacy.as_bool(e.get("send_enabled", "true"), True),
                    "delete_by_start": legacy.as_bool(e.get("delete_by_start", "false"), False),
                    "sheet": e.get("sheet", ""), "range": e.get("range", "A1:Z50"),
                    "file": e.get("file", f"export_{n}.png"), "caption": e.get("caption", ""),
                } for n in exports if (e := eng.config[f"EXPORT:{n}"]).get("workbook") == key],
            })
        return out

    @app.get("/api/workbooks")
    def get_workbooks():
        return workbook_list()

    @app.post("/api/workbooks")
    def add_workbook(body: Body):
        path = os.path.abspath(str(body.model_dump().get("path", "")).strip().strip('"'))
        need(path and os.path.isfile(path), f"ไม่พบไฟล์: {path}")
        keys = lines(eng.config["WORKBOOKS"].get("items"))
        need(path.lower() not in {eng.config[f"WORKBOOK:{k}"].get("path", "").lower() for k in keys
                                  if f"WORKBOOK:{k}" in eng.config}, "มีไฟล์นี้อยู่แล้ว")
        key = legacy.unique_key(keys, legacy.safe_key(path))
        keys.append(key)
        eng.config[f"WORKBOOK:{key}"] = {"path": path, "display_name": os.path.basename(path), "enabled": "true",
                                         "send_excel": "false"}
        eng.config["WORKBOOKS"]["items"] = ",".join(keys)
        eng.save_config()
        eng.write_log(f"Added workbook: {os.path.basename(path)}")
        return workbook_list()

    @app.put("/api/workbooks/{key}")
    def edit_workbook(key: str, body: Body):
        sec = f"WORKBOOK:{key}"
        need(sec in eng.config, "ไม่พบไฟล์นี้", 404)
        data, w = body.model_dump(), eng.config[sec]
        for k in ("enabled", "send_excel"):
            if k in data:
                w[k] = "true" if data[k] else "false"
        if "group" in data:
            w["group"] = str(data["group"]).strip()
        if data.get("path"):
            p = os.path.abspath(str(data["path"]).strip().strip('"'))
            need(os.path.isfile(p), f"ไม่พบไฟล์: {p}")
            w["path"], w["display_name"] = p, os.path.basename(p)
        eng.save_config()
        return workbook_list()

    @app.delete("/api/workbooks/{key}")
    def delete_workbook(key: str):
        eng.delete_workbook(key)
        return workbook_list()

    @app.post("/api/workbooks/{key}/exports")
    def add_export(key: str):
        need(f"WORKBOOK:{key}" in eng.config, "ไม่พบไฟล์นี้", 404)
        eng.add_export_for_workbook(key)
        return workbook_list()

    @app.put("/api/exports/{name}")
    def edit_export(name: str, body: Body):
        sec = f"EXPORT:{name}"
        need(sec in eng.config, "ไม่พบรายการนี้", 404)
        data, e = body.model_dump(), eng.config[sec]
        for k in ("enabled", "send_enabled", "delete_by_start"):
            if k in data:
                e[k] = "true" if data[k] else "false"
        for k in ("sheet", "range", "file", "caption"):
            if k in data:
                e[k] = str(data[k]).strip()
        e.pop("group", None)
        eng.save_config()
        return workbook_list()

    @app.delete("/api/exports/{name}")
    def delete_export(name: str):
        eng.delete_export(name)
        return workbook_list()

    # ================================================================ DWS PLAN
    @app.get("/api/dws")
    def dws_state():
        plans = {"day": eng.get_plan_alias_value("day") if hasattr(eng, "get_plan_alias_value") else "",
                 "night": eng.get_plan_alias_value("night") if hasattr(eng, "get_plan_alias_value") else ""}
        return {"bot_running": eng.bot_running, "plans": eng.dynamic_plans, "aliases": plans,
                "clients": eng.controller_clients, "rows": eng.controller_rows, "rows_at": eng.controller_rows_at}

    @app.post("/api/dws/refresh")
    def dws_refresh():
        eng.refresh_status()
        return {"ok": True}

    @app.post("/api/dws/switch")
    def dws_switch(body: Body):
        plan = str(body.model_dump().get("plan", "")).strip()
        need(plan, "ไม่ได้เลือกแพลน")
        eng.switch_plan(plan)
        return {"ok": True}

    @app.post("/api/dws/plans/reload")
    def dws_plans():
        eng.load_dynamic_plans()
        return dws_state()

    @app.post("/api/bot/{which}/{action}")
    def bot(which: str, action: str):
        need(which in ("feishu", "jms") and action in ("start", "stop"), "ไม่รู้จักคำสั่ง", 404)
        if which == "feishu":
            eng.start_feishu_bot() if action == "start" else eng.stop_feishu_bot()
        else:
            eng.start_jms_bot() if action == "start" else eng.stop_jms_placeholder()
        return {"bot_running": eng.bot_running, "jms_running": eng.jms_running}

    @app.put("/api/dws/clients")
    def dws_clients(body: Body):
        for row in body.model_dump().get("clients") or []:
            name = str(row.get("name", "")).strip()
            if not name.startswith("DWS"):
                continue
            if name not in eng.config:
                eng.config[name] = {}
            sec = eng.config[name]
            sec["IP"] = str(row.get("ip", "")).strip()
            sec["PORT"] = str(row.get("port", "")).strip()
            if "pull_folder" in row:
                sec["pull_folder"] = str(row.get("pull_folder", "")).strip()
                sec["pull_file"] = str(row.get("pull_file", "")).strip()
                sec["pull_enabled"] = "true" if row.get("pull_enabled") else "false"
        eng.save_config()
        return dws_state()

    # ================================================================ สิทธิ์รหัส
    @app.get("/api/policy")
    def get_policy():
        return jms_policy.load_policy(eng.jms_policy_path)

    @app.put("/api/policy")
    def put_policy(body: Body):
        data = body.model_dump()
        try:
            saved = jms_policy.save_policy({"version": jms_policy.POLICY_VERSION,
                                            "blocked_prefixes": data.get("blocked_prefixes") or [],
                                            "exempt_codes": data.get("exempt_codes") or []}, eng.jms_policy_path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))
        eng.jms_log("[POLICY] updated from Web UI")
        return saved

    @app.post("/api/policy/parse")
    def parse_policy(body: Body):
        data = body.model_dump()
        valid, invalid, dup = jms_policy.parse_bulk_entries(data.get("text", ""), data.get("kind", "exempt_codes"))
        return {"valid": valid, "invalid": invalid, "duplicates": dup}

    @app.get("/api/policy/check/{code}")
    def check_code(code: str):
        d = jms_policy.evaluate_code(code, eng.jms_policy_path)
        return {"code": code, "allowed": bool(getattr(d, "allowed", False)), "reason": getattr(d, "reason", "")}

    # ================================================================ ตั้งค่า
    @app.get("/api/settings")
    def get_settings():
        return {"groups": schema.SETTINGS_GROUPS, "values": schema.read_values(eng.config, schema.SETTINGS_GROUPS)}

    @app.put("/api/settings")
    def put_settings(body: Body):
        n = schema.write_values(eng.config, schema.SETTINGS_GROUPS, body.model_dump().get("values") or {})
        eng.save_config()
        eng.write_log(f"บันทึกการตั้งค่าแล้ว ({n} ช่อง)", level="SUCCESS")
        return get_settings()

    # ================================================================ สรุปรายวัน
    @app.get("/api/daily")
    def get_daily():
        start, end = eng.daily_summary_range()
        return {"groups": schema.DAILY_GROUPS, "values": schema.read_values(eng.config, schema.DAILY_GROUPS),
                "busy": eng.ds_busy, "status": eng.daily_summary_status[0],
                "last_sent": eng.daily_summary_option("last_sent_date"), "range": {"start": start, "end": end}}

    @app.put("/api/daily")
    def put_daily(body: Body):
        schema.write_values(eng.config, schema.DAILY_GROUPS, body.model_dump().get("values") or {})
        eng.save_config()
        eng.write_log("บันทึกการตั้งค่าสรุปรายวันแล้ว", level="SUCCESS")
        return get_daily()

    @app.post("/api/daily/run")
    def run_daily(body: Body):
        data = body.model_dump()
        try:
            start = datetime.fromisoformat(data["start"])
            end = datetime.fromisoformat(data["end"])
        except Exception:  # noqa: BLE001
            raise HTTPException(400, "ช่วงเวลาไม่ถูกต้อง")
        err = eng.start_daily_summary(start, end, bool(data.get("send")))
        need(not err, err or "")
        return {"started": True}

    # ================================================================ VIP AWB
    @app.get("/api/vip")
    def vip_state():
        cfg = vip_awb.load_settings(eng.config)
        sec = eng.config[vip_core.SECTION]
        return {
            "busy": eng.vip_busy, "status": eng.vip_status_text[0], "last": last_vip(),
            "stage_on": eng.selected_steps().get("vip_awb", False),
            "settings": {"chat_id": cfg.chat_id, "day_start_hour": cfg.day_start_hour, "send_image": cfg.send_image,
                         "send_excel": cfg.send_excel, "title": cfg.title,
                         "brands": [{"name": n, "terms": t} for n, t in cfg.keywords.items()],
                         "scan_types": sec.get("scan_types", ""), "lookup_prefixes": sec.get("lookup_prefixes", ""),
                         "customer_code_prefix": sec.get("customer_code_prefix", "")},
        }

    @app.put("/api/vip")
    def put_vip(body: Body):
        s = body.model_dump()
        brands = [b for b in s.get("brands") or [] if str(b.get("name", "")).strip()]
        need(brands, "ต้องมีแบรนด์อย่างน้อย 1 แบรนด์")
        vip_awb.ensure_defaults(eng.config)
        sec = eng.config[vip_core.SECTION]
        sec["chat_id"] = str(s.get("chat_id", "")).strip()
        sec["day_start_hour"] = str(int(s.get("day_start_hour", 12)) % 24)
        sec["send_image"] = "true" if s.get("send_image", True) else "false"
        sec["send_excel"] = "true" if s.get("send_excel", True) else "false"
        sec["title"] = str(s.get("title", "")).strip() or vip_core.DEFAULTS["title"]
        sec["brands"] = "\n".join(
            f"{b['name'].strip()} = {', '.join(t.strip() for t in b.get('terms') or [] if t.strip()) or b['name'].strip()}"
            for b in brands)
        for k in ("scan_types", "lookup_prefixes"):
            sec[k] = str(s.get(k, "")).strip() or vip_core.DEFAULTS[k]
        sec["customer_code_prefix"] = str(s.get("customer_code_prefix", "")).strip()
        eng.save_config()
        eng.write_log(f"บันทึกการตั้งค่า VIP AWB แล้ว ({len(brands)} แบรนด์)", level="SUCCESS")
        return vip_state()

    @app.post("/api/vip/run")
    def vip_run(body: Body):
        need(not eng.vip_busy, "VIP AWB กำลังทำงานอยู่ รอให้เสร็จก่อน", 409)
        eng.run_vip_awb_in_background(send=bool(body.model_dump().get("send")), force=True)
        return {"started": True}

    @app.get("/api/vip/file/{name}")
    def vip_file(name: str):
        base = os.path.dirname(os.path.abspath(legacy.CONFIG_FILE))
        path = os.path.join(base, "VipAwb", os.path.basename(name))
        need(os.path.isfile(path), "ไม่พบไฟล์", 404)
        return FileResponse(path, filename=os.path.basename(path))

    # ================================================================ Dashboard / ระบบ
    @app.get("/api/dashboard")
    def dashboard():
        try:
            url = eng.dashboard_web_url()
        except Exception as e:  # noqa: BLE001
            url, _ = "", e
        return {"started": bool(getattr(eng, "dash_web_started", False)), "status": eng.dashboard_web_status, "url": url}

    @app.post("/api/dashboard/start")
    def dashboard_start():
        eng.dashboard_start_web(auto=True)
        return dashboard()

    @app.post("/api/pick")
    def pick(body: Body):
        kind = body.model_dump().get("kind", "folder")
        need(picker is not None, "เลือกไฟล์ได้เฉพาะในหน้าต่างโปรแกรม — พิมพ์ที่อยู่เองแทน")
        return {"path": picker(kind) or ""}

    # ================================================================ หน้าเว็บ
    if os.path.isdir(STATIC_DIR):
        app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = os.path.join(STATIC_DIR, path)
            if path and os.path.isfile(target):
                return FileResponse(target)
            return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    return app
