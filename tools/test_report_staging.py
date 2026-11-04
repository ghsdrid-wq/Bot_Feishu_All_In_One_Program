"""Verify local snapshot safety and noon-reset isolation."""

import configparser
import os
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from core.report_staging import copy_snapshot, stage


class StagingTests(unittest.TestCase):
    def test_failed_copy_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "raw.xlsx", Path(folder) / "copy.xlsx"
            source.write_bytes(b"broken")
            target.write_bytes(b"previous")
            with self.assertRaises(zipfile.BadZipFile):
                copy_snapshot(source, target)
            self.assertEqual(target.read_bytes(), b"previous")
            self.assertFalse(list(Path(folder).glob("*.part")))

    def test_noon_reset_keeps_previous_business_day_snapshot(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("core.dws_mirror.plan", return_value=[]),
        ):
            cfg = configparser.RawConfigParser()
            cfg["REPORT_CALCULATION"] = {"auto_cache": folder + "/cache"}
            source = Path(folder) / "16.xlsx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("data", "before reset")
            stamp = datetime(2026, 10, 21, 16).timestamp()
            os.utime(source, (stamp, stamp))
            args = (
                cfg,
                date(2026, 10, 21),
                datetime(2026, 10, 22, 12, 5),
                15,
                lambda: True,
                lambda x: None,
            )
            result = stage({"16": str(source)}, *args)
            original = Path(result["16"]).read_bytes()
            source.write_bytes(b"reset")
            stamp = datetime(2026, 10, 22, 12).timestamp()
            os.utime(source, (stamp, stamp))
            stage({"16": str(source)}, *args)
            self.assertEqual(Path(result["16"]).read_bytes(), original)
            Path(result["16"]).unlink()
            with self.assertRaises(OSError):
                stage({"16": str(source)}, *args)


if __name__ == "__main__":
    unittest.main()
