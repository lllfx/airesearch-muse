# airesearch-muse

把投研工作**交代**给 AI，而不是和它聊天。

一个任务台：你交代一件事（"研究 600519"），Agent 发布计划、执行、遇到拿不准的事停下来问你、要做外发动作先申请批准、做完留回执、等你验收。引擎是 [DeepSeek Harness (dsh)](https://github.com/deepseek-ai/deepseek-harness)，任务板是自己的。

```
┌ 前端 React ──────┐  REST + SSE  ┌ 后端 Python ───────────────┐  JSON-RPC/stdio  ┌ dsh ─────────┐
│ 等你处理│进行中│…│ ◄──────────► │ 任务状态机 · SQLite · MCP  │ ◄─────────────► │ agent loop   │
└──────────────────┘              │ 工具 muse_* 给 agent 调    │ ◄── MCP/http ── │ skills · 沙箱 │
                                  └────────────────────────────┘                 └──────────────┘
```

## 三个模块

| 目录 | 语言 | 做什么 |
|---|---|---|
| `dsh/` | YAML + Python 脚本 | dsh 的覆盖层（`muse.patch.yml`）、投研 skill（`skills/stock-research`） |
| `backend/` | Python 3.11+ / FastAPI | 任务状态机、四张表（任务 / 输入请求 / 动作审批 / 回执）、驱动 dsh、给 agent 的 MCP 工具 |
| `frontend/` | React + Vite + TS | 按"该谁动"分列的看板、任务抽屉、回答 / 审批 / 验收 |

## 任务怎么流转

```
backlog → queued → running ─┬─► waiting_input ────► running  (你回答后)
                            ├─► waiting_approval ─► running  (你批准/拒绝后)
                            ├─► review → done              (requires_review=true，你验收)
                            ├─► done                        (requires_review=false)
                            ├─► failed → queued            (自动重试，≤ max_retries)
                            └─► interrupted → queued       (后端重启后一键恢复)
```

挂起不是 dsh 的功能，是这个项目做的：后端把四个工具通过 MCP 给 agent——

| 工具 | agent 什么时候调 | 看板上发生什么 |
|---|---|---|
| `muse_plan` | 开工前、每完成一步 | 卡片上出现分段进度条 |
| `muse_request_input` | 拿不准、缺数据 | 任务进"等你处理"列，agent 结束本轮 |
| `muse_request_approval` | 要发邮件 / 推送 / 下单 | 同上，附动作参数等你批 |
| `muse_receipt` | 每份产出 | 回执：路径、数据源、计算窗口 |

你回复后，后端往**同一个 dsh 会话**发一条新消息，agent 带着完整上下文继续。dsh 的会话日志就是断点。

## 跑起来

```sh
# 1. 后端（Python 3.11+）
cd backend && pip install -e ".[dev]"
python -m pytest                       # 用 mock 引擎跑完整条生命周期

# 2. 前端
cd ../frontend && npm install && npm run build   # 产物由后端托管

# 3. 启动（先离线演示，不需要 API key）
cd .. && MUSE_ENGINE=mock python -m muse.main --app-dir backend
# 或：cd backend && MUSE_ENGINE=mock python -m uvicorn muse.main:app --port 8787
```

打开 http://127.0.0.1:8787 → 新任务 → 看它提问、申请审批、交回执。

接真实引擎：

```sh
cp .env.example .env     # 填 DEEPSEEK_API_KEY，MUSE_ENGINE=dsh
set -a; . .env; set +a
cd backend && python -m uvicorn muse.main:app --port 8787
```

后端会自己拉起 `npx @deepseek-ai/dsh --profile sdk --patch dsh/muse.patch.yml`（需要 Node 22+）。首次运行 npx 下载较慢。

开发前端：`cd frontend && npm run dev`（代理 `/api` 到 8787）。

## 写自己的 skill

放到 `dsh/skills/<name>/SKILL.md`，frontmatter 要有 `name`（kebab-case）和 `description`。`stock-research` 是模板：拉数据 → 算因子 → 写报告 → 交回执，数据源不可用时用 `muse_request_input` 问人而不是编数。脚本默认离线 demo 数据，`MUSE_DATA_SOURCE=fail` 模拟故障，接真实数据源改 `fetch_fin.py` 的 `fetch_real()`。

## 目前没有的

- 浏览器接管、定时盯盘（dsh 有 `schedule` 包，还没接）
- 多用户 / 鉴权（`MUSE_TOKEN` 只用于 dsh → 后端的 MCP 调用）
- 子代理可视化（dsh 会报 `subagent.started`，后端暂时忽略）
- dsh 的审批（`approval/request`）在 SDK 模式下不可用，所以沙箱提权请求会被直接拒绝；需要更宽权限时在 `muse.patch.yml` 里调 `sandbox-policy`
