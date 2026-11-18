#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render Chanlun structure charts as dependency-free SVG.

The renderer intentionally owns presentation, not trading logic. It accepts
analysis JSON from chanlun-obays when available, otherwise it runs its
deterministic analyzer as a fallback.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DRAWING_VERSION = "0.3.0"

THEMES = {
    "light": {
        "bg": "#ffffff",
        "surface": "#fbfcfe",
        "ink": "#1f2733",
        "muted": "#6b7785",
        "grid": "#e8edf3",
        "frame": "#d8e0e9",
        "hub": "#b9770e",
        "hub_fill": "#f3d9a4",
        "pen": "#2563eb",
        "fractal_top": "#c0392b",
        "fractal_bottom": "#1e8449",
        "signal": "#7c3aed",
        "position": "#0f766e",
        "segment": "#8b5e34",
    },
    "dark": {
        "bg": "#111827",
        "surface": "#182131",
        "ink": "#e7edf5",
        "muted": "#a6b3c5",
        "grid": "#2c394b",
        "frame": "#42536a",
        "hub": "#f3b64c",
        "hub_fill": "#6a4c15",
        "pen": "#7db4ff",
        "fractal_top": "#ff8b7d",
        "fractal_bottom": "#65d69a",
        "signal": "#c4a0ff",
        "position": "#59d4c3",
        "segment": "#e7b27e",
    },
}


@dataclass
class Panel:
    label: str
    bars: list[dict[str, Any]]
    analysis: dict[str, Any]
    source: str
    symbol: str
    period: str


def num(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def read_bars(path: str) -> list[dict[str, Any]]:
    """Read CSV/JSON bars with tolerant headers and JSON shapes."""
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(path)
    if source.suffix.lower() == ".json":
        obj = json.loads(source.read_text(encoding="utf-8-sig"))
        if isinstance(obj, list):
            rows = obj
        elif isinstance(obj, dict):
            rows = None
            for key in ("data", "bars", "klines"):
                if isinstance(obj.get(key), list):
                    rows = obj[key]
                    break
            if rows is None:
                for value in obj.values():
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        rows = value
                        break
            if rows is None:
                raise ValueError("JSON 中没有可识别的 K 线数组: %s" % path)
        else:
            raise ValueError("JSON 顶层必须是数组或对象: %s" % path)
        bars = []
        for row in rows:
            date = row.get("date") or row.get("datetime") or row.get("时间")
            o = num(row.get("open", row.get("开盘", row.get("o"))))
            h = num(row.get("high", row.get("最高", row.get("h"))))
            l = num(row.get("low", row.get("最低", row.get("l"))))
            c = num(row.get("close", row.get("收盘", row.get("c"))))
            if not date or h <= 0 or l <= 0:
                continue
            bars.append(
                {
                    "date": str(date).strip(),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": num(row.get("volume", row.get("vol", row.get("成交量", 0)))),
                }
            )
        return bars

    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.reader(handle) if row and any(cell.strip() for cell in row)]
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    aliases = {
        "date": ["date", "日期", "时间", "datetime", "time"],
        "open": ["open", "开盘", "开盘价"],
        "high": ["high", "最高", "最高价"],
        "low": ["low", "最低", "最低价"],
        "close": ["close", "收盘", "收盘价"],
        "volume": ["volume", "vol", "成交量", "量"],
    }
    idx: dict[str, int] = {}
    for key, names in aliases.items():
        for i, name in enumerate(header):
            if name in names:
                idx[key] = i
                break
    has_header = "date" in idx and "open" in idx and "high" in idx and "low" in idx
    data_rows = rows[1:] if has_header else rows
    bars = []
    for row in data_rows:
        if len(row) < 4:
            continue
        get = lambda key, fallback: row[idx[key]] if key in idx else fallback
        date = get("date", row[0])
        bars.append(
            {
                "date": str(date).strip(),
                "open": num(get("open", row[1])),
                "high": num(get("high", row[2])),
                "low": num(get("low", row[3])),
                "close": num(get("close", row[4] if len(row) > 4 else row[3])),
                "volume": num(get("volume", row[5] if len(row) > 5 else 0)),
            }
        )
    return [bar for bar in bars if bar["high"] > 0 and bar["low"] > 0]


def validate_bars(bars: list[dict[str, Any]]) -> None:
    """Reject non-monotonic input instead of silently changing structure order."""
    previous: str | None = None
    for row in bars:
        text = str(row.get("date", "")).strip()
        if not text:
            raise ValueError("K 线缺少 date")
        key = text.replace("T", " ")
        if previous is not None and key < previous:
            raise ValueError(
                "K 线必须按时间升序: %s 出现在 %s 之后" % (text, previous)
            )
        previous = key


