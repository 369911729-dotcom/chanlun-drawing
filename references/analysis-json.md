# 结构 JSON 契约

画线器只读取以下字段。未知字段会保留但不会参与绘图。

## 1. K 线数组

```json
[
  {
    "date": "2026-10-16 22:30",
    "open": 4210.0,
    "high": 4220.4,
    "low": 4205.1,
    "close": 4217.7,
    "volume": 12345
  }
]
```

CSV 使用同名列即可；`date` 必须按时间升序，跨日数据不要只保留日期。

## 2. 分析对象

```json
{
  "source": "chanlun-obays",
  "summary": {
    "K线数": 300,
    "包含处理后K线": 237,
    "有效笔数": 10,
    "中枢数": 2,
    "最新收盘": 4186.7
  },
  "fractals": [
    {"type": "top", "date": "2025-12-01", "price": 4299.6}
  ],
  "bis": [
    {
      "type": "up",
      "top_date": "2025-12-01",
      "top": 4299.6,
      "bottom_date": "2025-11-21",
      "bottom": 4018.1
    }
  ],
  "zhongshu": [
    {
      "start_date": "2025-10-20",
      "end_date": "2025-12-08",
      "zg": 4299.6,
      "zd": 4197.8,
      "hints": ["疑似收敛中枢"]
    }
  ],
  "signals": [
    {"kind": "顶背驰/背离(力度衰减)", "date": "2025-12-01", "price": 4299.6}
  ],
  "buy_sell": [
    {"kind": "顶背离候选", "date": "2026-10-16", "price": 4220.4},
    {
      "kind": "结构信号(幅度比)",
      "date": "2026-10-16",
      "price": 4220.4,
      "source": "结构信号(幅度比)",
      "state": "扩展"
    }
  ],
  "p4_divergence": {
    "state": "扩展",
    "amp_ratio": 1.2,
    "has_sub_zs": true,
    "sub_status": "found",
    "div_type": "中枢延伸",
    "notes": ["次级别同向中枢存在"]
  },
  "segment_analysis": {
    "engine": "p2",
    "n_segments": 2,
    "n_confirmed": 1,
    "segments": [
      {
        "direction": "down",
        "start_bi": 0,
        "end_bi": 15,
        "start_price": 4398.0,
        "end_price": 3955.4,
        "break_type": "extreme_break",
        "gap": false,
        "confirmed": true,
        "n_bis": 16
      }
    ],
    "seg_zhongshu": []
  }
}
```

兼容字段：

- `hubs` 可作为 `zhongshu` 的别名；
- `bis` 的起止顺序由 `type` 决定，`up` 从 `bottom_date` 连到 `top_date`，`down` 反之；
- `fractals` 的 `date` 必须能在 K 线日期中找到；找不到时该分型不画；
- `buy_sell` 中带 `note` 的条目视为力度信号副本，不画成 A 类位置；
- `buy_sell` 中 `source="结构信号(幅度比)"` 且带 `state` 的条目与 `p4_divergence` 配对，
  以 `data-layer="p4-divergence"` 绘制一次，不重复画成 A 类位置。

`p4_divergence.sub_status` 为三态：

- `found`：次级别同向中枢已确认；
- `absent`：次级别结构已提供且确认没有同向中枢；
- `unknown`：次级别数据缺失。图上必须显示“次级别缺失”，不得表述为已确认无同向中枢。

P4 只作结构段状态展示，不把 `随时结束` 等状态改写成价格预测。

`segment_analysis` 是 `chanlun-obays --segment-engine p2` 的可选补充输出：

- `segments[].confirmed=true` 才画实线；否则只在 `--segments all` 时画虚线；
- `start_bi/end_bi` 是笔序列索引，画线器从对应笔端点解析日期；
- `structure_mode="segment"` 时主 `zhongshu` 已是线段中枢，原笔中枢保留在 `pen_zhongshu`；
- 没有确认线段时结构源会回退笔中枢，画线器不得伪造线段。

## 3. 生成方式

优先使用 `chanlun-obays` 的确定性分析器：

```bash
python3 ~/.cc-switch/skills/chanlun-obays/scripts/chanlun_analyze.py \
  data/day.csv --symbol GC --period day > data/day.json
```

需要 P2 线段或线段级中枢时：

```bash
python3 ~/.cc-switch/skills/chanlun-obays/scripts/chanlun_analyze.py \
  data/day.csv --symbol GC --period day --segment-engine p2 \
  --structure-mode segment > data/day-p2.json
```

也可以用 `detect_structure.py --out` 生成 `bis`/`hubs` 检测层，再补上
`fractals`/`signals`/`buy_sell`；若分析器提供 `p4_divergence`，一并保留其原始状态与
`sub_status`。画线器会兼容 `hubs` 别名。

外部 JSON 的 `bis` 若带 `start_idx/end_idx`，画线器会检查笔链连续性；断裂只作诊断展示，
不会擅自“修笔”改写来源结构。

## 4. 来源标记

画线器输出会记录三种来源：

- `外部 analysis.json`：调用方提供；
- `chanlun-obays`：脚本自动找到并运行了同机分析器；
- `基础规则兜底`：没有分析器时使用内置最小分型/笔/中枢算法。

来源不是装饰：多级别分析中若真身和代理、不同引擎混用，必须在交付说明中写清楚。
