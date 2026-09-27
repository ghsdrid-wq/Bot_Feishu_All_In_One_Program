"""Increment the single application version before a release build."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "app_version.py"
PATTERN = re.compile(r'(?m)^APP_VERSION = "(\d+)\.(\d+)\.(\d+)"$')


def current_version(text: str) -> tuple[int, int, int]:
    match = PATTERN.search(text)
    if not match:
        raise RuntimeError(f"APP_VERSION not found in {VERSION_FILE}")
    return tuple(int(value) for value in match.groups())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump AutoReportFeishu semantic version")
    parser.add_argument(
        "part", nargs="?", choices=("major", "minor", "patch"),
        help="version component to increment")
    parser.add_argument(
        "--set", dest="set_version", metavar="X.Y.Z",
        help="set an explicit semantic version")
    parser.add_argument(
        "--show", action="store_true", help="show current version without editing")
    args = parser.parse_args()

    text = VERSION_FILE.read_text(encoding="utf-8")
    major, minor, patch = current_version(text)
    if args.show or (not args.part and not args.set_version):
        print(f"{major}.{minor}.{patch}")
        return 0

    if args.set_version:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", args.set_version)
        if not match:
            parser.error("--set must use X.Y.Z, for example 2.0.1")
        major, minor, patch = (int(value) for value in match.groups())
    elif args.part == "major":
        major, minor, patch = major + 1, 0, 0
    elif args.part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1

    version = f"{major}.{minor}.{patch}"
    updated = PATTERN.sub(f'APP_VERSION = "{version}"', text, count=1)
    VERSION_FILE.write_text(updated, encoding="utf-8")
    print(f"Version: {version}")
    print(f"Release folder: AutoReportFeishuV{version.replace('.', '-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