def merge_bars(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply Chanlun containment merging to raw bars."""
    merged: list[dict[str, Any]] = []
    for bar in bars:
        if not merged:
            merged.append(dict(bar, _idx=0, _end=bar["date"]))
            continue
        prev = merged[-1]
        direction = 1
        if len(merged) >= 2:
            direction = 1 if merged[-1]["high"] >= merged[-2]["high"] else -1
        contained = (
            bar["high"] <= prev["high"] and bar["low"] >= prev["low"]
        ) or (
            bar["high"] >= prev["high"] and bar["low"] <= prev["low"]
        )
        if contained:
            if direction > 0:
                prev["high"] = max(prev["high"], bar["high"])
                prev["low"] = max(prev["low"], bar["low"])
            else:
                prev["high"] = min(prev["high"], bar["high"])
                prev["low"] = min(prev["low"], bar["low"])
            prev["close"] = bar["close"]
            prev["volume"] += bar["volume"]
            prev["_end"] = bar["date"]
        else:
            merged.append(dict(bar, _idx=len(merged), _end=bar["date"]))
    return merged


def find_fractals(merged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fractals = []
    for i in range(1, len(merged) - 1):
        left, mid, right = merged[i - 1], merged[i], merged[i + 1]
        if (
            mid["high"] > left["high"]
            and mid["high"] > right["high"]
            and mid["low"] > left["low"]
            and mid["low"] > right["low"]
        ):
            fractals.append(
                {
                    "type": "top",
                    "index": i,
                    "bar_index": mid.get("_idx", i),
                    "date": mid["date"],
                    "price": mid["high"],
                }
            )
        elif (
            mid["low"] < left["low"]
            and mid["low"] < right["low"]
            and mid["high"] < left["high"]
            and mid["high"] < right["high"]
        ):
            fractals.append(
                {
                    "type": "bottom",
                    "index": i,
                    "bar_index": mid.get("_idx", i),
                    "date": mid["date"],
                    "price": mid["low"],
                }
            )
    return fractals


def build_bis(fractals: list[dict[str, Any]], min_gap: int = 4) -> list[dict[str, Any]]:
    """Strict pen chain aligned with chanlun-obays >= 2.9.4.

    Same-direction extremes may advance a candidate endpoint. When that
    happens, the previous confirmed pen is rebuilt so adjacent pens remain
    chained. No already-confirmed reverse pen is deleted.
    """
    if len(fractals) < 2:
        return []
    cleaned = []
    for fractal in fractals:
        if cleaned and cleaned[-1]["type"] == fractal["type"]:
            more_extreme = (
                fractal["price"] >= cleaned[-1]["price"]
                if fractal["type"] == "top"
                else fractal["price"] <= cleaned[-1]["price"]
            )
            if more_extreme:
                cleaned[-1] = fractal
        else:
            cleaned.append(fractal)

    def make_bi(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        top = a if a["type"] == "top" else b
        bottom = a if a["type"] == "bottom" else b
        direction = "up" if bottom["index"] < top["index"] else "down"
        length = round(abs(top["price"] - bottom["price"]), 4)
        top_bar = top.get("bar_index", top["index"])
        bottom_bar = bottom.get("bar_index", bottom["index"])
        n_bars = abs(top_bar - bottom_bar) + 1
        return {
            "type": direction,
            "top": top["price"],
            "bottom": bottom["price"],
            "top_date": top["date"],
            "bottom_date": bottom["date"],
            "top_index": top["index"],
            "bottom_index": bottom["index"],
            "top_bar": top_bar,
            "bottom_bar": bottom_bar,
            "start_idx": min(a["index"], b["index"]),
            "end_idx": max(a["index"], b["index"]),
            "length": length,
            "slope": round(length / max(n_bars, 1), 4),
            "bars": n_bars,
        }

    bis: list[dict[str, Any]] = []
    pen = cleaned[0]
    previous: dict[str, Any] | None = None
    for fractal in cleaned[1:]:
        if fractal["type"] == pen["type"]:
            more_extreme = (
                fractal["price"] >= pen["price"]
                if fractal["type"] == "top"
                else fractal["price"] <= pen["price"]
            )
            if more_extreme:
                pen = fractal
                if bis and previous is not None:
                    bis[-1] = make_bi(previous, pen)
            continue
        if fractal["index"] - pen["index"] >= min_gap:
            bis.append(make_bi(pen, fractal))
            previous = pen
            pen = fractal
    return bis


def find_zhongshu(bis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hubs = []
    i = 0
    while i + 2 < len(bis):
        b1, b2, b3 = bis[i], bis[i + 1], bis[i + 2]
        if (
            b1["type"] == b2["type"]
            or b2["type"] == b3["type"]
            or b1["type"] != b3["type"]
        ):
            i += 1
            continue
        if any("start_idx" not in b or "end_idx" not in b for b in (b1, b2, b3)):
            i += 1
            continue
        if b1["end_idx"] != b2["start_idx"] or b2["end_idx"] != b3["start_idx"]:
            i += 1
            continue
        zg = min(b1["top"], b2["top"], b3["top"])
        zd = max(b1["bottom"], b2["bottom"], b3["bottom"])
        if zg <= zd:
            i += 1
            continue
        j = i + 3
        while j < len(bis):
            item = bis[j]
            if item["top"] < zd or item["bottom"] > zg:
                break
            j += 1
        in_bis = bis[i:j]
        spans = [abs(item["top"] - item["bottom"]) for item in in_bis]
        hints = []
        if zg - zd <= max(spans) * 0.35:
            hints.append("疑似收敛中枢")
        if len(in_bis) >= 3 and spans[1] <= spans[0] * 0.5 and (
            len(in_bis) < 4 or spans[1] <= spans[2] * 0.5
        ):
            hints.append("疑似奔走中枢")
        if len(in_bis) >= 5:
            hints.append("疑似扩大中枢")
        first, last = in_bis[0], in_bis[-1]
        hubs.append(
            {
                "start_date": first["top_date"] if first["type"] == "down" else first["bottom_date"],
                "end_date": last["top_date"] if last["type"] == "up" else last["bottom_date"],
                "zg": round(zg, 4),
                "zd": round(zd, 4),
                "mid": round((zg + zd) / 2, 4),
                "type": b1["type"],
                "n_bis": len(in_bis),
                "hints": hints,
            }
        )
        i = j
    return hubs


def fallback_analysis(bars: list[dict[str, Any]], min_gap: int) -> dict[str, Any]:
    merged = merge_bars(bars)
    fractals = find_fractals(merged)
    bis = build_bis(fractals, min_gap=min_gap)
    hubs = find_zhongshu(bis)
    return {
        "source": "基础规则兜底(strict)",
        "summary": {
            "K线数": len(bars),
            "包含处理后K线": len(merged),
            "有效笔数": len(bis),
            "中枢数": len(hubs),
            "最新收盘": bars[-1]["close"] if bars else None,
        },
        "fractals": fractals,
        "bis": bis,
        "zhongshu": hubs,
        "signals": [],
        "buy_sell": [],
        "p4_divergence": {},
    }


def bis_chain_quality(bis: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe alternation and endpoint continuity without mutating analysis."""
    alternating = all(
        left.get("type") != right.get("type") for left, right in zip(bis, bis[1:])
    )
    indexed = all("end_idx" in row and "start_idx" in row for row in bis)
    breaks = (
        [
            i
            for i, (left, right) in enumerate(zip(bis, bis[1:]))
            if left.get("end_idx") != right.get("start_idx")
        ]
        if indexed
        else []
    )
    return {
        "alternating": alternating,
        "continuous": (not breaks) if indexed else None,
        "breaks": breaks,
    }


def structural_signal(
    analysis: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the P4 payload and its latest plottable structural signal."""
    p4 = analysis.get("p4_divergence")
    if not isinstance(p4, dict) or p4.get("error"):
        return {}, None
    candidates = [
        row
        for row in (analysis.get("buy_sell") or [])
        if isinstance(row, dict)
        and row.get("price") is not None
        and (
            str(row.get("source", "")).startswith("结构信号")
            or row.get("state")
        )
    ]
    return p4, (candidates[-1] if candidates else None)


def p4_label(p4: dict[str, Any]) -> str:
    state = str(p4.get("state") or "待定")
    neutral = {
        "随时结束": "高幅度比",
        "中枢背离段失败": "回中枢",
        "走势背离段失败": "破 p0",
        "扩展": "扩展",
        "变异": "变异",
        "延伸": "延伸",
        "待定": "待定",
    }.get(state, state)
    suffix = "｜次级别缺失" if p4.get("sub_status") == "unknown" else ""
    return "结构段：%s%s" % (neutral, suffix)


def select_segments(
    analysis: dict[str, Any], mode: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select plottable P2 segments while preserving confirmation state."""
    if mode == "none":
        return [], {}
    payload = analysis.get("segment_analysis")
    if not isinstance(payload, dict):
        payload = {}
    rows = payload.get("segments") or analysis.get("segments") or []
    rows = [row for row in rows if isinstance(row, dict)]
    if mode == "confirmed":
        rows = [row for row in rows if row.get("confirmed") is True]
    return rows, payload


def segment_endpoints(
    segment: dict[str, Any], bis: list[dict[str, Any]]
) -> tuple[Any, float, Any, float] | None:
    """Resolve a P2 segment to actual bi endpoints and K-line dates."""
    start_date = segment.get("start_date")
    end_date = segment.get("end_date")
    start_price = segment.get("start_price")
    end_price = segment.get("end_price")
    try:
        start_bi = bis[int(segment["start_bi"])]
        end_bi = bis[int(segment["end_bi"])]
    except (KeyError, IndexError, TypeError, ValueError):
        start_bi = end_bi = {}
    direction = segment.get("direction")
    if direction == "up":
        start_date = start_date or start_bi.get("bottom_date")
        end_date = end_date or end_bi.get("top_date")
        start_price = start_price if start_price is not None else start_bi.get("bottom")
        end_price = end_price if end_price is not None else end_bi.get("top")
    elif direction == "down":
        start_date = start_date or start_bi.get("top_date")
        end_date = end_date or end_bi.get("bottom_date")
        start_price = start_price if start_price is not None else start_bi.get("top")
        end_price = end_price if end_price is not None else end_bi.get("bottom")
    if start_date is None or end_date is None or start_price is None or end_price is None:
        return None
    return start_date, num(start_price), end_date, num(end_price)


def find_analyzer() -> str | None:
    candidates = [
        os.environ.get("CHANLUN_ANALYZE", ""),
        str(Path.home() / ".cc-switch/skills/chanlun-obays/scripts/chanlun_analyze.py"),
        str(Path.home() / ".cc-switch/skills/chanlun-obays/scripts/structure/chanlun_analyze.py"),
        str(Path.home() / ".codex/skills/chanlun-obays/scripts/chanlun_analyze.py"),
        str(Path.home() / ".codex/skills/chanlun-obays/scripts/structure/chanlun_analyze.py"),
        str(ROOT.parent / "chanlun-obays/scripts/chanlun_analyze.py"),
        str(ROOT.parent / "chanlun-obays/scripts/structure/chanlun_analyze.py"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_dir():
            path = path / "chanlun_analyze.py"
        if path.is_file():
            return str(path)
    return None


def infer_period(label: str) -> str:
    if "60" in label:
        return "60m"
    if "30" in label:
        return "30m"
    if "周" in label or "week" in label.lower():
        return "week"
    return "day"


def analyze_bars(
    bars: list[dict[str, Any]],
    label: str,
    symbol: str,
    min_gap: int,
    analysis_path: str | None,
    segment_engine: str,
    structure_mode: str,
) -> tuple[dict[str, Any], str]:
    if analysis_path:
        payload = json.loads(Path(analysis_path).read_text(encoding="utf-8-sig"))
        payload.setdefault("source", "外部 analysis.json")
        if "zhongshu" not in payload and "hubs" in payload:
            payload["zhongshu"] = payload["hubs"]
        return payload, "外部 analysis.json"

    analyzer = find_analyzer()
    if analyzer:
        period = infer_period(label)
        runner = (
            "import json,os,sys;"
            "sys.path.insert(0,os.path.dirname(sys.argv[1]));"
            "import chanlun_analyze as ca;"
            "bars=ca.read_data(sys.argv[2]);"
            "print(json.dumps(ca.analyze(bars,symbol=sys.argv[3],"
            "period=sys.argv[4],min_gap=int(sys.argv[5]),"
            "segment_engine=sys.argv[6],structure_mode=sys.argv[7]),ensure_ascii=False))"
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as handle:
                json.dump(bars, handle, ensure_ascii=False)
                temp_path = handle.name
            command = [
                sys.executable,
                "-c",
                runner,
                analyzer,
                temp_path,
                symbol,
                period,
                str(min_gap),
                segment_engine,
                structure_mode,
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            text = completed.stdout
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                payload = json.loads(text[start : end + 1])
                if "error" not in payload:
                    payload.setdefault("source", "chanlun-obays")
                    if "zhongshu" not in payload and "hubs" in payload:
                        payload["zhongshu"] = payload["hubs"]
                    source = (
                        "chanlun-obays(P2)"
                        if (payload.get("segment_analysis") or {}).get("engine") == "p2"
                        else "chanlun-obays"
                    )
                    return payload, source
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
            pass
        finally:
            try:
                os.unlink(temp_path)
            except (UnboundLocalError, OSError):
                pass

    payload = fallback_analysis(bars, min_gap)
    return payload, payload["source"]


def parse_panel_specs(args: argparse.Namespace) -> list[tuple[str, str, str | None]]:
    if args.panel:
        specs = []
        for raw in args.panel:
            label, sep, remainder = raw.partition("=")
            if not sep or not label.strip() or not remainder.strip():
                raise ValueError("--panel 格式应为 LABEL=BARS[,ANALYSIS]")
            bars_path, comma, analysis_path = remainder.partition(",")
            specs.append((label.strip(), bars_path.strip(), analysis_path.strip() if comma else None))
        return specs
    if not args.bars:
        raise ValueError("请提供 --bars 或至少一个 --panel")
    return [(args.label, args.bars, args.analysis)]


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def fmt_price(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:.1f}"
    if abs(value) >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}"


def short_date(value: str) -> str:
    value = str(value)
    if len(value) >= 16 and (" " in value or "T" in value):
        return value.replace("T", " ")[5:16]
    return value[5:10] if len(value) >= 10 else value


def clean_label(value: Any) -> str:
    text = str(value)
    text = re.sub(r"\s*→.*$", "", text)
    text = text.replace("买点", "结构位置").replace("卖点", "结构位置")
    text = text.replace("买入", "结构").replace("卖出", "结构")
    return text.strip()[:30]


def index_map(bars: list[dict[str, Any]]) -> dict[str, int]:
    mapping = {}
    for i, bar in enumerate(bars):
        mapping.setdefault(str(bar["date"]), i)
        mapping.setdefault(str(bar["date"])[:10], i)
    return mapping


def find_index(mapping: dict[str, int], date: Any, fallback: int = 0) -> int:
    key = str(date)
    if key in mapping:
        return mapping[key]
    if key[:10] in mapping:
        return mapping[key[:10]]
    return fallback


def svg_text(x: float, y: float, text: str, color: str, size: int = 12,
             anchor: str = "start", weight: int = 400, extra: str = "") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" '
        f'text-anchor="{anchor}" font-weight="{weight}" {extra}>{esc(text)}</text>'
    )


def render_panel(
    panel: Panel,
    top: float,
    width: float,
    panel_height: float,
    mode: str,
    palette: str,
    theme: dict[str, str],
    max_bars: int,
    fractal_mode: str,
    max_hub_labels: int,
    max_signals: int,
    segment_mode: str,
) -> list[str]:
    bars = panel.bars[-max_bars:] if max_bars and len(panel.bars) > max_bars else panel.bars
    if not bars:
        return [svg_text(82, top + 30, f"{panel.label}: 无 K 线", theme["ink"], 14)]
    analysis = panel.analysis
    bis = analysis.get("bis") or []
    hubs = analysis.get("zhongshu") or analysis.get("hubs") or []
    fractals = analysis.get("fractals") or []
    signals = analysis.get("signals") or []
    positions = analysis.get("buy_sell") or []
    quality = bis_chain_quality(bis)
    p4, p4_signal = structural_signal(analysis)
    segments, segment_payload = select_segments(analysis, segment_mode)
    mapping = index_map(bars)
    plot_x, plot_y = 82.0, top + 62.0
    plot_w = width - 112.0
    plot_h = panel_height - 112.0
    up_color = theme["fractal_top"] if palette == "cn" else theme["fractal_bottom"]
    down_color = theme["fractal_bottom"] if palette == "cn" else theme["fractal_top"]

    values = [b["low"] for b in bars] + [b["high"] for b in bars]
    for hub in hubs:
        values.extend([num(hub.get("zg")), num(hub.get("zd"))])
    for signal in signals + positions:
        if signal.get("price") is not None:
            values.append(num(signal["price"]))
    if p4_signal and p4_signal.get("price") is not None:
        values.append(num(p4_signal["price"]))
    for segment in segments:
        endpoints = segment_endpoints(segment, bis)
        if endpoints:
            values.extend([endpoints[1], endpoints[3]])
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.04, abs(hi) * 0.002, 1)
    lo, hi = lo - pad, hi + pad

    def sx(i: float) -> float:
        return plot_x + (plot_w - 1) * i / max(len(bars) - 1, 1)

    def sy(value: float) -> float:
        return plot_y + plot_h - (value - lo) * plot_h / max(hi - lo, 1e-9)

    def visible_index(date: Any) -> int | None:
        idx = find_index(mapping, date, -1)
        return idx if idx >= 0 else None

    if quality["continuous"] is True:
        chain_text = "笔链连续"
    elif quality["continuous"] is False:
        chain_text = "笔链断开 %s" % quality["breaks"]
    else:
        chain_text = "笔链未提供索引"
    out = [
        f'<g data-panel="{esc(panel.label)}">',
        svg_text(plot_x, top + 24, panel.label, theme["ink"], 17, weight=600),
        svg_text(
            plot_x,
            top + 43,
            f"结构来源：{panel.source}｜{len(bars)} 根 K 线｜{chain_text}",
            theme["muted"],
            12,
            extra='data-bis-chain="%s"'
            % (
                "continuous"
                if quality["continuous"] is True
                else "broken"
                if quality["continuous"] is False
                else "unknown"
            ),
        ),
        f'<rect x="{plot_x}" y="{plot_y}" width="{plot_w}" height="{plot_h}" '
        f'fill="{theme["surface"]}" stroke="{theme["frame"]}" data-layer="plot-frame"/>',
    ]

    for tick in range(5):
        value = lo + (hi - lo) * tick / 4
        y = sy(value)
        out.append(
            f'<line x1="{plot_x}" y1="{y:.1f}" x2="{plot_x + plot_w:.1f}" '
            f'y2="{y:.1f}" stroke="{theme["grid"]}" data-layer="grid"/>'
        )
        out.append(
            svg_text(plot_x - 8, y + 4, fmt_price(value), theme["muted"], 12, "end")
        )

    tick_idx = sorted(set([0, max(0, len(bars) // 4), max(0, len(bars) // 2),
                           max(0, len(bars) * 3 // 4), len(bars) - 1]))
    for idx in tick_idx:
        if idx < 0 or idx >= len(bars):
            continue
        anchor = "start" if idx == 0 else "end" if idx == len(bars) - 1 else "middle"
        out.append(
            svg_text(
                sx(idx),
                plot_y + plot_h + 20,
                short_date(bars[idx]["date"]),
                theme["muted"],
                12,
                anchor,
            )
        )

    actual_mode = "line" if mode == "line" or (mode == "auto" and len(bars) > 260) else "candle"
    if actual_mode == "line":
        points = " ".join(f"{sx(i):.1f},{sy(bar['close']):.1f}" for i, bar in enumerate(bars))
        out.append(
            f'<polyline fill="none" stroke="{theme["pen"]}" stroke-width="1.5" '
            f'points="{points}" data-layer="close-line"/>'
        )
    else:
        body_w = max(1.5, min(9.0, plot_w / max(len(bars), 1) * 0.68))
        for i, bar in enumerate(bars):
            x = sx(i)
            up = bar["close"] >= bar["open"]
            color = up_color if up else down_color
            out.append(
                f'<line x1="{x:.1f}" y1="{sy(bar["high"]):.1f}" '
                f'x2="{x:.1f}" y2="{sy(bar["low"]):.1f}" '
                f'stroke="{color}" stroke-width="1" data-layer="wick"/>'
            )
            y_open, y_close = sy(bar["open"]), sy(bar["close"])
            y = min(y_open, y_close)
            h = max(1.2, abs(y_open - y_close))
            fill = color if not up else theme["bg"]
            out.append(
                f'<rect x="{x - body_w / 2:.1f}" y="{y:.1f}" width="{body_w:.1f}" '
                f'height="{h:.1f}" fill="{fill}" stroke="{color}" '
                f'stroke-width="1" data-layer="candle"/>'
            )

    # Hubs are under strokes but above candles.
    hub_labels = 0
    for hub in hubs:
        start = visible_index(hub.get("start_date"))
        end = visible_index(hub.get("end_date"))
        if start is None or end is None:
            continue
        if end < 0 or start >= len(bars) or end < start:
            continue
        start = max(0, start)
        end = min(len(bars) - 1, end)
        x1, x2 = sx(start), sx(end)
        y1, y2 = sy(num(hub.get("zg"))), sy(num(hub.get("zd")))
        out.append(
            f'<rect x="{x1:.1f}" y="{min(y1, y2):.1f}" width="{max(2, x2 - x1):.1f}" '
            f'height="{max(2, abs(y2 - y1)):.1f}" fill="{theme["hub"]}" '
            f'fill-opacity="0.16" stroke="{theme["hub"]}" stroke-dasharray="5 4" '
            f'data-layer="hub"/>'
        )
        if hub_labels < max_hub_labels:
            out.append(
                svg_text(
                    x1 + 3,
                    min(y1, y2) - 7,
                    f"ZG {fmt_price(num(hub.get('zg')))} / ZD {fmt_price(num(hub.get('zd')))}",
                    theme["hub"],
                    11,
                )
            )
            hub_labels += 1

    # Optional P2 segments sit above hubs and below strict pens.
    segment_labels = 0
    for segment in reversed(segments):
        endpoints = segment_endpoints(segment, bis)
        if not endpoints:
            continue
        start_date, start_price, end_date, end_price = endpoints
        start = visible_index(start_date)
        end = visible_index(end_date)
        if start is None or end is None:
            continue
        confirmed = segment.get("confirmed") is True
        dash = "" if confirmed else 'stroke-dasharray="8 5"'
        out.append(
            f'<line x1="{sx(start):.1f}" y1="{sy(start_price):.1f}" '
            f'x2="{sx(end):.1f}" y2="{sy(end_price):.1f}" '
            f'stroke="{theme["segment"]}" stroke-width="4" stroke-opacity="0.52" '
            f'{dash} stroke-linecap="round" data-layer="segment" '
            f'data-confirmed="{str(confirmed).lower()}" '
            f'data-break-type="{esc(segment.get("break_type", ""))}"/>'
        )
        if segment_labels < 1:
            direction = "↑" if segment.get("direction") == "up" else "↓"
            label = "线段%s %s笔" % (direction, segment.get("n_bis") or "?")
            mid_x = (sx(start) + sx(end)) / 2
            mid_y = (sy(start_price) + sy(end_price)) / 2 - 6
            label_x = min(max(mid_x, plot_x + 58), plot_x + plot_w - 58)
            out.append(
                svg_text(
                    label_x,
                    mid_y,
                    label,
                    theme["segment"],
                    10,
                    "middle",
                    600,
                    'data-layer="segment-label"',
                )
            )
            segment_labels += 1

    # Strokes: connect endpoints in the analysis' stroke order.
    stroke_points = []
    endpoint_dates = set()
    for bi in bis:
        if bi.get("type") == "up":
            a_date, a_price = bi.get("bottom_date"), bi.get("bottom")
            b_date, b_price = bi.get("top_date"), bi.get("top")
        else:
            a_date, a_price = bi.get("top_date"), bi.get("top")
            b_date, b_price = bi.get("bottom_date"), bi.get("bottom")
        a, b = visible_index(a_date), visible_index(b_date)
        if a is None or b is None:
            continue
        stroke_points.extend([(a, num(a_price)), (b, num(b_price))])
        endpoint_dates.update([str(a_date), str(b_date)])
        out.append(
            f'<line x1="{sx(a):.1f}" y1="{sy(num(a_price)):.1f}" '
            f'x2="{sx(b):.1f}" y2="{sy(num(b_price)):.1f}" '
            f'stroke="{theme["pen"]}" stroke-width="2.2" stroke-linecap="round" '
            f'data-layer="pen"/>'
        )
        for idx, price in ((a, num(a_price)), (b, num(b_price))):
            out.append(
                f'<circle cx="{sx(idx):.1f}" cy="{sy(price):.1f}" r="3.2" '
                f'fill="{theme["pen"]}" data-layer="pen-endpoint"/>'
            )

    if fractal_mode != "none":
        fractal_rows = fractals
        if fractal_mode == "endpoints" or len(fractal_rows) > 90:
            fractal_rows = [f for f in fractal_rows if str(f.get("date")) in endpoint_dates]
        for fractal in fractal_rows:
            idx = visible_index(fractal.get("date"))
            if idx is None:
                continue
            x, price = sx(idx), sy(num(fractal.get("price")))
            is_top = fractal.get("type") == "top"
            y = price - 11 if is_top else price + 11
            color = theme["fractal_top"] if is_top else theme["fractal_bottom"]
            points = (
                f"{x:.1f},{y - 5:.1f} {x - 5:.1f},{y + 4:.1f} {x + 5:.1f},{y + 4:.1f}"
                if is_top
                else f"{x:.1f},{y + 5:.1f} {x - 5:.1f},{y - 4:.1f} {x + 5:.1f},{y - 4:.1f}"
            )
            out.append(
                f'<polygon points="{points}" fill="{color}" fill-opacity="0.9" '
                f'data-layer="fractal-{fractal.get("type")}"/>'
            )

    # Signal and structural-position markers.
    unique_signals = []
    seen = set()
    for signal in signals:
        key = (signal.get("date"), signal.get("price"), signal.get("kind"))
        if key not in seen:
            seen.add(key)
            unique_signals.append(signal)
    signal_rows = unique_signals[-max_signals:]
    for signal_index, signal in enumerate(signal_rows):
        idx = visible_index(signal.get("date"))
        if idx is None or signal.get("price") is None:
            continue
        x, y = sx(idx), sy(num(signal.get("price")))
        kind = str(signal.get("kind", "背离候选"))
        label = "顶背离候选" if "顶" in kind else "底背离候选" if "底" in kind else clean_label(kind)
        out.append(
            f'<path d="M {x:.1f} {y - 6:.1f} L {x + 6:.1f} {y:.1f} '
            f'L {x:.1f} {y + 6:.1f} L {x - 6:.1f} {y:.1f} Z" '
            f'fill="{theme["signal"]}" fill-opacity="0.88" data-layer="signal"/>'
        )
        if signal_index == len(signal_rows) - 1:
            label_x = x + 8 if x < plot_x + plot_w - 105 else x - 8
            label_anchor = "start" if x < plot_x + plot_w - 105 else "end"
            out.append(svg_text(label_x, y - 8, label, theme["signal"], 11, label_anchor))

    if p4_signal is not None and p4:
        idx = visible_index(p4_signal.get("date"))
        if idx is not None:
            x, y = sx(idx), sy(num(p4_signal.get("price")))
            out.append(
                f'<path d="M {x:.1f} {y - 7:.1f} L {x + 7:.1f} {y:.1f} '
                f'L {x:.1f} {y + 7:.1f} L {x - 7:.1f} {y:.1f} Z" '
                f'fill="none" stroke="{theme["signal"]}" stroke-width="2" '
                f'data-layer="p4-divergence" data-state="{esc(p4.get("state", ""))}" '
                f'data-sub-status="{esc(p4.get("sub_status", "unknown"))}"/>'
            )
            label_x = x + 9 if x < plot_x + plot_w - 145 else x - 9
            label_anchor = "start" if x < plot_x + plot_w - 145 else "end"
            out.append(
                svg_text(
                    label_x,
                    y - 26,
                    p4_label(p4),
                    theme["signal"],
                    10,
                    label_anchor,
                    600,
                    'data-layer="p4-label"',
                )
            )

    position_count = 0
    for position in positions:
        if (
            position is p4_signal
            or str(position.get("source", "")).startswith("结构信号")
            or position.get("note")
            or position.get("price") is None
        ):
            continue
        idx = visible_index(position.get("date"))
        if idx is None:
            continue
        x, y = sx(idx), sy(num(position.get("price")))
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="none" '
            f'stroke="{theme["position"]}" stroke-width="2" data-layer="position"/>'
        )
        label_x = x + 9 if x < plot_x + plot_w - 85 else x - 9
        label_anchor = "start" if x < plot_x + plot_w - 85 else "end"
        out.append(svg_text(label_x, y + 14, "A类候选", theme["position"], 10, label_anchor))
        position_count += 1
        if position_count >= max_signals:
            break

    summary = analysis.get("summary") or {}
    summary_bits = [
        f"笔 {len(bis)}",
        f"中枢 {len(hubs)}",
        f"收盘 {fmt_price(bars[-1]['close'])}",
    ]
    if summary.get("包含处理后K线"):
        summary_bits.insert(1, f"合并 {summary['包含处理后K线']}")
    if p4:
        summary_bits.append("P4 %s" % p4_label(p4))
    if segment_payload:
        summary_bits.append(
            "线段 %s/%s"
            % (
                segment_payload.get("n_confirmed", len(segments)),
                segment_payload.get("n_segments", len(segments)),
            )
        )
        if segment_payload.get("error"):
            summary_bits.append("P2错误")
    structure_mode = analysis.get("structure_mode")
    if structure_mode:
        summary_bits.append("中枢口径 %s" % structure_mode)
    if analysis.get("structure_fallback"):
        summary_bits.append("线段中枢回退")
    out.append(
        svg_text(
            plot_x,
            top + panel_height - 8,
            "｜".join(summary_bits),
            theme["muted"],
            12,
        )
    )
    out.append("</g>")
    return out


def render_svg(
    panels: list[Panel],
    title: str,
    width: int,
    panel_height: int,
    mode: str,
    theme_name: str,
    palette: str,
    max_bars: int,
    fractal_mode: str,
    max_hub_labels: int,
    max_signals: int,
    segment_mode: str,
) -> str:
    theme = THEMES[theme_name]
    gap = 38
    header = 92
    footer = 44
    height = header + len(panels) * panel_height + max(0, len(panels) - 1) * gap + footer
    width = max(720, width)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" '
        f'font-family="-apple-system,BlinkMacSystemFont,\'PingFang SC\',\'Microsoft YaHei\',sans-serif">',
        f"<title>{esc(title)}</title>",
        f"<desc>缠论结构图：分型、笔、中枢、背离候选与结构位置；仅形态标注。</desc>",
        f'<rect width="{width}" height="{height}" fill="{theme["bg"]}"/>',
        svg_text(40, 34, title, theme["ink"], 23, weight=600),
        svg_text(
            40,
            58,
            (
                "笔=蓝实线｜中枢=虚线框｜线段=粗半透明线｜实/空心菱形=力度/P4｜圆环=A类结构位置（形态）"
                if any(select_segments(panel.analysis, segment_mode)[0] for panel in panels)
                else "实线=笔｜虚线框=中枢｜实心菱形=背离候选｜空心菱形=P4结构段｜圆环=A类结构位置（形态）"
            ),
            theme["muted"],
            12,
        ),
        f'<line x1="40" y1="72" x2="{width - 40}" y2="72" stroke="{theme["frame"]}"/>',
    ]
    for i, panel in enumerate(panels):
        top = header + i * (panel_height + gap)
        out.extend(
            render_panel(
                panel,
                top,
                width,
                panel_height,
                mode,
                palette,
                theme,
                max_bars,
                fractal_mode,
                max_hub_labels,
                max_signals,
                segment_mode,
            )
        )
    first_date = panels[0].bars[0]["date"] if panels and panels[0].bars else ""
    last_date = panels[0].bars[-1]["date"] if panels and panels[0].bars else ""
    out.extend(
        [
            f'<line x1="40" y1="{height - 34}" x2="{width - 40}" y2="{height - 34}" '
            f'stroke="{theme["frame"]}"/>',
            svg_text(
                40,
                height - 14,
                f"数据范围：{first_date} → {last_date}｜生成：{datetime.now().strftime('%Y-%m-%d %H:%M')}｜仅形态结构标注，不构成投资建议",
                theme["muted"],
                11,
            ),
            "</svg>",
        ]
    )
    return "\n".join(out) + "\n"


def synthetic_bars(count: int = 160) -> list[dict[str, Any]]:
    bars = []
    base = 100.0
    for i in range(count):
        wave = math.sin(i / 8) * 3.0 + math.sin(i / 21) * 6.0
        trend = i * 0.08
        close = base + trend + wave
        open_ = close - math.sin(i / 3) * 0.6
        high = max(open_, close) + 1.2 + (i % 3) * 0.15
        low = min(open_, close) - 1.2 - (i % 2) * 0.2
        bars.append(
            {
                "date": f"2026-01-{i % 28 + 1:02d} {i % 10:02d}:00",
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": 1000 + i,
            }
        )
    return bars


def self_test() -> int:
    bars = synthetic_bars()
    analysis = fallback_analysis(bars, min_gap=4)
    quality = bis_chain_quality(analysis["bis"])
    if not quality["alternating"] or quality["continuous"] is not True:
        print("FAIL strict bis chain:", quality)
        return 1
    analysis["p4_divergence"] = {
        "state": "扩展",
        "amp_ratio": 1.21,
        "has_sub_zs": False,
        "sub_status": "unknown",
        "div_type": "待定",
    }
    analysis["buy_sell"].append(
        {
            "kind": "结构信号(幅度比)",
            "source": "结构信号(幅度比)",
            "state": "扩展",
            "date": bars[-1]["date"],
            "price": bars[-1]["high"],
            "note": "次级别数据缺失",
        }
    )
    first_bi = analysis["bis"][0]
    last_bi = analysis["bis"][min(len(analysis["bis"]) - 1, 5)]
    start_price = first_bi["top"] if first_bi["type"] == "down" else first_bi["bottom"]
    end_price = last_bi["bottom"] if first_bi["type"] == "down" else last_bi["top"]
    analysis["segment_analysis"] = {
        "engine": "p2",
        "n_segments": 2,
        "n_confirmed": 1,
        "segments": [
            {
                "direction": first_bi["type"],
                "start_bi": 0,
                "end_bi": min(len(analysis["bis"]) - 1, 5),
                "start_price": start_price,
                "end_price": end_price,
                "confirmed": True,
                "break_type": "self_test",
                "n_bis": min(len(analysis["bis"]), 6),
            }
        ],
        "seg_zhongshu": [],
    }
    panel = Panel("自检", bars, analysis, analysis["source"], "TEST", "1h")
    svg = render_svg(
        [panel], "缠论画线自检", 1200, 390, "candle", "light", "cn",
        0, "endpoints", 2, 4, "confirmed",
    )
    required = ('data-layer="candle"', 'data-layer="pen"', 'data-layer="hub"',
                'data-layer="fractal', 'data-layer="p4-divergence"',
                'data-sub-status="unknown"', 'data-layer="segment"',
                "次级别缺失",
                "仅形态结构标注，不构成投资建议")
    missing = [token for token in required if token not in svg]
    if missing:
        print("FAIL missing:", missing)
        return 1
    if "结构段：扩展｜次级别缺失" not in svg:
        print("FAIL P4 label")
        return 1
    with tempfile.NamedTemporaryFile(mode="w", suffix=".svg", delete=False, encoding="utf-8") as handle:
        handle.write(svg)
        path = handle.name
    try:
        print("PASS", path, "bytes=", len(svg.encode("utf-8")))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="缠论画线：CSV/JSON → SVG")
    parser.add_argument("--version", action="version", version=DRAWING_VERSION)
    parser.add_argument("--bars", help="单面板 K 线 CSV/JSON")
    parser.add_argument("--analysis", help="单面板结构 JSON（可选）")
    parser.add_argument("--panel", action="append", help="多面板：LABEL=BARS[,ANALYSIS]")
    parser.add_argument("--label", default="结构图", help="单面板标题（默认：结构图）")
    parser.add_argument("--symbol", default="", help="标的代码/名称")
    parser.add_argument("--title", default="缠论结构图", help="总标题")
    parser.add_argument("--out", help="输出 SVG 路径")
    parser.add_argument("--width", type=int, default=1200, help="SVG 宽度，最小 720")
    parser.add_argument("--panel-height", type=int, default=390)
    parser.add_argument("--mode", choices=["auto", "candle", "line"], default="auto")
    parser.add_argument("--theme", choices=["light", "dark"], default="light")
    parser.add_argument("--palette", choices=["cn", "intl"], default="cn")
    parser.add_argument("--max-bars", type=int, default=0, help="只画最后 N 根，0=全部")
    parser.add_argument("--fractals", choices=["none", "endpoints", "all"], default="endpoints")
    parser.add_argument("--no-fractals", action="store_true", help="等价于 --fractals none")
    parser.add_argument("--max-hub-labels", type=int, default=2)
    parser.add_argument("--max-signals", type=int, default=4)
    parser.add_argument("--min-gap", type=int, default=4, help="兜底分析的笔最小跨度")
    parser.add_argument("--segment-engine", choices=["none", "p2"], default="none")
    parser.add_argument("--segments", choices=["none", "confirmed", "all"], default="confirmed")
    parser.add_argument("--structure-mode", choices=["pen", "segment"], default="pen")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.out:
        parser.error("需要 --out 输出 SVG")

    specs = parse_panel_specs(args)
    panels = []
    for label, bars_path, analysis_path in specs:
        bars = read_bars(bars_path)
        if not bars:
            raise ValueError("没有解析到有效 K 线: %s" % bars_path)
        validate_bars(bars)
        analysis, source = analyze_bars(
            bars,
            label,
            args.symbol or label,
            args.min_gap,
            analysis_path,
            args.segment_engine,
            args.structure_mode,
        )
        panels.append(
            Panel(
                label=label,
                bars=bars,
                analysis=analysis,
                source=source,
                symbol=args.symbol or label,
                period=infer_period(label),
            )
        )
    svg = render_svg(
        panels,
        args.title,
        args.width,
        args.panel_height,
        args.mode,
        args.theme,
        args.palette,
        args.max_bars,
        "none" if args.no_fractals else args.fractals,
        args.max_hub_labels,
        args.max_signals,
        args.segments,
    )
    Path(args.out).write_text(svg, encoding="utf-8")
    print(
        "OK %s｜panels=%d｜bytes=%d｜sources=%s"
        % (
            args.out,
            len(panels),
            len(svg.encode("utf-8")),
            ",".join(panel.source for panel in panels),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
