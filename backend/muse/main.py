"""组装：Store → Board → (MCP tools, Engine) → Runner → FastAPI。"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router
from .board import Board, EventBus
from .config import Settings, settings
from .engine import DshEngine, MockEngine
from .iwencai import IwencaiClient
from .mcp_tools import build_mcp
from .runner import Runner
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def create_app(cfg: Settings = settings) -> FastAPI:
    store = Store(cfg.db_path)
    bus = EventBus()
    board = Board(store, bus)
    iwencai = IwencaiClient.from_env(cfg.workspace)
    mcp, tools = build_mcp(board, iwencai)
    engine = MockEngine(tools) if cfg.engine == "mock" else DshEngine(cfg)
    runner = Runner(board, engine, cfg)
    tools.runner = runner
    # 挂在 /mcp 下，所以内部路径设为根；无状态 + JSON 响应，dsh 的 mcp-client 两种都支持
    mcp_app = mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            await runner.start()
            try:
                yield
            finally:
                await runner.stop()
                store.close()

    app = FastAPI(title="airesearch-muse", lifespan=lifespan)
    app.state.board, app.state.runner, app.state.settings = board, runner, cfg
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)
    app.mount("/mcp", mcp_app)  # dsh 的 mcp-client 连这里

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run("muse.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
