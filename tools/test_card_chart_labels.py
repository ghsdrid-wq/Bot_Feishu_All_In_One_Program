"""Regression checks for compact labels in the Feishu stacked bar chart."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import card_report


class CardChartLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {"hour": "14:00", "type": "AutoPacking", "value": 28_840},
            {"hour": "14:00", "type": "DWS 1-11", "value": 3_211},
            {"hour": "14:00", "type": "PDA", "value": 0},
            {"hour": "15:00", "type": "AutoPacking", "value": 26_068},
            {"hour": "15:00", "type": "DWS 1-11", "value": 4_623},
            {"hour": "15:00", "type": "PDA", "value": 300},
        ]

    def test_tiny_and_zero_segments_have_no_static_label(self) -> None:
        labeled = card_report._chart_rows_with_labels(self.rows)
        labels = {(row["hour"], row["value"]): row["label_value"] for row in labeled}
        self.assertEqual(labels[("14:00", 0)], "")
        self.assertEqual(labels[("15:00", 300)], "")
        self.assertEqual(labels[("14:00", 28_840)], "28,840")
        self.assertEqual(labels[("15:00", 4_623)], "4,623")

    def test_spec_places_small_bold_labels_inside_segments(self) -> None:
        spec = card_report._hourly_chart_spec(self.rows, "ชิ้น")
        label = spec["label"]
        self.assertEqual(label["position"], "inside")
        self.assertEqual(label["formatter"], "{label_value}")
        self.assertTrue(label["smartInvert"])
        self.assertTrue(label["overlap"]["hideOnHit"])
        self.assertEqual(label["style"]["fill"], "#4A4A4A")
        self.assertEqual(label["style"]["stroke"], "#FFFFFF")
        self.assertEqual(label["style"]["lineWidth"], 2)
        self.assertEqual(label["style"]["fontSize"], 10)
        self.assertEqual(label["style"]["fontWeight"], "bold")


if __name__ == "__main__":
    unittest.main()
