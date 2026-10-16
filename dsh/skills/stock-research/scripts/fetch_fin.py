#!/usr/bin/env python3
"""拉取单只股票的财务与行情数据到 data/<code>/。

默认是离线 demo 模式：生成确定性的模拟数据，让整条任务链路不依赖任何外部接口就能跑通。
接真实数据源时替换 fetch_real()，保持输出文件格式不变即可。
设置 MUSE_DATA_SOURCE=fail 可模拟数据源故障，用于测试"请求输入 → 挂起 → 恢复"流程。
"""
from __future__ import annotations
import json, os, sys, hashlib
from pathlib import Path


def demo_data(code: str) -> dict:
    seed = int(hashlib.md5(code.encode()).hexdigest()[:8], 16)
    rnd = lambda i, lo, hi: lo + ((seed >> (i % 24)) % 1000) / 1000 * (hi - lo)
    quarters = [f"2025Q{q}" for q in (1, 2, 3, 4)] + [f"2026Q{q}" for q in (1, 2)]
    return {
        "code": code,
        "name": f"DEMO-{code}",
        "source": "demo-offline",
        "quarters": quarters,
        "revenue": [round(rnd(i, 80, 160) * (1 + 0.03 * i), 1) for i in range(6)],
        "net_profit": [round(rnd(i + 3, 8, 30) * (1 + 0.04 * i), 1) for i in range(6)],
        "roe": [round(rnd(i + 7, 6, 22), 2) for i in range(6)],
        "close_60d": [round(rnd(i + 11, 20, 60) + i * 0.1, 2) for i in range(60)],
        "pe_ttm": round(rnd(13, 8, 45), 1),
        "pb": round(rnd(17, 0.8, 6), 2),
    }


def fetch_real(code: str) -> dict:
    raise NotImplementedError("接入真实数据源时实现这里")


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or len(sys.argv[1]) != 6:
        print("usage: fetch_fin.py <6-digit-code>", file=sys.stderr)
        return 2
    code = sys.argv[1]
    out = Path("data") / code
    out.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("MUSE_DATA_SOURCE", "demo")
    try:
        if mode == "fail":
            raise ConnectionError("simulated data source outage")
        data = fetch_real(code) if mode == "real" else demo_data(code)
    except Exception as e:  # noqa: BLE001
        (out / "meta.json").write_text(json.dumps({"ok": False, "error": str(e), "source": mode}, ensure_ascii=False))
        print(f"data source error: {e}", file=sys.stderr)
        return 1
    (out / "financials.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    (out / "meta.json").write_text(json.dumps({"ok": True, "source": data["source"], "quarters": data["quarters"]}, ensure_ascii=False))
    print(f"wrote {out}/financials.json ({data['source']}, {len(data['quarters'])} quarters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
