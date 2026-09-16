from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Createphoto


def set_workbook_path(config, name_fragment: str, workbook_path: Path) -> None:
    keys = [key.strip() for key in config["WORKBOOKS"].get("items", "").split(",") if key.strip()]
    for key in keys:
        section = f"WORKBOOK:{key}"
        if section not in config:
            continue
        descriptor = " ".join(
            [
                key,
                config[section].get("display_name", ""),
                Path(config[section].get("path", "")).name,
            ]
        ).lower()
        if name_fragment.lower() in descriptor:
            config[section]["path"] = str(workbook_path.resolve())
            return
    raise KeyError(f"Workbook matching {name_fragment!r} was not found")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--auto-workbook", type=Path)
    parser.add_argument("--dws-workbook", type=Path)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    Createphoto.get_business_date = lambda _start_hour: args.business_date

    if args.auto_workbook or args.dws_workbook:
        config = Createphoto.load_config()
        Createphoto.migrate_old_export_config(config)
        if args.auto_workbook:
            set_workbook_path(config, "autopacking", args.auto_workbook)
        if args.dws_workbook:
            set_workbook_path(config, "dws", args.dws_workbook)
        Createphoto.load_config = lambda: config
        Createphoto.save_config = lambda _config: None

    started = time.perf_counter()
    Createphoto.run_create(
        str(args.output_dir.resolve()),
        log=lambda message: print(message, flush=True),
        is_running=lambda: True,
    )
    print(f"FULL_RUN_SECONDS={time.perf_counter() - started:.3f}", flush=True)


if __name__ == "__main__":
    main()
