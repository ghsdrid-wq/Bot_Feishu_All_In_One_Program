let
    Source = Excel.Workbook(File.Contents("C:\0DWS\RealtimeDB.xlsx"), null, true),
    Sheets = Table.SelectRows(Source, each [Kind] = "Sheet"),
    RawSheets = Table.SelectRows(Sheets, each Text.Upper([Item]) = "DATA"),
    Raw = if Table.RowCount(RawSheets) > 0 then RawSheets{0}[Data]
          else if Table.RowCount(Sheets) > 0 then Sheets{0}[Data]
          else error "Realtime DB contains no data worksheet",
    Promoted = Table.PromoteHeaders(Raw, [PromoteAllScalars=true]),
    Cleaned = Table.TransformColumnNames(Promoted, each Text.Trim(_)),
    Columns = Table.ColumnNames(Cleaned),
    Resolve = (names as list) as text =>
        let Matches = List.Select(names, each List.Contains(Columns, _))
        in if List.Count(Matches) > 0 then Matches{0}
           else error ("Realtime DB missing required column: " & Text.Combine(names, " / ")),
    Operation = Resolve({"最新操作类型", "ประเภทการดำเนินงานล่าสุด", "ชื่อประเภทการดำเนินการล่าสุด"}),
    Overtime = Resolve({"超时类型", "ประเภทเกินเวลา"}),
    Selected = Table.SelectColumns(Cleaned, {Operation, Overtime}),
    Renames = List.Select({{Operation, "ประเภทการดำเนินงานล่าสุด"}, {Overtime, "ประเภทเกินเวลา"}}, each _{0} <> _{1}),
    Renamed = Table.RenameColumns(Selected, Renames),
    Result = Table.TransformColumnTypes(Renamed, {{"ประเภทการดำเนินงานล่าสุด", type text}, {"ประเภทเกินเวลา", type text}})
in
    Result
