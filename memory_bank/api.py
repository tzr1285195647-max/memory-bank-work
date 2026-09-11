from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .schemas import InterviewResponse, ProjectCreate, ReviewResponse, RevokeRequest
from .storage import ConsentRevokedError, MemoryStore, ProjectNotFoundError
from .workflow import MemoryBankWorkflow


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    store = MemoryStore(cfg.database_path)
    workflow = MemoryBankWorkflow(store, cfg.checkpoint_path)
    static_dir = Path(__file__).resolve().parent / "static"

    app = FastAPI(
        title="记忆银行 API",
        version="0.1.0",
        description="证据驱动、可恢复、有人类确认的家庭记忆 Agent MVP。",
    )
    app.state.settings = cfg
    app.state.store = store
    app.state.workflow = workflow
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "mode": cfg.mode, "workflow": "langgraph", "version": app.version}

    @app.get("/api/projects")
    def list_projects() -> dict[str, Any]:
        return {"items": store.list_projects()}

    @app.post("/api/projects", status_code=201)
    def create_project(payload: ProjectCreate) -> dict[str, Any]:
        project = store.create_project(**payload.model_dump())
        return workflow.start(project)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return workflow.view(project_id)

    @app.post("/api/projects/{project_id}/respond")
    def respond(project_id: str, payload: InterviewResponse) -> dict[str, Any]:
        decision_id = payload.decision_id or f"decision_{uuid.uuid4().hex}"
        return workflow.resume(
            project_id,
            {
                "action": "answer",
                "answer": payload.answer,
                "finish": payload.finish,
                "decision_id": decision_id,
            },
        )

    @app.post("/api/projects/{project_id}/review")
    def review(project_id: str, payload: ReviewResponse) -> dict[str, Any]:
        decision_id = payload.decision_id or f"decision_{uuid.uuid4().hex}"
        return workflow.resume(
            project_id,
            {
                "action": payload.action,
                "edited_text": payload.edited_text,
                "decision_id": decision_id,
            },
        )

    @app.post("/api/projects/{project_id}/revoke")
    def revoke(project_id: str, payload: RevokeRequest) -> dict[str, Any]:
        return workflow.revoke(project_id, payload.reason)

    @app.exception_handler(ProjectNotFoundError)
    async def not_found_handler(_request, exc: ProjectNotFoundError):
        return _json_error(404, f"找不到项目：{exc.args[0]}")

    @app.exception_handler(ConsentRevokedError)
    async def revoked_handler(_request, exc: ConsentRevokedError):
        return _json_error(409, str(exc))

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request, exc: HTTPException):
        return _json_error(exc.status_code, str(exc.detail))

    return app


def _json_error(status_code: int, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": message})

