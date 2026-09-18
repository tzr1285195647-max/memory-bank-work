"""Slow Agent operations use durable local tasks and short HTTP polls.

The existing synchronous handlers remain the single source of business rules.
Only whitelisted operations can be queued. No HTTP loopback or saved bearer tokens.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from . import api, service
from .database import AgentTask, SessionLocal
from .routes import agent
from .schemas import MemoryFragmentConfirmRequest, MemoryFragmentOut, MemoryFragmentPatchRequest, RecordingOut, StoryOut, TranscriptionOut

router = APIRouter(prefix="/api/agent/tasks", tags=["agent-tasks"])
LOGGER = logging.getLogger("memory_bank.agent_tasks")


class EmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    method: Literal["GET", "POST", "PUT", "PATCH"]
    path: str = Field(max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskOut(BaseModel):
    taskId: str
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    result: dict[str, Any] | None = None
    errorStatus: int | None = None
    error: str | None = None


def _operation(method: str, path: str):
    # Resolve handlers at call time, including their original request/response schemas.
    routes = [
        ("POST", r"/api/agent/interviews", agent.start, agent.StartRequest, None),
        ("POST", r"/api/agent/interviews/answers", agent.answer, agent.AnswerRequest, None),
        ("POST", r"/api/agent/interviews/stop", agent.stop, agent.StopRequest, None),
        ("POST", r"/api/agent/interviews/review", agent.review, agent.ReviewRequest, None),
        ("POST", r"/api/agent/fragments/generate", agent.generate_from_fragments, agent.FragmentGenerationRequest, StoryOut),
        ("POST", r"/api/agent/stories/(?P<story_id>[^/]{1,64})/review", agent.review_existing, agent.StoryReviewRequest, StoryOut),
        ("GET", r"/api/recordings/(?P<recording_id>[^/]{1,64})/transcription", api.get_recording_transcription, EmptyPayload, TranscriptionOut),
        ("PUT", r"/api/recordings/(?P<recording_id>[^/]{1,64})/fragment", api.confirm_recording_fragment, MemoryFragmentConfirmRequest, RecordingOut),
        ("PATCH", r"/api/fragments/(?P<recording_id>[^/]{1,64})", api.patch_memory_fragment, MemoryFragmentPatchRequest, MemoryFragmentOut),
    ]
    for verb, pattern, handler, schema, output in routes:
        match = re.fullmatch(pattern, path)
        if verb == method and match:
            return handler, schema, output, match.groupdict()
    raise HTTPException(422, "该操作不支持后台处理")


def _view(task: AgentTask) -> dict:
    return {
        "taskId": task.id, "status": task.status,
        "result": json.loads(task.result_json) if task.status == "succeeded" and task.result_json else None,
        "errorStatus": task.error_status, "error": task.error_detail,
    }


def _dispatch(task: AgentTask, session, auth: api.AuthContext) -> dict:
    handler, schema, output, kwargs = _operation(task.method, task.path)
    payload = schema.model_validate_json(task.payload_json)
    if getattr(payload, "sessionId", None):
        from .agents import SessionNotFoundError
        try:
            view = agent._runtime().view(payload.sessionId)
        except SessionNotFoundError as exc:
            raise HTTPException(404, "采访会话不存在") from exc
        if view.get("family_id") != auth.family_id:
            raise HTTPException(404, "采访会话不存在")
        if (task.path.endswith(("/answers", "/stop")) or getattr(payload, "action", None) == "request_more"):
            if view.get("actor_id") != auth.user_id:
                raise HTTPException(403, "只有原讲述者可继续或结束这次采访")
    if schema is not EmptyPayload:
        kwargs["payload"] = payload
    result = handler(session=session, auth=auth, **kwargs)
    return output.model_validate(result).model_dump(mode="json") if output else result


class TaskRunner:
    """Single local worker keeps shared Agent provider calls ordered; reads stay responsive."""
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-agent")
        self.lock = threading.Lock()
        self.scheduled: set[str] = set()

    def recover(self):
        with SessionLocal() as session:
            # A process may have committed some work before dying. Do not replay writes.
            session.execute(update(AgentTask).where(AgentTask.status == "running").values(
                status="interrupted", error_status=409,
                error_detail="后端重启中断了本次处理。已保存的录音和碎片仍在，请先刷新故事与碎片列表，再继续操作。",
            ))
            queued = list(session.scalars(select(AgentTask.id).where(AgentTask.status == "queued")))
            session.commit()
        for task_id in queued:
            self.schedule(task_id)

    def schedule(self, task_id: str):
        with self.lock:
            if task_id in self.scheduled:
                return
            self.scheduled.add(task_id)
            self.pool.submit(self._run, task_id)

    def close(self):
        self.pool.shutdown(wait=True)

    def _run(self, task_id: str):
        try:
            with SessionLocal() as session:
                claimed = session.execute(update(AgentTask).where(
                    AgentTask.id == task_id, AgentTask.status == "queued",
                ).values(status="running"))
                session.commit()
                if not claimed.rowcount:
                    return
                task = session.get(AgentTask, task_id)
                try:
                    member = service.require_family_member(session, task.family_id, task.user_id)
                    service.active_consent(session, task.family_id)
                    auth = api.AuthContext(task.user_id, task.family_id, member.role)
                    result = _dispatch(task, session, auth)
                    task.result_json = json.dumps(result, ensure_ascii=False)
                    task.status = "succeeded"
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    task = session.get(AgentTask, task_id)
                    task.status = "failed"
                    task.error_status = exc.status_code if isinstance(exc, HTTPException) else 500
                    task.error_detail = str(exc.detail) if isinstance(exc, HTTPException) else "处理暂时失败，已保存的内容不会丢失，请稍后重试。"
                    session.commit()
                    LOGGER.warning("task_failed id=%s type=%s", task_id, type(exc).__name__)
        finally:
            with self.lock:
                self.scheduled.discard(task_id)


@router.post("", response_model=TaskOut, status_code=202)
def submit_task(payload: TaskRequest, request: Request, session: api.SessionDep, auth: api.AuthDep):
    service.require_family_member(session, auth.family_id, auth.user_id)
    service.active_consent(session, auth.family_id)
    _, schema, _, _ = _operation(payload.method, payload.path)
    try:
        normalized = schema.model_validate(payload.payload).model_dump(mode="json")
    except ValidationError as exc:
        raise HTTPException(422, "任务参数不符合接口要求") from exc
    body = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    query = select(AgentTask).filter_by(family_id=auth.family_id, user_id=auth.user_id, request_key=payload.requestKey)
    task = session.scalar(query)
    if task is None:
        active = session.scalar(select(func.count()).select_from(AgentTask).where(AgentTask.status.in_(["queued", "running"])))
        if active >= 32:
            raise HTTPException(429, "正在处理的任务较多，请稍后重试")
        task = AgentTask(id=str(uuid.uuid4()), family_id=auth.family_id, user_id=auth.user_id,
                         request_key=payload.requestKey, method=payload.method, path=payload.path, payload_json=body)
        session.add(task)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            task = session.scalar(query)
    if task is None:
        raise HTTPException(409, "任务提交冲突，请稍后重试")
    if (task.method, task.path, task.payload_json) != (payload.method, payload.path, body):
        raise HTTPException(409, "请求编号已用于其他内容，请重新提交")
    result = _view(task)
    if task.status == "queued":
        request.app.state.agent_tasks.schedule(task.id)
    return result


@router.get("/{task_id}", response_model=TaskOut)
def task_status(task_id: str, session: api.SessionDep, auth: api.AuthDep):
    service.require_family_member(session, auth.family_id, auth.user_id)
    service.active_consent(session, auth.family_id)
    task = session.scalar(select(AgentTask).filter_by(id=task_id, family_id=auth.family_id, user_id=auth.user_id))
    if task is None:
        raise HTTPException(404, "任务不存在")
    return _view(task)
