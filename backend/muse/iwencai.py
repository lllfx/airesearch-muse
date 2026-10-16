"""同花顺问财 OpenAPI 客户端。把 dsh/skills 里五个脚本的请求形态收敛成一个类，供 MCP 原生工具调用。

两类接口：
  query2data          行情 / 股东股本，自然语言问句 → datas 列表（POST {query,page,limit,is_cache,expand_index}）
  comprehensive/search 公告 / 新闻 / 研报（POST {query, channels:[...], app_id, size}）
网关要求 8 个头：Authorization + Content-Type + 6 个 X-Claw-*。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_IDS = {
    "market": "hithink-market-query",
    "shareholder": "hithink-management-query",
    "announcement": "announcement-search",
    "news": "news-search",
    "report": "report-search",
}
PREVIEW_ROWS = 5


class IwencaiClient:
    def __init__(self, api_key: str, base_url: str = "https://openapi.iwencai.com", workspace: Path | None = None, timeout: int = 30):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace or Path.cwd()
        self.timeout = timeout

    @classmethod
    def from_env(cls, workspace: Path) -> "IwencaiClient | None":
        key = os.environ.get("IWENCAI_API_KEY", "").strip()
        if not key:
            return None
        return cls(key, os.environ.get("IWENCAI_BASE_URL", "https://openapi.iwencai.com"), workspace,
                   int(os.environ.get("IWENCAI_TIMEOUT", "30")))

    def _headers(self, skill_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Claw-Call-Type": "normal",
            "X-Claw-Skill-Id": skill_id,
            "X-Claw-Skill-Version": "1.0.0",
            "X-Claw-Plugin-Id": "none",
            "X-Claw-Plugin-Version": "none",
            "X-Claw-Trace-Id": secrets.token_hex(32),
        }

    def _post(self, path: str, body: dict, skill_id: str) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body, ensure_ascii=False).encode(),
                                     headers=self._headers(skill_id), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    async def call(self, kind: str, query: str, page: int = 1, limit: int = 10, size: int = 10) -> dict:
        if kind in ("market", "shareholder"):
            path, body = "/v1/query2data", {"query": query, "page": str(page), "limit": str(limit), "is_cache": "1", "expand_index": "true"}
        else:
            path, body = "/v1/comprehensive/search", {"query": query, "channels": [kind], "app_id": "AIME_SKILL", "size": size}
        status, raw = await asyncio.to_thread(self._post, path, body, SKILL_IDS[kind])
        queried_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out_dir = self.workspace / "data" / "iwencai"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{kind}_{datetime.now():%Y%m%d_%H%M%S}_{secrets.token_hex(2)}.json"
        (out_dir / fname).write_bytes(raw)
        rel = f"data/iwencai/{fname}"
        if status >= 400:
            raise RuntimeError(f"HTTP {status}: {raw[:300].decode(errors='replace')}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"non-JSON response saved at {rel}")
        data, count = _extract(payload)
        return {"queried_at": queried_at, "count": count, "data": data, "path": rel, "preview": data[:PREVIEW_ROWS]}


def _extract(payload: Any) -> tuple[list, int]:
    """两类接口的返回壳不同，尽量找到列表本体。找不到就原样返回，不要吞数据。"""
    if isinstance(payload, dict):
        for key in ("datas", "data", "results", "items", "list"):
            v = payload.get(key)
            if isinstance(v, list):
                return v, int(payload.get("code_count") or payload.get("total") or len(v))
            if isinstance(v, dict):
                inner, n = _extract(v)
                if inner:
                    return inner, n
        return [payload], 1
    if isinstance(payload, list):
        return payload, len(payload)
    return [], 0
