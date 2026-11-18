# 变更记录

## 0.3.0 — 2026-10-24

- 接入 `chanlun-obays` 可选 P2 线段输出：支持 `segment_analysis.segments`、确认/未确认线段和线段中枢。
- 新增 `--segment-engine`、`--segments`、`--structure-mode`，外部 analysis JSON 与在线分析器均可用。
- 图层增加粗半透明线段，线段端点从 `start_bi/end_bi` 映射到实际 K 线日期。
- 补充 P2 契约、图层规范和回归测试；默认仍不启用线段，不改变既有笔结构图。

## 0.2.0 — 2026-10-23

- 对齐 `chanlun-obays` 2.9.6：内置兜底改为 strict 连笔，并在端点前移时同步回写上一笔，保证笔链连续。
- 接入 `p4_divergence` 与对应结构信号，新增“P4 结构段”图层；`sub_status=unknown` 明确显示“次级别缺失”。
- P4 状态改用中性结构标签，避免把“随时结束”等状态渲染成价格预测。
- 增加 K 线时间升序校验、外部笔链连续性诊断、图层数据属性与回归测试。
- 更新结构 JSON 契约、画线规范和缠论速查中的 strict 笔链、P2/P4 口径说明。

## 0.1.0 — 2026-10-19

- 新建 `chanlun-drawing` 技能，定位为缠论画线层。
- 新增 `scripts/draw_chanlun.py`：CSV/JSON → SVG，支持单面板与多面板、蜡烛/收盘线、分型、笔、中枢、背离候选、A 类结构位置。
- 默认只画笔端点分型，避免密集图标签过载；`--fractals all` 可展开全部分型。
- 结构来源优先使用 `chanlun-obays` 或外部 `analysis.json`，缺失时使用内置基础规则兜底。
- 新增画线规范、结构 JSON 契约与缠论速查三份参考。
- 自检：`--self-test` 通过；用 COMEX 黄金日线/60分/30分数据完成三面板渲染验证。
