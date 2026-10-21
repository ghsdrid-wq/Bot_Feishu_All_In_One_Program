"""Update only the native DataMashup package, preserving other XLSX parts."""
from __future__ import annotations

import argparse
import base64
import io
import hashlib
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from copy import copy
from datetime import datetime
from pathlib import Path

import win32crypt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = re.compile(r'File\.Contents\("([^"]+)"\)', re.IGNORECASE)
QUERY = re.compile(r'(shared\s+REALTIME_DB\s*=\s*)(.*?);(?=\s*(?:shared\s|$))', re.DOTALL)


def replacement_xml(xml: bytes, formula: str) -> bytes:
    root = ET.fromstring(xml)
    blob = base64.b64decode(root.text)
    size = int.from_bytes(blob[4:8], "little")
    output = io.BytesIO()
    changed = 0
    with zipfile.ZipFile(io.BytesIO(blob[8:8+size])) as original, zipfile.ZipFile(output, "w") as target:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename.endswith(".m"):
                text = content.decode("utf-8-sig")
                match = QUERY.search(text)
                if match:
                    source = SOURCE.search(match.group(2))
                    if source is None:
                        raise ValueError("REALTIME_DB has no File.Contents source")
                    updated = SOURCE.sub(lambda _: source.group(0), formula, count=1)
                    text = text[:match.start(2)] + updated.rstrip() + text[match.end(2):]
                    content = text.encode("utf-8")
                    changed += 1
            target.writestr(copy(info), content)
    if changed != 1:
        raise ValueError(f"Expected one REALTIME_DB query, found {changed}")
    package = output.getvalue()
    permissions_start = 8 + size
    permissions_size = int.from_bytes(blob[permissions_start:permissions_start+4], "little")
    permissions = blob[permissions_start+4:permissions_start+4+permissions_size]
    metadata_start = permissions_start + 4 + permissions_size
    metadata_size = int.from_bytes(blob[metadata_start:metadata_start+4], "little")
    bindings_start = metadata_start + 4 + metadata_size
    # MS-QDEFF 2.6: bind the edited package to unchanged permissions with DPAPI.
    hashes = b"".join((32).to_bytes(4, "little") + hashlib.sha256(part).digest()
                      for part in (package, permissions))
    bindings = win32crypt.CryptProtectData(hashes, None, b"DataExplorer Package Components", None, None, 0)
    blob = (blob[:4] + len(package).to_bytes(4, "little") + package
            + blob[permissions_start:bindings_start]
            + len(bindings).to_bytes(4, "little") + bindings)
    encoded = base64.b64encode(blob).decode("ascii")
    encoding = "utf-16" if xml.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    text = xml.decode(encoding)
    start = text.index(">", text.index("<DataMashup")) + 1
    end = text.index("</DataMashup>", start)
    text = text[:start] + encoded + text[end:]
    return text.encode(encoding)


def update(path: Path) -> None:
    path = path.resolve()
    backup = ROOT / "Backup" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-realtime-chinese") / path.name
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    formula = (ROOT / "Project-Template/modules/realtime_db_bilingual.pq").read_text(encoding="utf-8")
    staging = path.with_suffix(".query-update.tmp")
    try:
        with zipfile.ZipFile(path) as original:
            parts = []
            for name in original.namelist():
                if name.startswith("customXml/") and name.endswith(".xml"):
                    root = ET.fromstring(original.read(name))
                    if root.tag.split("}")[-1] == "DataMashup":
                        parts.append(name)
            if len(parts) != 1:
                raise ValueError("Expected exactly one DataMashup part")
            part = parts[0]
            replacement = replacement_xml(original.read(part), formula)
            with zipfile.ZipFile(staging, "w") as target:
                for info in original.infolist():
                    target.writestr(copy(info), replacement if info.filename == part else original.read(info.filename))
            with zipfile.ZipFile(staging) as target:
                assert target.testzip() is None
                assert all(original.read(name) == target.read(name) for name in original.namelist() if name != part)
        os.replace(staging, path)
    finally:
        staging.unlink(missing_ok=True)
    print("Updated REALTIME_DB; all XLSX parts except DataMashup are byte-identical")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    args = parser.parse_args()
    update(args.workbook)
