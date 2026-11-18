---
name: chanlun-drawing
description: Create auditable Chanlun (缠论) K-line charts from CSV/JSON, including strict stroke chains, optional P2 segments, fractals, hubs, P4 divergence segments, structural-position labels, and multi-level panels. Use when the user asks to draw or annotate a Chanlun chart; do not use it to produce trading instructions or price forecasts.
version: 0.3.0
display_name: 缠论画线
display_name_en: Chanlun Drawing
description_zh: 把 K 线数据与分析结果画成可核对的缠论结构图：分型、strict 笔、可选 P2 线段、笔/线段中枢、P4 结构段、A 类结构位置与多级别面板。适用于画缠论图、标注结构和把分析结果可视化；不负责交易建议或涨跌预测。
description_en: Render auditable Chanlun structure charts from K-line data and analysis JSON, including strict stroke chains, optional P2 segments, pen/segment hubs, P4 divergence segments, structural-position labels, and multi-level panels. For charting and annotation only.
metadata:
  display_name: 缠论画线
  display_name_en: Chanlun Drawing
  description_zh: 把 K 线数据与分析结果画成可核对的缠论结构图：分型、strict 笔、可选 P2 线段、笔/线段中枢、P4 结构段、A 类结构位置与多级别面板。适用于画缠论图、标注结构和把分析结果可视化；不负责交易建议或涨跌预测。
  description_en: Render auditable Chanlun structure charts from K-line data and analysis JSON, including strict stroke chains, optional P2 segments, pen/segment hubs, P4 divergence segments, structural-position labels, and multi-level panels. For charting and annotation only.
  version: 0.3.0
---

# 缠论画线

## 定位

本技能是**画线层**，不是第二套缠论分析引擎。

- **结构来源**：优先消费 `chanlun-obays`（≥ 2.9.6）的 `chanlun_analyze.py` 输出，或用户提供的兼容 `analysis.json`。
- **本技能负责**：图层顺序、K 线/笔/可选 P2 线段/中枢的几何绘制、多级别排版、标签避让、SVG 产物与可核对性。
- **默认兜底**：找不到结构分析器时，脚本使用与 `chanlun-obays` 一致的 strict 连笔口径及最小中枢规则；兜底结果必须在图标题或说明中标注“基础规则兜底（strict）”。
- **边界**：只画已完成的历史结构，不画预测箭头，不把 A 类结构位置写成操作指令。

## 工作流

1. **确认数据**：CSV 至少包含 `date,open,high,low,close`；JSON 接受数组或 `data/bars/klines` 字段。确认标的、周期与时间范围。
2. **取得结构**：
   - 优先给每个面板传 `analysis.json`（`chanlun_analyze.py` 或兼容结构文件的输出）。
   - 没有结构文件时，脚本会尝试调用同机的 `chanlun-obays/scripts/chanlun_analyze.py`。
   - 两者都没有时，才使用内置 strict 基础规则兜底。
3. **画图**：用 `scripts/draw_chanlun.py` 输出 SVG。单级别用 `--bars`；多级别重复 `--panel`。
4. **检查**：打开 SVG 检查标题、轴、笔端点、笔链连续性、可选线段、中枢矩形、P4 结构段及标签是否重叠；密集数据优先切 `--mode line` 或只画最后 `--max-bars` 根。
5. **交付**：返回 SVG 路径、数据范围、结构来源（`chanlun-obays` / 外部 JSON / 基础兜底）和未解决的口径分歧。不要补写图里没有的方向结论。

## 命令

单面板：

```bash
python3 scripts/draw_chanlun.py \
  --bars data/day.csv \
  --analysis data/day.json \
  --title "COMEX 黄金 · 日线" \
  --out chart.svg
```

多面板（推荐用于日线 / 60 分 / 30 分）：

```bash
python3 scripts/draw_chanlun.py \
  --panel "日线=data/day.csv,data/day.json" \
  --panel "60分=data/60.csv,data/60.json" \
  --panel "30分=data/30.csv,data/30.json" \
  --title "COMEX 黄金 · 三级结构" \
  --out chart.svg
```

没有结构 JSON 时省略逗号后的路径即可。脚本会在 `CHANLUN_ANALYZE`、同机 `chanlun-obays` 安装路径和技能兄弟目录中寻找分析器。

常用参数：

- `--mode auto|candle|line`：默认超过 260 根自动切收盘线；
- `--fractals endpoints|all|none`：默认只画笔端点分型，避免密集图堆满三角；
- `--palette cn|intl`：红涨绿跌 / 绿涨红跌；
- `--max-bars N`：只画最后 N 根，适合局部放大；
- `--theme light|dark`：浅色 / 深色 SVG。
- `--segment-engine none|p2`：无外部结构 JSON 时，可选择启用 P2 线段引擎；
- `--segments none|confirmed|all`：叠加 `segment_analysis.segments`，默认只画确认段；
- `--structure-mode pen|segment`：中枢 SSOT 使用笔或已确认线段；仅 `segment` 有确认线段时生效。

## 画线约定

- **图层顺序**：网格 → K 线 → 中枢 → 笔 → 分型/信号 → 标签。中枢必须在 K 线之上、笔之下，避免遮住端点。
- **笔**：用端点日期和价格连接，不在笔中间画预测延伸线。向上笔与向下笔使用同一种线型，方向由端点和折线表达。
- **笔链**：strict 算法只做同向端点压缩与异向成笔，不删除已成立的笔；端点前移时同步回写上一笔终点。图副标题显示笔链是否连续。
- **P2 线段**：只在结构 JSON 含 `segment_analysis` 时画粗半透明线；确认段为实线，未确认段（`--segments all`）为虚线。多个线段不得自动改写成价格预测。
- **中枢**：用半透明矩形覆盖 `start_date → end_date` 与 `ZD → ZG`；标签写 `ZG / ZD`，不写“支撑/压力”。
- **分型**：顶分型画在最高价上方，底分型画在最低价下方；只画结构分析给出的分型，不把每根局部极值都标成买卖点。
- **背离**：菱形标记 + “背离候选”；只有趋势背驰才可写“背驰”，没有趋势时不要升级术语。
- **P4 结构段**：当 `analysis.json` 含 `p4_divergence` 及对应的 `buy_sell` 结构信号时，用独立菱形和中性状态标签绘制；`sub_status=unknown` 明示“次级别缺失”，不得据此确认扩展、变异或走势结束。
- **A 类位置**：圆环 + “A类结构位置（形态）”；不写“买点/卖点”作为指令，不添加预期幅度。
- **多级别**：每个级别独立坐标轴、独立结构，不把不同价格尺度的线叠在同一面板。
- **合规**：图页脚保留“仅形态结构标注，不构成投资建议”；不要在图上加仓位、止损、目标价或预测箭头。

详细排版、颜色与数据契约见：

- [`references/drawing-spec.md`](references/drawing-spec.md)
- [`references/analysis-json.md`](references/analysis-json.md)
- [`references/chanlun-primer.md`](references/chanlun-primer.md)

## 验证

```bash
python3 scripts/draw_chanlun.py --self-test
python3 -m unittest discover -s tests -v
python3 /Users/ozqy/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```

`--self-test` 会用合成 K 线生成临时 SVG，检查 strict 笔链、P2 线段、基本图层、P4 `sub_status=unknown` 语义与输出结构。
