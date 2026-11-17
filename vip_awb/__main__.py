r"""ทดสอบ VIP AWB นอกโปรแกรมหลัก (อ่าน config.ini ของโปรแกรม)

    python -m vip_awb --dry-run                         รันรอบปัจจุบัน ไม่ส่ง (พิมพ์ข้อความ + ที่อยู่ไฟล์)
    python -m vip_awb --dry-run --at "2026-11-18 12:05" จำลองเวลา
    python -m vip_awb --send [--force]                  ส่งเข้า Feishu จริง
    python -m vip_awb --config path\to\config.ini      ใช้ config อื่น (เช่นสำเนาจากเครื่องรัน)
"""
import argparse
import configparser
import logging
import os
import sys
from datetime import datetime

from . import core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m vip_awb")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.ini"))
    ap.add_argument("--base-dir", help="โฟลเดอร์เก็บ vip_awb.db / VipAwb (ค่าเริ่มต้น = โฟลเดอร์ของ config)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--at", help='เช่น "2026-11-18 12:05"')
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    cp = configparser.RawConfigParser()
    cp.read(args.config, encoding="utf-8-sig")
    core.ensure_defaults(cp)
    cfg = core.load_settings(cp)
    g = lambda s, k: cp.get(s, k, fallback="").strip()
    token = g("FEISHU", "jms_token") or g("FEISHU", "auth_token") or g("DWS_JMS", "jms_token")
    now = datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else datetime.now()
    res = core.run(cfg, token, args.base_dir or os.path.dirname(os.path.abspath(args.config)), now,
                   send=args.send and not args.dry_run, force=args.force,
                   app_id=g("FEISHU", "app_id"), app_secret=g("FEISHU", "app_secret"),
                   chat_id=cfg.chat_id or g("FEISHU", "chat_id"))
    print(res["text"])
    for f in (res["png"], res["xlsx"]):
        if f:
            print("ไฟล์:", f)
    print("ส่งแล้ว" if res["sent"] else ("ไม่ได้ส่ง: " + (res["skipped"] or "dry-run")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
