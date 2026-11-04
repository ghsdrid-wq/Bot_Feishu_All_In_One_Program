"""Expand accepted numeric report layouts using dated slots and audited formulas."""

import re
from copy import copy
from datetime import timedelta

from openpyxl.formula import Tokenizer
from openpyxl.utils.cell import column_index_from_string, get_column_letter


def expand(book, inputs, window):
    slots = window.slots
    if not slots:
        raise ValueError("No completed reporting hour")
    overrides, layouts = {}, {}
    specs = {
        "Autoformat": (26, None, 39),
        "DWSREALTIME": (27, 16, 26),
        "AUTO PDA": (27, 16, 50),
    }
    maps, hourly = {}, {}
    for name, (last, subtotal, height) in specs.items():
        if name not in book:
            continue
        sheet = book[name]
        hourcols = [c for c in range(3, last + 1) if c != subtotal]
        columns, cursor, split = [], 3, None
        for i, slot in enumerate(slots):
            if i and slot.date() > slots[0].date() and slot.hour == 1 and split is None:
                split = cursor
                if subtotal:
                    cursor += 1
            oldcol = hourcols[(slot.hour - 12) % 24]
            columns.append((slot, oldcol, cursor))
            cursor += 1
        if subtotal and split is None:
            split = cursor
            cursor += 1
        mapping = {1: 1, 2: 2}
        for c in hourcols:
            matches = [n for _, old, n in columns if old == c]
            mapping[c] = matches[0] if matches else columns[0][2]
        # Right edges of aggregate ranges include all extension hours.
        mapping[last] = columns[-1][2]
        mapping[3] = columns[0][2]
        if subtotal:
            mapping[subtotal] = split
        for c in range(last + 1, sheet.max_column + 1):
            mapping[c] = cursor + c - last - 1
        maps[name], hourly[name] = mapping, columns
        layouts[name] = {
            "end_column": mapping[
                37 if name == "Autoformat" else 33 if name == "DWSREALTIME" else 29
            ],
            "end_row": height,
            "hour_columns": [n for _, _, n in columns],
            "column_map": mapping,
        }

    def refmap(value, current):
        if "!" in value:
            qualifier, address = value.rsplit("!", 1)
            target = qualifier.strip("'")
        else:
            qualifier, address, target = None, value, current
        if target not in maps:
            return value

        def replace(m):
            col = column_index_from_string(m[2])
            return m[1] + get_column_letter(maps[target].get(col, col)) + m[3]

        address = re.sub(r"(\$?)([A-Z]{1,3})(\$?\d+)", replace, address)
        return (qualifier + "!" if qualifier is not None else "") + address

    def formula(value, name):
        if not isinstance(value, str) or not value.startswith("="):
            return value
        tokens = Tokenizer(value).items
        return "=" + "".join(
            refmap(t.value, name)
            if t.type == "OPERAND" and t.subtype == "RANGE"
            else t.value
            for t in tokens
        )

    for name, mapping in maps.items():
        sheet = book[name]
        dimensions = {
            c: copy(sheet.column_dimensions[get_column_letter(c)])
            for c in range(1, sheet.max_column + 1)
        }
        cells = {
            (c.row, c.column): copy(c)
            for row in sheet
            for c in row
            if c.value is not None or c.has_style
        }
        merges = list(sheet.merged_cells.ranges)
        for merged in merges:
            sheet.unmerge_cells(str(merged))
        sheet._cells.clear()
        last, subtotal, _ = specs[name]
        oldhourcols = set(range(3, last + 1)) - ({subtotal} if subtotal else set())

        def put(old, row, col, value, sheet=sheet):
            target = sheet.cell(row, col, value)
            target._style = copy(old._style)
            target.alignment = copy(old.alignment)
            return target

        for (row, col), old in cells.items():
            if col in oldhourcols:
                continue
            put(old, row, mapping.get(col, col), formula(old.value, name))
        for slot, oldcol, newcol in hourly[name]:
            for (row, col), old in cells.items():
                if col != oldcol:
                    continue
                value = formula(old.value, name)
                # Vertical hourly formulas need the new column, not the aggregate-edge mapping.
                if (
                    isinstance(value, str)
                    and value.startswith("=")
                    and not (
                        "COUNTIF" in old.value
                        and ("!" in old.value or "DATA[" in old.value)
                    )
                ):
                    value = re.sub(
                        r"(\$?)"
                        + re.escape(get_column_letter(mapping[oldcol]))
                        + r"(\$?\d+)",
                        lambda m, newcol=newcol: (
                            m[1] + get_column_letter(newcol) + m[2]
                        ),
                        value,
                    )
                put(old, row, newcol, value)
                if isinstance(old.value, str) and "点-" in old.value:
                    label = f"{slot.hour}点-{slot.hour + 1}点"
                    if len(slots) > 24:
                        label = slot.strftime("%m/%d ") + label
                    sheet.cell(row, newcol, label)
                if isinstance(old.value, str) and old.value.startswith("="):
                    v = hourly_value(name, old.value, slot, inputs)
                    if v is not None:
                        overrides[(name, sheet.cell(row, newcol).coordinate)] = v
            sheet.column_dimensions[get_column_letter(newcol)].width = max(
                dimensions[oldcol].width or 10, 14 if name == "Autoformat" else 18
            )
        for oldcol, newcol in mapping.items():
            if oldcol not in oldhourcols and oldcol in dimensions:
                sheet.column_dimensions[get_column_letter(newcol)].width = dimensions[
                    oldcol
                ].width
        for merged in merges:
            left = mapping.get(merged.min_col, merged.min_col)
            right = mapping.get(merged.max_col, merged.max_col)
            if left <= right and not (
                merged.min_col == merged.max_col and merged.min_col in oldhourcols
            ):
                sheet.merge_cells(
                    start_row=merged.min_row,
                    end_row=merged.max_row,
                    start_column=left,
                    end_column=right,
                )
        header_row = 4 if name == "AUTO PDA" else 3
        sheet.row_dimensions[header_row].height = max(
            sheet.row_dimensions[header_row].height or 0, 72 if len(slots) > 24 else 52
        )
        for cell in sheet[header_row]:
            alignment = copy(cell.alignment)
            alignment.wrap_text = True
            cell.alignment = alignment
        # Explicit titles avoid the old NOW/hour-only helper logic.
        for row in (1, 34) if name == "Autoformat" else (1,):
            old = cells.get((row, 1))
            if old and isinstance(old.value, str):
                prefix = re.search(r'^="([^"]+)"', old.value)
                overrides[(name, f"A{row}")] = (
                    (prefix[1] if prefix else name + " ")
                    + window.start.strftime("%Y/%m/%d %H:%M")
                    + " - "
                    + (window.slots[-1] + timedelta(hours=1)).strftime("%Y/%m/%d %H:%M")
                )
    return overrides, layouts


def hourly_value(name, formula, slot, inputs):
    if name == "Autoformat":
        match = re.fullmatch(r"='(\d+)'!([MNOP])(\d+)", formula)
        if not match:
            return None
        values, errors = inputs.autopacking_slot(slot)
        pipeline = "58987-001" if match[2] in "MO" else "58987-002"
        if match[2] in "OP":
            count = errors.get(pipeline, 0)
            return count if count > 1 else 0
        return values.get((pipeline, int(match[3]) - 1), 0)
    if "COUNTIF" not in formula or not ("!" in formula or "DATA[" in formula):
        return None
    source = re.search(r"'(DWS[1-8]|DWS9-11)'!", formula)
    key = (
        source[1]
        if source
        else "AUTO_PDA_DATA"
        if "AUTO_PDA_DATA[" in formula
        else "DWS_PDA_DATA"
    )
    person = None
    if key in ("DWS9-11", "AUTO_PDA_DATA", "DWS_PDA_DATA"):
        person = re.findall(r'"([^\"]+)"', formula)[0].casefold()
    return sum(
        count
        for (day, hour, scanner), count in inputs.counter(key).items()
        if day == slot.date()
        and hour == slot.hour
        and (person is None or scanner == person)
    )
