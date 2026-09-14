"""多智能体运行时：驱动图、处理中断与恢复、暴露可视化所需状态。

用法（服务层）：
    runtime = AgentRuntime()                     # 默认 Mock 智能体
    view = runtime.start_session(session_id=..., family_id=..., ...)
    view = runtime.resume(session_id, {"answer": "..."})     # 回答采访问题
    view = runtime.resume(session_id, {"action": "approve"}) # 人工确认
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from .graph import build_parent_graph
from .mock import MockAgentProvider
from .provider import AgentProvider

DEFAULT_MAX_ROUNDS = 3


class SessionNotFoundError(LookupError):
    pass


class AgentRuntime:
    """单机演示用的图运行时。

    SqliteSaver 让中断状态跨进程存活：服务重启后仍可按 session_id 恢复，
    这正是「可恢复」这条产品能力的实现基础。
    """

    def __init__(self, checkpoint_path: Path, provider: AgentProvider | None = None) -> None:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._saver_conn = SqliteSaver.from_conn_string(str(checkpoint_path))
        self.checkpointer = self._saver_conn.__enter__()
        if provider is None:
            # 按配置选择：有 LLM_API_KEY 就用真实模型（失败自动回落），否则纯 Mock
            from .llm import build_provider

            provider = build_provider()
        self.provider = provider
        self.graph = build_parent_graph(self.provider, self.checkpointer)

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "name", type(self.provider).__name__)

    # ------------------------------------------------------------ 生命周期

    def close(self) -> None:
        try:
            self._saver_conn.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - 关闭失败不影响进程退出
            pass

    @staticmethod
    def _config(session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    # ------------------------------------------------------------ 启动会话

    def start_session(
        self,
        *,
        session_id: str,
        family_id: str,
        actor_id: str = "",
        subject_name: str = "讲述者",
        topic: str = "",
        consent_version: int = 1,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        consent_ok: bool = True,
    ) -> dict[str, Any]:
        initial: dict[str, Any] = {
            "session_id": session_id,
            "family_id": family_id,
            "actor_id": actor_id,
            "subject_name": subject_name,
            "topic": topic,
            "consent_version": consent_version,
            "max_rounds": max_rounds,
            "consent_ok": consent_ok,
            "stage": "interview",
            "round_index": 0,
            "turns": [],
            "claims": [],
            "claim_ids": [],
            "missing_fields": list(_seven_elements()),
            "asked_questions": [],
            "agent_trace": [],
            "errors": [],
            "audit_findings": [],
            "duration_ms": 0,
            "recording_refs": [],
        }
        with self._lock:
            self.graph.invoke(initial, self._config(session_id))
            return self.view(session_id)

    # ------------------------------------------------------------ 恢复

    def resume(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from langgraph.types import Command

        with self._lock:
            self.graph.invoke(Command(resume=payload), self._config(session_id))
            return self.view(session_id)

    # ------------------------------------------------------------ 视图

    def view(self, session_id: str) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(session_id))
        if not snapshot or not snapshot.values:
            raise SessionNotFoundError(session_id)
        values = dict(snapshot.values)
        interrupts: list[dict[str, Any]] = []
        for task in snapshot.tasks:
            for item in task.interrupts:
                interrupts.append({"id": item.id, "value": item.value})
        return {
            "session_id": session_id,
            "stage": self._derive_stage(values, interrupts),
            "round_index": values.get("round_index", 0),
            "max_rounds": values.get("max_rounds", DEFAULT_MAX_ROUNDS),
            "duration_ms": values.get("duration_ms", 0),
            "recordings": values.get("recording_refs", []),
            "question": values.get("current_question", ""),
            "turns": values.get("turns", []),
            "claims": values.get("claims", []),
            "missing_fields": values.get("missing_fields", []),
            "draft_text": values.get("draft_text", ""),
            "draft_sentences": values.get("draft_sentences", []),
            "audit_findings": values.get("audit_findings", []),
            "audit_passed": values.get("audit_passed", True),
            "conflicts": values.get("conflicts", []),
            "delivery": values.get("delivery"),
            "stop_requested": bool(values.get("stop_requested")),
            "stop_reason": values.get("stop_reason"),
            "stopped": bool(values.get("stop_requested"))
            and not values.get("delivery"),
            "agent_trace": values.get("agent_trace", []),
            "errors": values.get("errors", []),
            "interrupts": interrupts,
            "next": snapshot.next,
        }

    @staticmethod
    def _derive_stage(values: dict[str, Any], interrupts: list[dict[str, Any]]) -> str:
        """从状态信号推导当前阶段。

        为什么不直接读 state["stage"]：子图与父图共享同一份状态 schema 时，
        子图回写会把父图的 stage 一起带回并覆盖父图的判断（实测 revoked
        会被覆盖成 interview）。因此以「谁挂起了中断、产出了什么」为准。
        """
        if not values.get("consent_ok", True):
            return "revoked"
        if values.get("delivery"):
            return "delivered"
        if values.get("next_action") == "rejected":
            return "rejected"
        if values.get("next_action") == "stopped":
            return "rejected"
        pending = [item["value"].get("kind") for item in interrupts]
        if "review" in pending:
            return "review"
        if "interview" in pending:
            return "interview"
        if values.get("draft_text") and values.get("audit_passed"):
            return "review"
        if values.get("claims"):
            return "writing"
        return "interview"


def _seven_elements() -> tuple[str, ...]:
    from .state import SEVEN_ELEMENTS

    return SEVEN_ELEMENTS


__all__ = ["AgentRuntime", "SessionNotFoundError"]
