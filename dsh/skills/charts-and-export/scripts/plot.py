#!/usr/bin/env python3
"""spec.json → PNG。支持 line / bar / candle。用法：plot.py spec.json out.png"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
except ImportError:
    print("missing dependency. run: pip install matplotlib", file=sys.stderr)
    sys.exit(2)

PALETTE = ["#2347C5", "#0F6E56", "#B8741A", "#A3322A", "#534AB7", "#1D9E75"]


def _cjk_font() -> None:
    for name in ("Noto Sans CJK SC", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", "WenQuanYi Zen Hei", "Source Han Sans SC"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: plot.py spec.json out.png", file=sys.stderr)
        return 2
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    _cjk_font()
    kind = spec.get("type", "line")
    x = spec.get("x", [])
    fig, ax = plt.subplots(figsize=(10, 5), dpi=130)
    fig.patch.set_facecolor("#F2F3EF")
    ax.set_facecolor("#FFFFFF")

    if kind == "candle":
        ohlc = spec["ohlc"]
        for i, (o, h, l, c) in enumerate(ohlc):
            up = c >= o
            color = "#A3322A" if up else "#0F6E56"   # A 股习惯：红涨绿跌
            ax.vlines(i, l, h, color=color, linewidth=0.8)
            ax.bar(i, abs(c - o) or (h - l) * 0.01, bottom=min(o, c), width=0.6, color=color, edgecolor=color)
        if spec.get("volume"):
            ax2 = ax.twinx()
            ax2.bar(range(len(ohlc)), spec["volume"], width=0.6, color="#8A9099", alpha=0.25)
            ax2.set_yticks([])
        ax.set_xticks(range(0, len(x), max(1, len(x) // 10)))
        ax.set_xticklabels([x[i] for i in range(0, len(x), max(1, len(x) // 10))], rotation=30, ha="right", fontsize=8)
    else:
        for i, s in enumerate(spec.get("series", [])):
            color = PALETTE[i % len(PALETTE)]
            if kind == "bar":
                n = len(spec["series"])
                w = 0.8 / n
                ax.bar([j + (i - (n - 1) / 2) * w for j in range(len(x))], s["values"], width=w, label=s["name"], color=color)
            else:
                ax.plot(range(len(x)), s["values"], label=s["name"], color=color, linewidth=1.6)
        ax.set_xticks(range(0, len(x), max(1, len(x) // 12)))
        ax.set_xticklabels([str(x[i]) for i in range(0, len(x), max(1, len(x) // 12))], rotation=30 if len(x) > 8 else 0, ha="right" if len(x) > 8 else "center", fontsize=8)
        if len(spec.get("series", [])) > 1:
            ax.legend(frameon=False, fontsize=9)

    ax.set_title(spec.get("title", ""), fontsize=13, loc="left", pad=12)
    if spec.get("y_label"):
        ax.set_ylabel(spec["y_label"], fontsize=9)
    ax.grid(axis="y", color="#E9EBE5", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if spec.get("note"):
        fig.text(0.01, 0.01, spec["note"], fontsize=8, color="#8A9099")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    print(f"wrote {out} ({kind}, {len(x)} points)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
