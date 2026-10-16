---
name: charts-and-export
description: 画图（K 线/折线/柱状 PNG）、把 Markdown 报告导出为 docx、把表格导出为 xlsx。需要给人一张图片、一份 Word 或 Excel 交付物时使用。
---

# 画图与导出

三个脚本，都在 `scripts/` 下，用 bash 调用，输入输出全是文件。做完一定用 `muse_attach_file` 把产物挂到对话里。

| 脚本 | 用途 | 用法 |
|---|---|---|
| `plot.py` | 折线 / 柱状 / K 线 → PNG | `python scripts/plot.py spec.json out.png` |
| `md_to_docx.py` | Markdown → Word | `python scripts/md_to_docx.py reports/600519.md reports/600519.docx` |
| `table_to_xlsx.py` | JSON 表格 → Excel（多 sheet） | `python scripts/table_to_xlsx.py tables.json out.xlsx` |

## plot.py 的 spec 格式

```json
{"type": "line", "title": "600519 收盘价", "x": ["2026-01-02", "..."],
 "series": [{"name": "收盘价", "values": [1500.2, "..."]}], "y_label": "元",
 "note": "数据来源：同花顺问财，查询时间 2026-10-16"}
```

`type` 可选 `line` / `bar` / `candle`。K 线时 `series` 换成 `"ohlc": [[open, high, low, close], ...]`，可选 `"volume": [...]`。

## 什么时候用哪个

- 给人**看一眼**趋势：优先 `muse_show_chart`（不落盘，直接出现在对话里）。
- 要进报告、要发出去、或 K 线这种 `muse_show_chart` 画不了的：用 `plot.py` 出 PNG，再 `muse_attach_file`。
- 报告定稿要外发：`md_to_docx.py`，外发前先 `muse_request_approval`。
- 因子表、持仓表给人下载：`table_to_xlsx.py`。

## 约束

- 图上必须有标题和数据来源注脚（`note` 字段）。
- 中文字体：脚本会自动找系统里的 Noto Sans CJK / PingFang / 微软雅黑；找不到时中文会显示为方块，这时把标题改成英文并在 `muse_show_markdown` 里说明。
- 缺依赖时脚本会打印 `pip install ...` 命令并退出 2；不要自己 pip install，把命令告诉用户。
