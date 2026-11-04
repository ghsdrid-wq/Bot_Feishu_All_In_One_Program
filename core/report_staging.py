"""Stage report inputs locally without replacing a successful snapshot on failure."""

import os
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
        os.utime(temp, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def stage(
    paths, config, business_date, now, start_hour, keep_running, log, window=None
):
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
    root = cache_root(config)
    if window is not None:
        for slot in window.slots:
            key = f"{(slot.hour + 1) % 24:02d}"
            target = (
                Path(root)
                / "slots"
                / slot.strftime("%Y-%m-%d")
                / f"{slot.hour:02d}.xlsx"
            )
            if not target.is_file():
                source = Path(paths[key])
                root_path = source.parent
                try:
                    datetime.strptime(root_path.name, "%Y-%m-%d")
                    root_path = root_path.parent
                except ValueError:
                    pass
                folder_date = (slot + timedelta(hours=1) - timedelta(hours=13)).date()
                candidates = [
                    root_path / folder_date.isoformat() / source.name,
                    source,
                    root_path / source.name,
                ]
                accepted = None
                for candidate in candidates:
                    try:
                        modified = datetime.fromtimestamp(candidate.stat().st_mtime)
                    except OSError:
                        continue
                    if slot <= modified <= slot + timedelta(hours=1, minutes=30):
                        accepted = candidate
                        break
                if accepted is None:
                    raise OSError(
                        f"AutoPacking {slot}: no source with matching hour provenance and no dated snapshot"
                    )
                copy_snapshot(accepted, target, keep_running)
            paths[slot.isoformat()] = str(target)
        return paths
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


def cache_root(config):
    from core import dws_mirror

    return config.get(
        "REPORT_CALCULATION", "auto_cache", fallback=""
    ).strip() or os.path.join(
        dws_mirror.core.PROJECT_ROOT, "out", "source_cache", "AutoPacking"
    )
