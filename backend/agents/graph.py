"""LangGraph 图：五类智能体在父图内协同（扁平结构）。

为什么不用"父图套子图"：嵌套子图里的 interrupt 无法被外部 Command(resume) 恢复
（实测 resume 后状态不变、图仍停在原中断）。因此把五类智能体定义为**父图节点**，
配合 Send 实现并行、用顶层 interrupt 实现采访与人工确认两处暂停。

    父图
      load_context → consent_gate ─(revoked)───────────────→ revoke_and_delete
                          │(allowed)
                          ↓
      interview.select_question ──(有下一个问题)──┐
             ↑                                   │
             │                            interview.commit_turn（interrupt 等回答）
             │                                   │
             │                            evidence.fan_out（Send 并行）
             │                                   ↓
             │                     evidence.extract_claims × N（每轮一个分支）
             │                                   ↓
             │                        evidence.merge（合并 + 冲突检测）
             │                                   ↓
             │                            writing.draft（只用证据成文）
             │                                   ↓
             │                          writing.audit（逐句查证据）
             │                    ┌──(不通过)─────┘        │(通过)
             └────────────────────┘                       ↓
                                            review.decide（interrupt 等人工确认）
                                        ┌───────┬──────────┬─────────┐
                                    approve   edit    request_more  reject
                                        ↓       ↓          ↓          ↓
                                    creative  audit     interview    END
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from . import state as st
from .provider import AgentProvider


def _trace(node: str, **payload: Any) -> dict[str, Any]:
    """智能体调用轨迹：带唯一 seq，用幂等 reducer 合并。"""
    return {"agent_trace": [{"node": node, "seq": uuid.uuid4().hex[:8], **payload}]}


def build_parent_graph(provider: AgentProvider, checkpointer: SqliteSaver):
    # ------------------------------------------------------------ 前置

    def load_context(state: st.MemoryBankState) -> dict[str, Any]:
        return _trace("load_context", session_id=state.get("session_id"))

    def consent_gate(state: st.MemoryBankState) -> dict[str, Any]:
        """授权闸门：撤回或版本不符时改道删除，不进入任何写路径。"""
        allowed = bool(state.get("consent_ok", True))
        return {
            "next_action": "allowed" if allowed else "revoked",
            **_trace("consent_gate", allowed=allowed, consent_version=state.get("consent_version")),
        }

    def route_consent(state: st.MemoryBankState) -> Literal["allowed", "revoked"]:
        return "revoked" if state.get("next_action") == "revoked" else "allowed"

    def revoke_and_delete(state: st.MemoryBankState) -> dict[str, Any]:
        return {
            "consent_ok": False,
            "delivery": None,
            "draft_text": "",
            "next_action": "revoked",
            **_trace("revoke_and_delete"),
        }

    # ------------------------------------------------------------ 采访导演

    def interview_select_question(state: st.MemoryBankState) -> dict[str, Any]:
        """采访导演智能体：决定下一个问题，或识别停止意愿。"""
        previous = [turn.get("answer", "") for turn in state.get("turns", [])]
        decision = provider.choose_question(
            subject_name=state.get("subject_name", "讲述者"),
            topic=state.get("topic", ""),
            round_index=state.get("round_index", 0),
            asked_questions=list(state.get("asked_questions", [])),
            previous_answers=previous,
            missing_fields=list(state.get("missing_fields", [])),
        )

        if decision.get("should_stop"):
            return {
                "current_question": "",
                "next_action": "finish_without_question",
                "stop_requested": True,
                "stop_reason": decision.get("stop_reason"),
                "closing": decision.get("closing") or "好，今天就到这儿。",
                **_trace("interview.select_question", should_stop=True, reason=decision.get("stop_reason")),
            }

        question = decision.get("question")
        return {
            "current_question": question or "",
            "next_action": "ask" if question else "no_more_questions",
            "asked_questions": [question] if question else [],
            "target_element": decision.get("target_element"),
            **_trace(
                "interview.select_question",
                round=state.get("round_index", 0),
                target_element=decision.get("target_element"),
                question=question,
            ),
        }

    def interview_commit_turn(state: st.MemoryBankState) -> dict[str, Any]:
        """等待讲述者回答（顶层 interrupt），提交后进入证据抽取。"""
        payload = interrupt(
            {
                "kind": "interview",
                "title": f"第 {state.get('round_index', 0) + 1} 轮采访",
                "question": state.get("current_question", ""),
                "targetElement": state.get("target_element"),
                "hint": "一次只问一个问题；回答会成为可追溯的会话事件。",
            }
        )
        answer = str((payload or {}).get("answer", "")).strip()
        finish = bool((payload or {}).get("finish"))
        turn = {
            "id": f"turn-{uuid.uuid4().hex[:10]}",
            "round_index": state.get("round_index", 0),
            "question": state.get("current_question", ""),
            "answer": answer,
            "finish_requested": finish,
        }
        return {
            "turns": [turn],
            "round_index": state.get("round_index", 0) + 1,
            "next_action": "evidence",
            **_trace("interview.commit_turn", turn_id=turn["id"], length=len(answer), finish=finish),
        }

    # ------------------------------------------------------------ 证据抽取

    def evidence_prepare(state: st.MemoryBankState) -> dict[str, Any]:
        """准备证据抽取：只记录待抽取轮次，不修改 turns。"""
        pending = [turn for turn in state.get("turns", []) if turn.get("answer")]
        return _trace("evidence.prepare", pending_turns=len(pending))

    def route_evidence(state: st.MemoryBankState):
        """并行 fan-out 路由：每个未抽取的轮次一个 Send 分支。

        注意：Send 只能由**路由函数**返回；如果作为普通节点返回 Send 列表，
        LangGraph 会抛 InvalidUpdateError。Send 的输入必须是增量状态。
        """
        already = {claim.get("turn_id") for claim in state.get("claims", [])}
        targets = [
            turn
            for turn in state.get("turns", [])
            if turn.get("answer") and turn.get("id") not in already
        ]
        if not targets:
            return "evidence_merge"
        return [
            Send(
                "evidence_extract_claims",
                {
                    "session_id": state.get("session_id", ""),
                    "family_id": state.get("family_id", ""),
                    "consent_version": state.get("consent_version", 1),
                    "turn_id": turn["id"],
                    "transcript": turn["answer"],
                },
            )
            for turn in targets
        ]

    def evidence_extract_claims(task: st.EvidenceTask) -> dict[str, Any]:
        """证据抽取智能体：每条事实都带原文片段与来源轮次。"""
        result = provider.extract_claims(transcript=task["transcript"], turn_id=task["turn_id"])
        claims = [
            {
                "id": f"claim-{uuid.uuid4().hex[:10]}",
                "element": item["element"],
                "text": item["text"],
                "quote": item["quote"],
                "turn_id": task["turn_id"],
                "confidence": item.get("confidence", 0.6),
                "order": index,
            }
            for index, item in enumerate(result.get("claims", []))
        ]
        return {
            "claims": claims,
            "claim_ids": [claim["id"] for claim in claims],
            **_trace("evidence.extract_claims", turn_id=task["turn_id"], claims=len(claims)),
        }

    def evidence_merge(state: st.MemoryBankState) -> dict[str, Any]:
        """合并证据 + 冲突检测：缺失要素保持缺失，矛盾讲述并列不裁定。"""
        claims = state.get("claims", [])
        covered = {str(claim.get("element")) for claim in claims}
        missing = [element for element in st.SEVEN_ELEMENTS if element not in covered]
        conflicts = provider.resolve_conflicts(claims=claims)
        return {
            "missing_fields": missing,
            "conflicts": conflicts,
            "next_action": "draft",
            **_trace("evidence.merge", total=len(claims), missing=missing, conflicts=len(conflicts)),
        }

    # ------------------------------------------------------------ 写作与审计

    def writing_draft(state: st.MemoryBankState) -> dict[str, Any]:
        """写作智能体：只用已抽取的证据成文，每条事实句挂 claim id。"""
        result = provider.compose_draft(
            subject_name=state.get("subject_name", "讲述者"),
            topic=state.get("topic", ""),
            claims=state.get("claims", []),
        )
        session_id = state.get("session_id", "session")
        sentences = [
            {
                "id": f"{session_id[:8]}-s{index:02d}",
                "text": item.get("text", ""),
                "claim_ids": list(item.get("claim_ids") or []),
                "must_cite": bool(item.get("must_cite", True)),
            }
            for index, item in enumerate(result.get("sentences", []))
        ]
        return {
            "draft_sentences": sentences,
            "draft_text": "\n".join(sentence["text"] for sentence in sentences),
            **_trace("writing.draft", sentences=len(sentences), claims=len(state.get("claims", []))),
        }

    def writing_audit(state: st.MemoryBankState) -> dict[str, Any]:
        """写作审计智能体：逐句校验引用是否存在、证据是否可追溯。"""
        findings = provider.audit_draft(
            sentences=state.get("draft_sentences", []),
            claims=state.get("claims", []),
        )
        return {
            "audit_findings": findings,
            "audit_passed": not findings,
            "next_action": "review" if not findings else "needs_more_evidence",
            **_trace("writing.audit", findings=len(findings)),
        }

    # ------------------------------------------------------------ 人工确认

    def review_decide(state: st.MemoryBankState) -> dict[str, Any]:
        """人工确认点（顶层 interrupt）：产品规则要求发布权在人手里。"""
        payload = interrupt(
            {
                "kind": "review",
                "title": "确认这段文字是否准确",
                "draft": {"content": state.get("draft_text", "")},
                "findings": state.get("audit_findings", []),
                "conflicts": state.get("conflicts", []),
                "hint": "修改措辞可以；删掉证据标记的事实会被退回重审。",
            }
        )
        decision = dict(payload or {})
        action = decision.get("action", "approve")
        patch: dict[str, Any] = {"review_action": action, **_trace("review.decision", action=action)}
        if action == "edit" and decision.get("edited_text") is not None:
            patch["draft_text"] = decision["edited_text"]
        return patch

    def review_apply(state: st.MemoryBankState) -> dict[str, Any]:
        action = state.get("review_action", "approve")
        if action == "reject":
            return {"next_action": "rejected", **_trace("review.apply", action=action)}
        if action == "request_more":
            return {"next_action": "request_more", **_trace("review.apply", action=action)}
        if action == "edit":
            # 人工改写后必须重新审计：删掉证据标记的事实需要补录
            return {"next_action": "reaudit", **_trace("review.apply", action=action)}
        return {
            "approved_text": state.get("draft_text", ""),
            "next_action": "approved",
            **_trace("review.apply", action="approve"),
        }

    def route_after_review(state: st.MemoryBankState) -> str:
        action = state.get("next_action", "approved")
        if action == "reaudit":
            return "audit"
        if action == "request_more":
            return "select_question"
        if action == "rejected":
            return "end"
        return "creative"

    # ------------------------------------------------------------ 交付

    def creative_delivery(state: st.MemoryBankState) -> dict[str, Any]:
        approved = (state.get("approved_text") or state.get("draft_text") or "").strip()
        name = state.get("subject_name", "讲述者")
        if not approved or not state.get("claims"):
            # 老人主动停止且没有可成文内容：正常收尾，不产出交付物
            return {"next_action": "stopped", **_trace("creative.stopped", reason="no_claims")}
        delivery = f"{approved}\n\n——\n这段记忆由{name}的口述证据生成，并经过人工确认。"
        return {
            "delivery": delivery,
            "next_action": "delivered",
            **_trace("creative.delivery", length=len(delivery)),
        }

    # ------------------------------------------------------------ 路由

    def route_entry(state: st.MemoryBankState) -> str:
        """入口分流：撤回走删除；有意愿停止按有无内容决定；否则进入采访。"""
        if not state.get("consent_ok", True):
            return "revoked"
        if state.get("stop_requested"):
            return "creative" if not state.get("claims") else "review"
        if state.get("next_action") == "approved":
            return "creative"
        if state.get("next_action") == "reaudit":
            return "audit"
        if state.get("next_action") == "request_more":
            return "select_question"
        return "select_question"

    def route_after_question(state: st.MemoryBankState) -> str:
        action = state.get("next_action")
        if action == "finish_without_question":
            return "creative" if not state.get("claims") else "review"
        if action == "no_more_questions":
            return "fan_out"
        return "commit_turn"

    def route_after_audit(state: st.MemoryBankState) -> str:
        return "review" if state.get("audit_passed", True) else "select_question"

    def route_after_merge(state: st.MemoryBankState) -> Literal["interview", "draft"]:
        """采访循环：还没讲完就继续追问，讲完了才成文。

        结束条件：讲述者表示 finish、达到轮次上限、七要素齐全、或问题已问尽。
        这是"一次只讲一个小故事、慢慢追问"的实现位置。
        """
        turns = state.get("turns", [])
        last = turns[-1] if turns else {}
        if last.get("finish_requested"):
            return "draft"
        if state.get("round_index", 0) >= state.get("max_rounds", 3):
            return "draft"
        if state.get("stop_requested"):
            return "draft"
        if not state.get("missing_fields"):
            return "draft"
        if state.get("next_action") == "no_more_questions":
            return "draft"
        return "interview"

    # ------------------------------------------------------------ 组装

    builder = StateGraph(st.MemoryBankState)
    builder.add_node("load_context", load_context)
    builder.add_node("consent_gate", consent_gate)
    builder.add_node("interview_select_question", interview_select_question)
    builder.add_node("interview_commit_turn", interview_commit_turn)
    builder.add_node("evidence_prepare", evidence_prepare)
    builder.add_node("evidence_extract_claims", evidence_extract_claims, input_schema=st.EvidenceTask)
    builder.add_node("evidence_merge", evidence_merge)
    builder.add_node("writing_draft", writing_draft)
    builder.add_node("writing_audit", writing_audit)
    builder.add_node("review_decide", review_decide)
    builder.add_node("review_apply", review_apply)
    builder.add_node("creative_delivery", creative_delivery)
    builder.add_node("revoke_and_delete", revoke_and_delete)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "consent_gate")
    builder.add_conditional_edges(
        "consent_gate", route_consent, {"allowed": "interview_select_question", "revoked": "revoke_and_delete"}
    )
    builder.add_conditional_edges(
        "interview_select_question",
        route_after_question,
        {
            "commit_turn": "interview_commit_turn",
            "fan_out": "evidence_prepare",
            "review": "review_decide",
            "creative": "creative_delivery",
        },
    )
    builder.add_edge("interview_commit_turn", "evidence_prepare")
    builder.add_conditional_edges(
        "evidence_prepare",
        route_evidence,
        ["evidence_extract_claims", "evidence_merge"],
    )
    builder.add_edge("evidence_extract_claims", "evidence_merge")
    builder.add_conditional_edges(
        "evidence_merge",
        route_after_merge,
        {"interview": "interview_select_question", "draft": "writing_draft"},
    )
    builder.add_edge("writing_draft", "writing_audit")
    builder.add_conditional_edges(
        "writing_audit",
        route_after_audit,
        {"review": "review_decide", "select_question": "interview_select_question"},
    )
    builder.add_edge("review_decide", "review_apply")
    builder.add_conditional_edges(
        "review_apply",
        route_after_review,
        {
            "creative": "creative_delivery",
            "audit": "writing_audit",
            "select_question": "interview_select_question",
            "end": END,
        },
    )
    builder.add_edge("creative_delivery", END)
    builder.add_edge("revoke_and_delete", END)

    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_parent_graph", "Command"]
