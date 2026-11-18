#!/usr/bin/env python3
"""Regression tests for strict pens and P4 structural rendering."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import draw_chanlun as draw  # noqa: E402


class DrawingRegressionTests(unittest.TestCase):
    def test_strict_bis_alternate_and_chain(self):
        bars = draw.synthetic_bars()
        analysis = draw.fallback_analysis(bars, min_gap=4)
        quality = draw.bis_chain_quality(analysis["bis"])
        self.assertGreater(len(analysis["bis"]), 0)
        self.assertTrue(quality["alternating"], quality)
        self.assertTrue(quality["continuous"], quality)
        for left, right in zip(analysis["bis"], analysis["bis"][1:]):
            self.assertEqual(left["end_idx"], right["start_idx"])

    def test_segments_render_with_confirmation_state(self):
        bars = draw.synthetic_bars()
        analysis = draw.fallback_analysis(bars, min_gap=4)
        first = analysis["bis"][0]
        last = analysis["bis"][min(5, len(analysis["bis"]) - 1)]
        start_price = first["top"] if first["type"] == "down" else first["bottom"]
        end_price = last["bottom"] if first["type"] == "down" else last["top"]
        analysis["segment_analysis"] = {
            "engine": "p2",
            "n_segments": 2,
            "n_confirmed": 1,
            "segments": [
                {
                    "direction": first["type"],
                    "start_bi": 0,
                    "end_bi": min(5, len(analysis["bis"]) - 1),
                    "start_price": start_price,
                    "end_price": end_price,
                    "confirmed": True,
                    "break_type": "test",
                    "n_bis": 6,
                },
                {
                    "direction": "up" if first["type"] == "down" else "down",
                    "start_bi": 0,
                    "end_bi": min(5, len(analysis["bis"]) - 1),
                    "confirmed": False,
                },
            ],
            "seg_zhongshu": [],
        }
        confirmed, _ = draw.select_segments(analysis, "confirmed")
        all_rows, _ = draw.select_segments(analysis, "all")
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(len(all_rows), 2)
        panel = draw.Panel("测试", bars, analysis, analysis["source"], "TEST", "day")
        svg = draw.render_svg(
            [panel], "P2 测试", 1200, 390, "candle", "light", "cn",
            0, "endpoints", 2, 4, "confirmed",
        )
        self.assertIn('data-layer="segment"', svg)
        self.assertIn('data-confirmed="true"', svg)
        self.assertIn("线段", svg)
        svg_all = draw.render_svg(
            [panel], "P2 测试", 1200, 390, "candle", "light", "cn",
            0, "endpoints", 2, 4, "all",
        )
        self.assertIn('data-confirmed="false"', svg_all)
        svg_none = draw.render_svg(
            [panel], "P2 测试", 1200, 390, "candle", "light", "cn",
            0, "endpoints", 2, 4, "none",
        )
        self.assertNotIn('data-layer="segment"', svg_none)
        self.assertNotIn("线段=粗半透明线", svg_none)

    def test_non_monotonic_bars_are_rejected(self):
        bars = draw.synthetic_bars(20)
        bars[2]["date"], bars[3]["date"] = bars[3]["date"], bars[2]["date"]
        with self.assertRaisesRegex(ValueError, "时间升序"):
            draw.validate_bars(bars)

    def test_p4_unknown_sub_status_is_explicit(self):
        bars = draw.synthetic_bars()
        analysis = draw.fallback_analysis(bars, min_gap=4)
        analysis["p4_divergence"] = {
            "state": "扩展",
            "amp_ratio": 1.2,
            "has_sub_zs": False,
            "sub_status": "unknown",
        }
        analysis["buy_sell"].append(
            {
                "source": "结构信号(幅度比)",
                "state": "扩展",
                "date": bars[-1]["date"],
                "price": bars[-1]["high"],
                "note": "次级别数据缺失",
            }
        )
        panel = draw.Panel("测试", bars, analysis, analysis["source"], "TEST", "day")
        svg = draw.render_svg(
            [panel],
            "P4 测试",
            1200,
            390,
            "candle",
            "light",
            "cn",
            0,
            "endpoints",
            2,
            4,
            "none",
        )
        self.assertIn('data-layer="p4-divergence"', svg)
        self.assertIn('data-sub-status="unknown"', svg)
        self.assertIn("结构段：扩展｜次级别缺失", svg)
        self.assertNotIn('data-layer="position"', svg)


if __name__ == "__main__":
    unittest.main()
