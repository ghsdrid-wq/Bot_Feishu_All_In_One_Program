"""Dormant Feishu line-chart variant retained for possible future reuse.

This module is intentionally not imported by the production report flow. It
preserves the approved report palette, full-value labels, responsive hour axis,
and the line/point treatment evaluated in live Feishu test round 16.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import card_report


SERIES_LABEL_DY = {
    "AutoPacking": (-17, -5),
    "DWS 1-11": (-18, -6),
    "PDA ลงรถ": (10, 22),
    "PDA บรรจุมือ": (-27, -15),
}


def hourly_line_chart_spec(
        rows: Sequence[Dict[str, Any]], unit: str) -> Dict[str, Any]:
    """Build the saved line-chart alternative without changing production."""
    hour_order: Dict[str, int] = {}
    prepared: List[Dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        hour = str(row.get("hour", ""))
        series = str(row.get("type", ""))
        hour_order.setdefault(hour, len(hour_order))
        value = max(int(row.get("value") or 0), 0)
        positions = SERIES_LABEL_DY.get(series, (-12, 0))
        row["label_value"] = "{:,}".format(value) if value else ""
        row["label_dy"] = positions[hour_order[hour] % 2]
        prepared.append(row)

    series_names = list(dict.fromkeys(
        str(row.get("type", "")) for row in rows))
    axis_label = {"style": {"fontSize": 10, "fill": "#60656F"}}
    return {
        "type": "line",
        "title": {"text": "แยกตามชนิด ({}/ชั่วโมง)".format(unit)},
        "data": {"values": prepared},
        "xField": "hour",
        "yField": "value",
        "seriesField": "type",
        "color": card_report.CHART_COLORS,
        "scales": [
            {
                "id": "lineLabelColor",
                "type": "ordinal",
                "domain": series_names,
                "range": card_report.CHART_LABEL_COLORS,
            },
            {
                "id": "lineLabelDy",
                "type": "linear",
                "domain": [-32, 32],
                "range": [-32, 32],
            },
        ],
        "label": {
            "visible": True,
            "position": "top",
            "offset": 0,
            "smartInvert": False,
            "formatter": "{label_value}",
            "style": {
                "fill": {"scale": "lineLabelColor", "field": "type"},
                "dy": {"scale": "lineLabelDy", "field": "label_dy"},
                "stroke": "#FFFFFF",
                "lineWidth": 2,
                "fontSize": 9,
                "fontWeight": "bold",
            },
            "overlap": {"hideOnHit": False},
        },
        "line": {"style": {"lineWidth": 2}},
        "point": {
            "visible": True,
            "style": {"size": 6, "lineWidth": 1, "stroke": "#FFFFFF"},
        },
        "legends": {"visible": True, "orient": "bottom"},
        "axes": [
            {
                "orient": "bottom",
                "sampling": True,
                "label": {
                    "autoRotate": True,
                    "autoRotateAngle": [45, 60],
                    "autoHide": True,
                    "autoHideMethod": "parity",
                    "minGap": 6,
                    **axis_label,
                },
            },
            {
                "orient": "left",
                "label": axis_label,
                "grid": {
                    "visible": True,
                    "style": {"stroke": "#E4E7EC", "lineDash": [4, 4]},
                },
            },
        ],
    }
