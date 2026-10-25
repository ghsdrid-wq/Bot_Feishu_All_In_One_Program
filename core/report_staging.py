"""Stage report inputs locally without replacing a successful snapshot on failure."""

import os
import shutil
import tempfile
import zipfile
from datetime import datetime, time, timedelta
from pathlib import Path


def copy_snapshot(source, target, keep_running=lambda: True):
    source, target = Path(source), Path(target)
    before = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target.parent, suffix=".part")
    os.close(fd)
    try:
        with source.open("rb") as src, open(temp, "wb") as dst:
            while chunk := src.read(1024 * 1024):
                if not keep_running():
                    raise InterruptedError("Source staging cancelled")
                dst.write(chunk)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError("Source changed during copy")
        with zipfile.ZipFile(temp) as archive:
            if archive.testzip() is not None:
                raise OSError("Invalid XLSX snapshot")
        shutil.copystat(source, temp)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def stage(paths, config, business_date, now, start_hour, keep_running, log):
    from core import dws_mirror

    paths = dict(paths)
    # Reuse the existing operator mirror configuration for every report entry point.
    for job in dws_mirror.plan():
        key = job["station"]
        if key not in paths:
            continue
        if not keep_running():
            raise InterruptedError("Source staging cancelled")
        result = dws_mirror.pull_one(job, lambda message, **kw: log(message))
        target = result.get("target", paths[key])
        paths[key] = target if os.path.isfile(target) else ""
        if not result["ok"]:
            log(f"[SOURCE] {key}: unreachable; retained snapshot may be incomplete")
    root = config.get("REPORT_CALCULATION", "auto_cache", fallback="").strip()
    root = root or os.path.join(
        dws_mirror.core.PROJECT_ROOT, "out", "source_cache", "AutoPacking"
    )
    reset = datetime.combine(business_date + timedelta(days=1), time(12))
    for key in paths:
        if not key.isdigit():
            continue
        hour = (int(key) - 1) % 24
        slot = datetime.combine(business_date + timedelta(days=hour < 12), time(hour))
        if (
            slot < datetime.combine(business_date, time(start_hour))
            or slot + timedelta(hours=1) > now
        ):
            continue
        target = Path(root) / business_date.isoformat() / f"{key}.xlsx"
        try:
            modified = datetime.fromtimestamp(Path(paths[key]).stat().st_mtime)
            if not slot <= modified < reset:
                raise OSError("Auto source predates the hour or was reset at noon")
            copy_snapshot(paths[key], target, keep_running)
        except InterruptedError:
            raise
        except OSError as error:
            if not target.is_file():
                raise OSError(
                    f"AutoPacking {key}: no snapshot for {business_date}: {error}"
                ) from error
            log(f"[SOURCE] AutoPacking {key}: retained business-day snapshot ({error})")
        paths[key] = str(target)
    return paths
