"""FastAPI 应用：本机开发与生产部署共用同一份代码，只切换配置。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .database import init_database
from .routes import agent as agent_routes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="记忆银行 API",
        version="0.1.0",
        description="证据驱动、可恢复、有人类确认的家庭记忆 Agent（本机开发版）。",
        lifespan=lifespan,
    )

    # 开发者工具与真机调试会带 Origin，本地开发放开
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    app.include_router(agent_routes.router)

    # 录音文件访问：演示用静态目录，生产应改为带签名的短期 URL
    app.mount("/media", StaticFiles(directory=settings.objects_dir), name="media")

    # 小程序字体子集：wx.loadFontFace 需要 http(s) 地址
    fonts_dir = settings.project_root / "assets" / "fonts"
    if fonts_dir.exists():
        app.mount("/fonts", StaticFiles(directory=fonts_dir), name="fonts")

    return app


app = create_app()
