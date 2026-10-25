"""Calculate accepted report templates into atomic, values-only XLSX copies.

Workbook formatting stays in the template; source reads and formula evaluation
run in Python. Excel/Power Query are not required for this stage.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import posixpath
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from contextlib import ExitStack, contextmanager
from copy import copy
from datetime import date, datetime
from pathlib import Path

import openpyxl
from openpyxl.utils.datetime import to_excel

from core.report_formulas import Calculator, UnsupportedFormula
from core.report_sources import Inputs, template_paths

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
TAG = lambda name: f"{{{NS}}}{name}"
ET.register_namespace("", NS)
ET.register_namespace(
    "r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
SUPPORTED = ({"Autoformat"}, {"DWSREALTIME", "AUTO PDA", "REALTIME DB"})
REPORT_SHEETS = {"Autoformat", "DWSREALTIME", "AUTO PDA", "REALTIME DB"}


def workbook_sheets(archive):
    relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in relations}
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    output = {}
    for sheet in workbook.find(TAG("sheets")):
        target = targets[
            sheet.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
        ]
        name = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        output[name] = sheet.get("name")
    return output


@contextmanager
def template_snapshot(path):
    """Discard cached raw rows and unused shared strings before loading styles.

    This prevents millions of old query cells from becoming Python cell objects.
    Source inputs are read separately and never taken from template query caches.
    """
    fd, temporary = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as source:
            sheets = workbook_sheets(source)
            payloads = {}
            needed = set()
            for name, title in sheets.items():
                if title in REPORT_SHEETS:
                    data = source.read(name)
                    root = ET.fromstring(data)
                else:
                    root = data = None
                    sheet_data = None
                    with source.open(name) as stream:
                        for event, element in ET.iterparse(
                            stream, events=("start", "end")
                        ):
                            if root is None:
                                root = element
                            if event == "start" and element.tag == TAG("sheetData"):
                                sheet_data = element
                            if event == "end" and element.tag == TAG("row"):
                                for cell in list(element):
                                    if (
                                        element.get("r") != "1"
                                        and cell.find(TAG("f")) is None
                                    ):
                                        element.remove(cell)
                                if not list(element):
                                    sheet_data.remove(element)
                                    element.clear()
                    # Snapshot cells no longer contain huge source-data ranges.
                    dimension = root.find(TAG("dimension"))
                    if dimension is not None:
                        root.remove(dimension)
                    # Capture just the original root opening, not its raw rows.
                    with source.open(name) as stream:
                        prefix = stream.read(8192)
                    data = sheet_xml(root, prefix)
                for cell in root.iter(TAG("c")):
                    if cell.get("t") == "s":
                        needed.add(int(cell.find(TAG("v")).text))
                payloads[name] = data
            remap = {}
            strings = []
            if "xl/sharedStrings.xml" in source.namelist():
                index = 0
                sst_root = None
                with source.open("xl/sharedStrings.xml") as stream:
                    for event, element in ET.iterparse(stream, events=("start", "end")):
                        if sst_root is None:
                            sst_root = element
                        if event == "end" and element.tag == TAG("si"):
                            if index in needed:
                                remap[index] = len(strings)
                                strings.append(ET.fromstring(ET.tostring(element)))
                            index += 1
                            sst_root.remove(element)
                            element.clear()
                sst = ET.Element(
                    TAG("sst"),
                    {"count": str(len(strings)), "uniqueCount": str(len(strings))},
                )
                sst.extend(strings)
                payloads["xl/sharedStrings.xml"] = ET.tostring(
                    sst, encoding="utf-8", xml_declaration=True
                )
                for name in sheets:
                    data = payloads[name]
                    root = ET.fromstring(data)
                    for cell in root.iter(TAG("c")):
                        if cell.get("t") == "s":
                            value = cell.find(TAG("v"))
                            value.text = str(remap[int(value.text)])
                    payloads[name] = sheet_xml(root, data)
            with zipfile.ZipFile(temporary, "w") as target:
                for info in source.infolist():
                    target.writestr(
                        copy(info),
                        payloads.get(info.filename)
                        if info.filename in payloads
                        else source.read(info.filename),
                    )
        yield temporary
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def sheet_xml(root, original: bytes) -> bytes:
    """Preserve native namespace declarations referenced by mc:Ignorable."""
    for event, item in ET.iterparse(io.BytesIO(original), events=("start-ns", "start")):
        if event == "start":
            break
        prefix, uri = item
        if not re.fullmatch(r"ns\d+", prefix):
            ET.register_namespace(prefix, uri)
    result = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    old = re.search(rb"<worksheet\b[^>]*>", original)
    new = re.search(rb"<worksheet\b[^>]*>", result)
    if old is None or new is None:
        raise UnsupportedFormula("Unsupported worksheet namespace layout")
    return result[: new.start()] + old.group() + result[new.end() :]


def supports(path: str) -> bool:
    with zipfile.ZipFile(path) as archive:
        names = set(workbook_sheets(archive).values())
        return any(required.issubset(names) for required in SUPPORTED)


def runtime_paths(config, template: str, business_date: date) -> dict:
    """Honor existing operator paths, including DWS mirror destinations."""
    paths = template_paths(template)
    get = lambda section, key, default="": (
        config.get(section, key, fallback=default).strip().strip('"')
    )
    raw = get("DWS_JMS", "raw_path") or get("PATH", "output_dir")
    for key, option, default in (
        ("AUTO_PDA_DATA", "name_auto", "DWSXAUTOPDA.xlsx"),
        ("DWS_PDA_DATA", "name_dwspda", "DWSPDA.xlsx"),
        ("DWS9-11", "name_dws", "DWS9-11.xlsx"),
        ("REALTIME_DB", "name_realtime_db", "RealtimeDB.xlsx"),
    ):
        if key in paths and raw:
            paths[key] = os.path.join(raw, get("DWS_JMS", option, default))
    for i in range(1, 9):
        key = f"DWS{i}"
        folder = get(key, "pull_folder")
        if (
            key in paths
            and folder
            and get(key, "pull_enabled", "false").lower() in ("true", "1", "yes", "on")
        ):
            paths[key] = os.path.join(
                folder, get(key, "pull_file") or os.path.basename(paths[key])
            )
    auto = get("PATH", "autopacking_dir")
    for key, path in list(paths.items()):
        if key.isdigit():
            root = auto or os.path.dirname(path)
            dated = os.path.join(root, business_date.isoformat())
            folder = dated if os.path.isdir(dated) else root
            paths[key] = os.path.join(folder, os.path.basename(path))
    for key in list(paths):
        override = get("REPORT_CALCULATION", "path_" + key.lower())
        if override:
            paths[key] = override
    return paths


def prepare(
    config,
    workbooks,
    items,
    output_dir: str,
    business_date: date,
    now: datetime,
    keep_running=lambda: True,
    log=print,
) -> dict:
    """Prepare all supported active templates before Excel can open any file."""
    if config.get("REPORT_CALCULATION", "enabled", fallback="true").lower() in (
        "false",
        "0",
        "no",
        "off",
    ):
        log("[REPORT] Legacy Excel calculation explicitly selected")
        return {}
    generated = {}
    for item in workbooks:
        if not any(export.workbook == item.key for export in items):
            continue
        if not keep_running():
            raise InterruptedError("Report preparation cancelled")
        if not item.path or not os.path.isfile(item.path) or not supports(item.path):
            continue
        filename = f"{item.key}-{os.path.basename(item.path)}"
        path = os.path.join(output_dir, "calculated_workbooks", filename)
        start_hour = config.getint("TIME", "start_hour", fallback=15)
        paths = runtime_paths(config, item.path, business_date)
        from core.report_staging import stage
        paths = stage(paths, config, business_date, now, start_hour, keep_running, log)
        evidence = calculate(
            item.path,
            path,
            business_date,
            now,
            paths,
            keep_running,
            log,
            start_hour=start_hour,
        )
        evidence_path = path + ".evidence.json"
        with open(evidence_path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, ensure_ascii=False, indent=2)
        os.replace(evidence_path + ".tmp", evidence_path)
        generated[item.key] = path
    return generated


def write_values(template: str, output: str, book, values: dict, keep_running) -> None:
    """Rewrite cell payloads only; keep styles, merges, charts and dimensions.

    Generated copies have no query tables/connections, formula chains or DataMashup.
    Input rows are kept in their original external files instead of duplicated in
    hidden sheets. The 24 AutoPacking helper columns remain as calculated values.
    """
    folder = os.path.dirname(os.path.abspath(output))
    os.makedirs(folder, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".xlsx", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(template) as source:
            remove = {
                n
                for n in source.namelist()
                if n.startswith(("xl/queryTables/", "xl/tables/", "customXml/"))
                or n in ("xl/connections.xml", "xl/calcChain.xml")
            }
            sheet_ids = workbook_sheets(source)
            with zipfile.ZipFile(temporary, "w") as target:
                for info in source.infolist():
                    if not keep_running():
                        raise InterruptedError("Report cancelled before publishing")
                    name = info.filename
                    if name in remove:
                        continue
                    data = source.read(name)
                    if name in sheet_ids:
                        title = sheet_ids[name]
                        root = ET.fromstring(data)
                        for cell in root.iter(TAG("c")):
                            address = cell.attrib["r"]
                            key = (title, address)
                            if title not in REPORT_SHEETS and key not in values:
                                column = re.match(r"[A-Z]+", address).group()
                                if title.isdigit() and column in ("M", "N", "O", "P"):
                                    continue
                                # Keep source headers only, removing cached input rows.
                                if re.search(r"\d+$", address).group() == "1":
                                    continue
                                value = None
                            else:
                                if key not in values:
                                    continue
                                value = values[key]
                            for child in list(cell):
                                if child.tag in (TAG("f"), TAG("v"), TAG("is")):
                                    cell.remove(child)
                            cell.attrib.pop("t", None)
                            if value is None:
                                continue
                            if isinstance(value, bool):
                                cell.set("t", "b")
                                ET.SubElement(cell, TAG("v")).text = (
                                    "1" if value else "0"
                                )
                            elif isinstance(value, (int, float)):
                                ET.SubElement(cell, TAG("v")).text = repr(value)
                            else:
                                cell.set("t", "inlineStr")
                                text = ET.SubElement(
                                    ET.SubElement(cell, TAG("is")), TAG("t")
                                )
                                text.text = str(value)
                                text.set(
                                    "{http://www.w3.org/XML/1998/namespace}space",
                                    "preserve",
                                )
                        for tag in ("queryTableParts", "tableParts"):
                            child = root.find(TAG(tag))
                            if child is not None:
                                root.remove(child)
                        data = sheet_xml(root, data)
                    elif name.endswith(".rels") or name == "[Content_Types].xml":
                        root = ET.fromstring(data)
                        for child in list(root):
                            relation = child.get("Type", "").rsplit("/", 1)[-1]
                            part = child.get("PartName", "").lstrip("/")
                            if (
                                relation
                                in (
                                    "connections",
                                    "queryTable",
                                    "table",
                                    "customXml",
                                    "calcChain",
                                )
                                or part in remove
                            ):
                                root.remove(child)
                        ET.register_namespace("", root.tag[1:].split("}", 1)[0])
                        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    target.writestr(copy(info), data)
        with zipfile.ZipFile(temporary) as check:
            if check.testzip() is not None:
                raise RuntimeError("Generated workbook ZIP is invalid")
            if any(n.startswith("xl/queryTables/") for n in check.namelist()):
                raise RuntimeError("Query table was not removed")
            if any(
                b"<f>" in check.read(n) or b"<f " in check.read(n)
                for n in check.namelist()
                if n.startswith("xl/worksheets/") and n.endswith(".xml")
            ):
                raise RuntimeError("Generated workbook still contains formulas")
        if not keep_running():
            raise InterruptedError("Report cancelled before publishing")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def calculate(
    template: str,
    output: str,
    business_date: date,
    now: datetime,
    paths: dict | None = None,
    keep_running=lambda: True,
    log=print,
    start_hour=12,
) -> dict:
    if os.path.normcase(os.path.abspath(template)) == os.path.normcase(
        os.path.abspath(output)
    ):
        raise ValueError(
            "Output must be a separate file; never overwrite an operator template"
        )
    snapshot = ExitStack()
    snapshot_path = snapshot.enter_context(template_snapshot(template))
    book = None
    try:
        book = openpyxl.load_workbook(
            snapshot_path, read_only=False, data_only=False, keep_links=False
        )
        names = set(book.sheetnames)
        if not any(required.issubset(names) for required in SUPPORTED):
            raise UnsupportedFormula("Unrecognized report template")
        source_paths = template_paths(template)
        source_paths.update(paths or {})
        if not 12 <= start_hour <= 19:
            raise ValueError("Report template start hour must be between 12 and 19")
        inputs = Inputs(
            source_paths, business_date, now, keep_running, log, start_hour=start_hour
        )
        overrides = {}
        for name in ("Autoformat", "DWSREALTIME"):
            if name in names:
                overrides[(name, "A2")] = to_excel(
                    datetime.combine(business_date, datetime.min.time())
                )
        # Column trimming happens later in the image renderer. Exclude those
        # hours before freezing totals, and adjust the heading helper counts.
        for name, control in (
            ("Autoformat", 33),
            ("DWSREALTIME", 27),
            ("AUTO PDA", 51),
        ):
            if name in names:
                for hour in range(12, start_hour):
                    from openpyxl.utils.cell import get_column_letter

                    col = get_column_letter(3 + hour - 12)
                    overrides[(name, f"{col}{control}")] = None
        for sheet in book:
            for row in sheet:
                for cell in row:
                    if cell.data_type != "f" or isinstance(cell.value, str):
                        continue
                    if not sheet.title.isdigit():
                        raise UnsupportedFormula(
                            "Unknown array formula outside hourly helper sheet"
                        )
                    formula = cell.value.text
                    pipeline = re.search(r'(?:A:A|K:K)="([^"]+)"', formula)
                    if not pipeline:
                        raise UnsupportedFormula(
                            f"Unknown hourly helper formula {formula}"
                        )
                    values, errors = inputs.autopacking(sheet.title)
                    feeder = re.search(r"B:B=(\d+)", formula)
                    if feeder:
                        overrides[(sheet.title, cell.coordinate)] = values.get(
                            (pipeline.group(1), int(feeder.group(1))), 0
                        )
                    elif "UNIQUE" in formula:
                        count = errors.get(pipeline.group(1), 0)
                        # Preserve the accepted template's explicit x=1 -> 0 rule.
                        overrides[(sheet.title, cell.coordinate)] = (
                            count if count > 1 else 0
                        )
                    else:
                        raise UnsupportedFormula(f"Unknown array calculation {formula}")
        calculator = Calculator(book, now, inputs.count, overrides)
        formula_count = sum(
            c.data_type == "f" for sheet in book for row in sheet for c in row
        )
        values = calculator.calculate()
        write_values(snapshot_path, output, book, values, keep_running)
        evidence = {
            "template": os.path.abspath(template),
            "output": os.path.abspath(output),
            "business_date": business_date.isoformat(),
            "calculated_at": now.isoformat(),
            "formulas_replaced": formula_count,
            "sources": inputs.evidence,
            "excel_required": False,
            "power_query_required": False,
        }
        log(f"[REPORT] Calculated {formula_count:,} formulas without Excel: {output}")
        return evidence
    finally:
        if book is not None:
            book.close()
        snapshot.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("template")
    parser.add_argument("output")
    parser.add_argument("--business-date", required=True, type=date.fromisoformat)
    parser.add_argument("--now", type=datetime.fromisoformat, default=None)
    parser.add_argument(
        "--paths-json", help="Optional local/SMB input path overrides by query name"
    )
    args = parser.parse_args()
    paths = (
        json.loads(Path(args.paths_json).read_text(encoding="utf-8"))
        if args.paths_json
        else None
    )
    evidence = calculate(
        args.template,
        args.output,
        args.business_date,
        args.now or datetime.now(),
        paths,
    )
    Path(args.output + ".evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
