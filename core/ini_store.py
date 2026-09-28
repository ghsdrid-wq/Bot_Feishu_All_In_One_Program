"""Thread-safe helpers for the shared application INI file."""

from __future__ import annotations

import configparser
import os
import threading
from collections.abc import Callable, Iterable


_INI_LOCK = threading.RLock()


def _read(path: str) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.read(path, encoding="utf-8")
    return parser


def _replace(path: str, parser: configparser.RawConfigParser) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        parser.write(handle)
    os.replace(tmp_path, path)


def write_ini(
    path: str,
    parser: configparser.RawConfigParser,
    *,
    preserve_disk_sections: Iterable[str] = (),
) -> None:
    """Atomically write an INI while preserving independently owned sections."""
    with _INI_LOCK:
        disk = _read(path)
        for section_name in preserve_disk_sections:
            if section_name not in disk:
                continue
            if section_name in parser:
                parser.remove_section(section_name)
            parser[section_name] = dict(disk[section_name])
        _replace(path, parser)


def update_ini(path: str, update: Callable[[configparser.RawConfigParser], None]) -> None:
    """Read-modify-write an INI as one in-process atomic operation."""
    with _INI_LOCK:
        parser = _read(path)
        update(parser)
        _replace(path, parser)

