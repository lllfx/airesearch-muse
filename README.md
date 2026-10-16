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
| `dsh/` | YAML + Python 脚本 | dsh 的覆盖层（`muse.patch.yml`）、skills：`stock-research`、问财五件套、`charts-and-export` |
| `backend/` | Python 3.11+ / FastAPI | 任务状态机、存储（任务 / 输入请求 / 审批 / 回执 / 记忆 / 资料库 / 定时）、驱动 dsh、给 agent 的 19 个 MCP 工具 |
| `frontend/` | React + Vite + TS | 三栏：左任务列表（按"该谁动"分组）、中对话流（卡片）、右计划 / 产出 / 资料库 / 记忆 / 定时 |

## 任务怎么流转

```
backlog → queued → running ─┬─► waiting_input ────► running  (你回答后)
                            ├─► waiting_approval ─► running  (你批准/拒绝后)
                            ├─► review → done              (requires_review=true，你验收)
                            ├─► done                        (requires_review=false)
                            ├─► failed → queued            (自动重试，≤ max_retries)
                            └─► interrupted → queued       (后端重启后一键恢复)
```

一个任务就是一段对话。你随时可以在输入框里说话：任务在跑就排到当前轮结束后送达（dsh 的 followup），在等你就当作回答，结束了就重开。

挂起不是 dsh 的功能，是这个项目做的：后端通过 MCP 给 agent 一组工具——

| 组 | 工具 | 作用 |
|---|---|---|
| 看板 | `muse_plan` `muse_request_input` `muse_request_approval` `muse_receipt` | 计划、提问（可带表单 `fields`）、审批、回执。提问和审批会让 agent 结束本轮，任务进"等你处理" |
| 富媒体 | `muse_show_table` `muse_show_chart` `muse_show_markdown` `muse_attach_file` | 表格、折线/柱状图、笔记、文件（图片内联）直接出现在对话里 |
| 协作 | `muse_spawn_subtask` `muse_memory_set/get` `muse_library_add/search` `muse_schedule` | 子任务（结论回传父任务）、跨任务记忆、可检索的研究资料库（SQLite FTS5）、定时任务 |
| 问财 | `iwencai_market_query` `iwencai_shareholder_query` `iwencai_search` | 同花顺问财原生工具：行情 / 股东股本 / 公告·新闻·研报。结构化返回，原始响应存 `data/iwencai/` |

你回复后，后端往**同一个 dsh 会话**发一条新消息，agent 带着完整上下文继续。dsh 的会话日志就是断点。

agent 自己的能力（bash / 读写文件 / web 搜索）来自 dsh 的 base bundle；`charts-and-export` skill 补了画图（含 K 线）、Markdown→docx、表格→xlsx 三个脚本。

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

- 浏览器接管
- 多用户 / 鉴权（`MUSE_TOKEN` 只用于 dsh → 后端的 MCP 调用）
- dsh 自己的子代理可视化（dsh 会报 `subagent.started`，后端暂时忽略；用 `muse_spawn_subtask` 的子任务是可见的）
- dsh 的审批（`approval/request`）在 SDK 模式下不可用，所以沙箱提权请求会被直接拒绝；需要更宽权限时在 `muse.patch.yml` 里调 `sandbox-policy`
- 定时任务的触发精度是 30 秒；后端不在线时不会补跑
