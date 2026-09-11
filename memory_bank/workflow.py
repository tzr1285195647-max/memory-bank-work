from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from .agents import MockIntelligence
from .schemas import EvidenceTask, MemoryBankState
from .storage import ConsentRevokedError, MemoryStore


class MemoryBankWorkflow:
    """父图编排五个隔离子图；子图默认使用 per-invocation 持久化。"""

    def __init__(self, store: MemoryStore, checkpoint_path: str | Path):
        self.store = store
        self.ai = MockIntelligence()
        self._checkpoint_conn = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
        self.checkpointer = SqliteSaver(self._checkpoint_conn)
        self.checkpointer.setup()
        self.graph = self._build_parent_graph()

    def _build_parent_graph(self):
        interview = self._build_interview_graph()
        evidence = self._build_evidence_graph()
        writing = self._build_writing_graph()
        review = self._build_review_graph()
        creative = self._build_creative_graph()

        builder = StateGraph(MemoryBankState)
        builder.add_node("load_context", self._load_context)
        builder.add_node("consent_gate", self._consent_gate)
        builder.add_node("interview", interview)
        builder.add_node("evidence", evidence)
        builder.add_node("writing", writing)
        builder.add_node("review", review)
        builder.add_node("creative", creative)
        builder.add_node("revoke_and_delete", self._revoke_and_delete)
        builder.add_node("mark_rejected", self._mark_rejected)

        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "consent_gate")
        builder.add_conditional_edges(
            "consent_gate",
            self._route_consent,
            {"allowed": "interview", "revoked": "revoke_and_delete", "denied": END},
        )
        builder.add_conditional_edges(
            "interview",
            self._route_interview_result,
            {"finish": "evidence", "revoked": "revoke_and_delete"},
        )
        builder.add_edge("evidence", "writing")
        builder.add_conditional_edges(
            "writing",
            self._route_writing_result,
            {"pass": "review", "needs_more": "interview"},
        )
        builder.add_conditional_edges(
            "review",
            self._route_review_result,
            {
                "approve": "creative",
                "edit": "writing",
                "request_more": "interview",
                "reject": "mark_rejected",
                "revoked": "revoke_and_delete",
            },
        )
        builder.add_edge("creative", END)
        builder.add_edge("revoke_and_delete", END)
        builder.add_edge("mark_rejected", END)
        return builder.compile(checkpointer=self.checkpointer)

    def _build_interview_graph(self):
        builder = StateGraph(MemoryBankState)
        builder.add_node("select_question", self._select_question)
        builder.add_node("wait_for_answer", self._wait_for_answer)
        builder.add_node("commit_turn", self._commit_turn)
        builder.add_edge(START, "select_question")
        builder.add_edge("select_question", "wait_for_answer")
        builder.add_edge("wait_for_answer", "commit_turn")
        builder.add_conditional_edges(
            "commit_turn",
            self._route_session,
            {"continue": "select_question", "finish": END, "revoked": END},
        )
        return builder.compile()

    def _build_evidence_graph(self):
        builder = StateGraph(MemoryBankState)
        builder.add_node("prepare_evidence", self._prepare_evidence)
        builder.add_node("extract_turn_claims", self._extract_turn_claims, input_schema=EvidenceTask)
        builder.add_node("merge_evidence", self._merge_evidence)
        builder.add_edge(START, "prepare_evidence")
        builder.add_conditional_edges("prepare_evidence", self._fan_out_evidence)
        builder.add_edge("extract_turn_claims", "merge_evidence")
        builder.add_edge("merge_evidence", END)
        return builder.compile()

    def _build_writing_graph(self):
        builder = StateGraph(MemoryBankState)
        builder.add_node("draft_chapter", self._draft_chapter)
        builder.add_node("evidence_audit", self._evidence_audit)
        builder.add_edge(START, "draft_chapter")
        builder.add_edge("draft_chapter", "evidence_audit")
        builder.add_edge("evidence_audit", END)
        return builder.compile()

    def _build_review_graph(self):
        builder = StateGraph(MemoryBankState)
        builder.add_node("review_interrupt", self._review_interrupt)
        builder.add_node("apply_decision", self._apply_review_decision)
        builder.add_edge(START, "review_interrupt")
        builder.add_edge("review_interrupt", "apply_decision")
        builder.add_edge("apply_decision", END)
        return builder.compile()

    def _build_creative_graph(self):
        builder = StateGraph(MemoryBankState)
        builder.add_node("create_memory_card", self._create_memory_card)
        builder.add_node("delivery", self._delivery)
        builder.add_edge(START, "create_memory_card")
        builder.add_edge("create_memory_card", "delivery")
        builder.add_edge("delivery", END)
        return builder.compile()

    def start(self, project: dict[str, Any]) -> dict[str, Any]:
        initial: MemoryBankState = {
            "project_id": project["id"],
            "family_id": project["family_id"],
            "actor_id": project["actor_id"],
            "consent_version": project["consent_version"],
            "stage": "interview",
            "round_index": 0,
            "max_rounds": project["max_rounds"],
            "claim_ids": [],
            "errors": [],
        }
        self.graph.invoke(initial, self._config(project["id"]))
        return self.view(project["id"])

    def resume(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if project["revoked"]:
            raise ConsentRevokedError("项目授权已经撤回")
        self.graph.invoke(Command(resume=payload), self._config(project_id))
        return self.view(project_id)

    def revoke(self, project_id: str, reason: str) -> dict[str, Any]:
        self.store.revoke_and_delete(project_id, reason)
        self.graph.update_state(
            self._config(project_id),
            {"stage": "revoked", "next_action": "revoked", "current_question": ""},
        )
        return self.view(project_id)

    def view(self, project_id: str) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(project_id))
        interruptions: list[dict[str, Any]] = []
        for task in snapshot.tasks:
            for item in task.interrupts:
                interruptions.append({"id": item.id, "value": item.value})
        result = self.store.bundle(project_id)
        result["workflow"] = {
            "next_nodes": list(snapshot.next),
            "interrupts": interruptions,
            "state": {
                "stage": snapshot.values.get("stage"),
                "round_index": snapshot.values.get("round_index", 0),
                "next_action": snapshot.values.get("next_action"),
                "audit_findings": snapshot.values.get("audit_findings", []),
            },
        }
        return result

    @staticmethod
    def _config(project_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": project_id}}

    def _load_context(self, state: MemoryBankState) -> dict[str, Any]:
        project = self.store.get_project(state["project_id"])
        return {
            "family_id": project["family_id"],
            "actor_id": project["actor_id"],
            "consent_version": project["consent_version"],
            "stage": project["stage"],
            "round_index": project["round_index"],
            "max_rounds": project["max_rounds"],
        }

    def _consent_gate(self, state: MemoryBankState) -> dict[str, Any]:
        project = self.store.get_project(state["project_id"])
        return {"next_action": "revoked" if project["revoked"] else "allowed"}

    @staticmethod
    def _route_consent(state: MemoryBankState) -> Literal["allowed", "revoked", "denied"]:
        return state.get("next_action", "denied")  # type: ignore[return-value]

    def _select_question(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.ensure_authorized(state["project_id"], state["consent_version"])
        project = self.store.get_project(state["project_id"])
        turns = self.store.list_turns(state["project_id"])
        question = self.ai.choose_question(
            subject_name=project["subject_name"],
            topic=project["topic"],
            round_index=len(turns),
            previous_answers=[turn["answer"] for turn in turns],
        )
        self.store.update_stage(state["project_id"], "interview", current_question=question)
        self.store.log_event(
            state["project_id"],
            "agent.interview_question_selected",
            {"round": len(turns) + 1, "question": question, "agent": self.ai.name},
        )
        return {
            "stage": "interview",
            "round_index": len(turns),
            "current_question": question,
            "next_action": None,
        }

    @staticmethod
    def _wait_for_answer(state: MemoryBankState) -> dict[str, Any]:
        decision = interrupt(
            {
                "kind": "interview",
                "title": f"第 {state.get('round_index', 0) + 1} 轮采访",
                "question": state["current_question"],
                "actions": ["answer", "finish", "revoke"],
            }
        )
        return {"decision": decision}

    def _commit_turn(self, state: MemoryBankState) -> dict[str, Any]:
        decision = state.get("decision", {})
        action = decision.get("action", "answer")
        if action == "revoke":
            return {"next_action": "revoked"}
        answer = str(decision.get("answer", "")).strip()
        if not answer:
            return {"next_action": "continue"}
        round_index = state.get("round_index", 0) + 1
        operation_id = decision.get("decision_id") or f"answer:{state['project_id']}:{round_index}"
        self.store.save_turn(
            project_id=state["project_id"],
            consent_version=state["consent_version"],
            round_index=round_index,
            question=state["current_question"],
            answer=answer,
            operation_id=operation_id,
        )
        should_finish = bool(decision.get("finish")) or round_index >= state.get("max_rounds", 3)
        return {
            "round_index": round_index,
            "next_action": "finish" if should_finish else "continue",
            "decision": {},
        }

    @staticmethod
    def _route_session(state: MemoryBankState) -> Literal["continue", "finish", "revoked"]:
        return state.get("next_action", "continue")  # type: ignore[return-value]

    @staticmethod
    def _route_interview_result(state: MemoryBankState) -> Literal["finish", "revoked"]:
        return state.get("next_action", "finish")  # type: ignore[return-value]

    def _prepare_evidence(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.ensure_authorized(state["project_id"], state["consent_version"])
        turns = self.store.list_turns(state["project_id"])
        self.store.update_stage(state["project_id"], "evidence")
        return {"stage": "evidence", "turn_ids": [turn["id"] for turn in turns], "next_action": None}

    @staticmethod
    def _fan_out_evidence(state: MemoryBankState):
        return [
            Send(
                "extract_turn_claims",
                {
                    "project_id": state["project_id"],
                    "family_id": state["family_id"],
                    "actor_id": state["actor_id"],
                    "consent_version": state["consent_version"],
                    "turn_id": turn_id,
                    "claim_ids": [],
                },
            )
            for turn_id in state.get("turn_ids", [])
        ]

    def _extract_turn_claims(self, state: EvidenceTask) -> dict[str, Any]:
        turn = self.store.get_turn(state["turn_id"])
        texts = self.ai.extract_claims(turn["answer"])
        ids = self.store.save_claims(
            project_id=state["project_id"],
            turn_id=state["turn_id"],
            consent_version=state["consent_version"],
            claims=texts,
        )
        return {"claim_ids": ids}

    def _merge_evidence(self, state: MemoryBankState) -> dict[str, Any]:
        claims = self.store.list_claims(state["project_id"])
        self.store.log_event(
            state["project_id"],
            "evidence.merge_completed",
            {"claim_count": len(claims), "claim_ids": [claim["id"] for claim in claims]},
        )
        return {"claim_ids": [claim["id"] for claim in claims], "stage": "evidence"}

    def _draft_chapter(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.ensure_authorized(state["project_id"], state["consent_version"])
        project = self.store.get_project(state["project_id"])
        claims = self.store.list_claims(state["project_id"])
        self.store.update_stage(state["project_id"], "writing")
        if state.get("next_action") == "edit":
            edited = self.store.latest_draft(state["project_id"])
            return {"stage": "writing", "draft_id": edited["id"] if edited else None}
        if not claims:
            return {"stage": "writing", "audit_findings": [{"type": "unsupported", "message": "没有可用证据"}], "next_action": "needs_more"}
        content = self.ai.compose_draft(
            subject_name=project["subject_name"], topic=project["topic"], claims=claims
        )
        operation_id = f"auto-draft:{state['project_id']}:{len(self.store.list_turns(state['project_id']))}"
        draft = self.store.save_draft(
            project_id=state["project_id"],
            consent_version=state["consent_version"],
            content=content,
            operation_id=operation_id,
        )
        return {"stage": "writing", "draft_id": draft["id"]}

    def _evidence_audit(self, state: MemoryBankState) -> dict[str, Any]:
        draft = self.store.latest_draft(state["project_id"])
        claims = self.store.list_claims(state["project_id"])
        if draft is None:
            findings = [{"type": "unsupported", "message": "没有待审计草稿"}]
        else:
            findings = self.ai.audit_draft(draft["content"], claims)
        self.store.log_event(
            state["project_id"],
            "agent.evidence_audit_completed",
            {"finding_count": len(findings), "findings": findings, "agent": self.ai.name},
        )
        return {"audit_findings": findings, "next_action": "pass" if not findings else "needs_more"}

    @staticmethod
    def _route_writing_result(state: MemoryBankState) -> Literal["pass", "needs_more"]:
        return state.get("next_action", "needs_more")  # type: ignore[return-value]

    def _review_interrupt(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.ensure_authorized(state["project_id"], state["consent_version"])
        self.store.update_stage(state["project_id"], "review")
        draft = self.store.latest_draft(state["project_id"])
        decision = interrupt(
            {
                "kind": "review",
                "title": "章节等待人工确认",
                "draft": draft,
                "claims": self.store.list_claims(state["project_id"]),
                "actions": ["approve", "edit", "request_more", "reject", "revoke"],
            }
        )
        return {"stage": "review", "decision": decision}

    def _apply_review_decision(self, state: MemoryBankState) -> dict[str, Any]:
        decision = state.get("decision", {})
        action = decision.get("action", "request_more")
        if action == "revoke":
            return {"next_action": "revoked"}
        if action == "reject":
            return {"next_action": "reject"}
        if action == "request_more":
            return {"next_action": "request_more", "decision": {}}
        draft = self.store.latest_draft(state["project_id"])
        if draft is None:
            return {"next_action": "request_more"}
        if action == "edit":
            edited_text = str(decision.get("edited_text", "")).strip()
            if not edited_text:
                return {"next_action": "request_more"}
            operation_id = decision.get("decision_id") or f"edit:{uuid.uuid4().hex}"
            edited = self.store.save_draft(
                project_id=state["project_id"],
                consent_version=state["consent_version"],
                content=edited_text,
                operation_id=operation_id,
            )
            return {"draft_id": edited["id"], "next_action": "edit", "decision": {}}
        self.store.approve_draft(state["project_id"], draft["id"], state["consent_version"])
        return {"draft_id": draft["id"], "next_action": "approve", "decision": {}}

    @staticmethod
    def _route_review_result(
        state: MemoryBankState,
    ) -> Literal["approve", "edit", "request_more", "reject", "revoked"]:
        return state.get("next_action", "request_more")  # type: ignore[return-value]

    def _create_memory_card(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.ensure_authorized(state["project_id"], state["consent_version"])
        project = self.store.get_project(state["project_id"])
        draft = self.store.latest_draft(state["project_id"])
        if draft is None or draft["status"] != "approved":
            raise RuntimeError("只有已批准的版本可以交付")
        self.store.update_stage(state["project_id"], "creative")
        content = self.ai.create_delivery(draft["content"], project["subject_name"])
        delivery = self.store.save_delivery(
            project_id=state["project_id"],
            consent_version=state["consent_version"],
            content=content,
            operation_id=f"delivery:{draft['id']}",
        )
        return {"stage": "creative", "delivery_id": delivery["id"]}

    def _delivery(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.update_stage(state["project_id"], "delivered")
        return {"stage": "delivered", "next_action": "delivered"}

    def _revoke_and_delete(self, state: MemoryBankState) -> dict[str, Any]:
        project = self.store.get_project(state["project_id"])
        if not project["revoked"]:
            self.store.revoke_and_delete(state["project_id"], "工作流中的撤回决定")
        return {"stage": "revoked", "next_action": "revoked"}

    def _mark_rejected(self, state: MemoryBankState) -> dict[str, Any]:
        self.store.mark_rejected(state["project_id"])
        return {"stage": "rejected", "next_action": "rejected"}
