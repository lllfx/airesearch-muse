#!/usr/bin/env python3
"""从 data/<code>/financials.json 计算一组基础因子，写 data/<code>/factors.json。

每个因子都带 window 和 source 字段，报告引用时必须一并写出。
"""
from __future__ import annotations
import json, sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: factor_calc.py <code>", file=sys.stderr)
        return 2
    code = sys.argv[1]
    d = Path("data") / code
    fin_path = d / "financials.json"
    if not fin_path.exists():
        print(f"missing {fin_path}; run fetch_fin.py first", file=sys.stderr)
        return 1
    f = json.loads(fin_path.read_text())
    rev, np_, roe, px = f["revenue"], f["net_profit"], f["roe"], f["close_60d"]
    src = f["source"]
    q = f["quarters"]

    def yoy(series):  # 最近一季对比去年同季（6 个季度里取 index 5 vs 1）
        return round((series[-1] / series[-5] - 1) * 100, 2)

    mom60 = round((px[-1] / px[0] - 1) * 100, 2)
    vol = round((max(px) - min(px)) / (sum(px) / len(px)) * 100, 2)
    factors = {
        "code": code,
        "source": src,
        "factors": [
            {"name": "revenue_yoy", "value": yoy(rev), "unit": "%", "window": f"{q[-5]}→{q[-1]}", "source": src},
            {"name": "net_profit_yoy", "value": yoy(np_), "unit": "%", "window": f"{q[-5]}→{q[-1]}", "source": src},
            {"name": "roe_latest", "value": roe[-1], "unit": "%", "window": q[-1], "source": src},
            {"name": "roe_avg_6q", "value": round(sum(roe) / len(roe), 2), "unit": "%", "window": f"{q[0]}→{q[-1]}", "source": src},
            {"name": "momentum_60d", "value": mom60, "unit": "%", "window": "last 60 trading days", "source": src},
            {"name": "range_vol_60d", "value": vol, "unit": "%", "window": "last 60 trading days", "source": src},
            {"name": "pe_ttm", "value": f["pe_ttm"], "unit": "x", "window": "TTM", "source": src},
            {"name": "pb", "value": f["pb"], "unit": "x", "window": "latest", "source": src},
        ],
    }
    (d / "factors.json").write_text(json.dumps(factors, ensure_ascii=False, indent=2))
    for x in factors["factors"]:
        print(f"{x['name']:>16} = {x['value']}{x['unit']}  [{x['window']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
