"""Regression checks for labels above the original Feishu stacked bars."""

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

    def test_every_nonzero_segment_has_a_static_label(self) -> None:
        labeled = card_report._chart_rows_with_labels(self.rows)
        labels = {(row["hour"], row["value"]): row["label_value"] for row in labeled}
        self.assertEqual(labels[("14:00", 0)], "")
        self.assertEqual(labels[("15:00", 300)], "300")
        self.assertEqual(labels[("14:00", 28_840)], "28,840")
        self.assertEqual(labels[("15:00", 4_623)], "4,623")
        self.assertTrue(all("label_dy" not in row for row in labeled))

    def test_spec_places_small_bold_labels_above_stacked_segments(self) -> None:
        spec = card_report._hourly_chart_spec(self.rows, "ชิ้น")
        chart = card_report._chart(spec)
        self.assertEqual(
            chart["chart_spec"]["color"],
            ["#C62828", "#F4B6C2", "#D9D9D9", "#F57573"],
        )
        label = spec["label"]
        self.assertTrue(spec["stack"])
        self.assertEqual(label["position"], "top")
        self.assertEqual(label["offset"], 2)
        self.assertEqual(label["formatter"], "{label_value}")
        self.assertFalse(label["smartInvert"])
        self.assertFalse(label["overlap"]["hideOnHit"])
        self.assertEqual(
            label["style"]["fill"],
            {"scale": "labelColor", "field": "type"},
        )
        self.assertNotIn("dy", label["style"])
        self.assertEqual(
            spec["scales"][0]["range"],
            ["#9E1B1B", "#B8325A", "#5F6368", "#C73E3A"],
        )
        self.assertEqual(len(spec["scales"]), 1)
        self.assertEqual(label["style"]["stroke"], "#FFFFFF")
        self.assertEqual(label["style"]["lineWidth"], 2)
        self.assertEqual(label["style"]["fontSize"], 8)
        self.assertEqual(label["style"]["fontWeight"], "bold")

        hour_axis = spec["axes"][0]
        self.assertEqual(hour_axis["orient"], "bottom")
        self.assertTrue(hour_axis["sampling"])
        self.assertNotIn("paddingInner", hour_axis)
        self.assertNotIn("paddingOuter", hour_axis)
        self.assertTrue(hour_axis["label"]["autoRotate"])
        self.assertEqual(hour_axis["label"]["autoRotateAngle"], [45, 60])
        self.assertTrue(hour_axis["label"]["autoHide"])
        self.assertEqual(hour_axis["label"]["autoHideMethod"], "parity")
        self.assertEqual(hour_axis["label"]["minGap"], 6)


if __name__ == "__main__":
    unittest.main()
