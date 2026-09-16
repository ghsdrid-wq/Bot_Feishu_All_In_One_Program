from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from Createphoto import (  # noqa: E402
    _is_query_reference_error,
    _is_query_status_poll_error,
    excel_call,
    refresh_workbook_queries,
)


class FakeQuery:
    def __init__(self) -> None:
        self.refresh_calls = 0

    def Refresh(self, background: bool) -> None:
        assert background is True
        self.refresh_calls += 1

    @property
    def Refreshing(self) -> bool:
        return False


class FakeTable:
    Name = "_05"

    def __init__(self, query: FakeQuery) -> None:
        self.QueryTable = query


class FakeListObjects:
    Count = 1

    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def Item(self, index: int) -> FakeTable:
        assert index == 1
        return self.table


class FakeWorksheet:
    Name = "05"

    def __init__(self, table: FakeTable) -> None:
        self.list_objects = FakeListObjects(table)
        self.access_count = 0

    @property
    def ListObjects(self) -> FakeListObjects:
        self.access_count += 1
        if self.access_count == 4:
            raise Exception("Item.ListObjects")
        return self.list_objects


class FakeWorksheets:
    Count = 1

    def __init__(self, worksheet: FakeWorksheet) -> None:
        self.worksheet = worksheet

    def Item(self, index: int) -> FakeWorksheet:
        assert index == 1
        return self.worksheet


class FakeConnections:
    Count = 1


class FakeWorkbook:
    def __init__(self, worksheet: FakeWorksheet) -> None:
        self.Worksheets = FakeWorksheets(worksheet)
        self.Connections = FakeConnections()


def main() -> None:
    assert _is_query_reference_error(Exception("Item.ListObjects"))
    assert _is_query_reference_error(Exception("ListObjects.Item"))
    assert _is_query_reference_error(Exception("Item.QueryTable"))
    assert _is_query_status_poll_error(
        Exception("Excel busy timeout during check query 05/_05")
    )
    assert not _is_query_reference_error(FileNotFoundError("missing workbook"))

    attempts = 0

    def flaky_query_reference() -> int:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise Exception("Item.ListObjects")
        return 42

    result = excel_call(
        flaky_query_reference,
        write=None,
        label="test query reference",
        timeout=1,
        delay=0.001,
        retry_errors=_is_query_reference_error,
    )
    assert result == 42
    assert attempts == 3

    query = FakeQuery()
    worksheet = FakeWorksheet(FakeTable(query))
    workbook = FakeWorkbook(worksheet)
    messages: list[str] = []
    refresh_workbook_queries(
        workbook,
        excel=object(),
        is_running=lambda: True,
        write=messages.append,
        batch_size=1,
        timeout=2,
    )
    assert query.refresh_calls == 1
    assert messages[-1].startswith("[REFRESH] All queries completed")
    print("PASS query refresh resilience")


if __name__ == "__main__":
    main()
