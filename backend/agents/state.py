"""LangGraph 状态定义：多智能体协同的共享契约。

设计要点：
- 所有事实性内容（Claim）必须携带 quote + turn_id，这是证据链的根
- claim_ids 用稳定去重 reducer，因为证据抽取是 Send 并行 fan-out 的
- errors 用累加 reducer，任一并行分支的失败都不会被覆盖
- 七要素缺失即 null 并进入 missing_fields，禁止推测
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

Stage = Literal[
    "interview",  # 采访与追问
    "evidence",  # 证据抽取与合并
    "writing",  # 起草与证据审计
    "review",  # 人工确认
    "creative",  # 交付物
    "delivered",
    "revoked",
]

SEVEN_ELEMENTS = ("time", "place", "people", "event", "result", "impact", "feeling")

ELEMENT_LABELS = {
    "time": "时间",
    "place": "地点",
    "people": "人物",
    "event": "事件",
    "result": "结果",
    "impact": "影响",
    "feeling": "感受",
}

# 优先级：先问更容易回答、更能撑住叙事的要素
ELEMENT_PRIORITY = ("event", "time", "place", "people", "result", "impact", "feeling")


def merge_unique(left: list[str] | None, right: list[str] | None) -> list[str]:
    """Send 并行分支的稳定去重 reducer：保持首次出现顺序。"""
    return list(dict.fromkeys([*(left or []), *(right or [])]))


def _merge_by_key(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """按 key_fields 组合去重的合并，后到的覆盖同键（保持稳定顺序）。

    为什么必须幂等：LangGraph 中父子图共用带 reducer 的状态键时，
    子图的输出会被父图**再次应用一遍 reducer**（见 langgraph#4007），
    非幂等的 operator.add 会让同一批数据成倍增长。
    这里改成按 id 覆盖，无论重放多少次结果都一致。
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in [*(left or []), *(right or [])]:
        if not isinstance(item, dict):
            continue
        key = "|".join(str(item.get(field, "")) for field in key_fields)
        if key not in merged:
            order.append(key)
        merged[key] = item
    return [merged[key] for key in order]


def merge_turns(
    left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """采访轮次：按 turn id 去重，幂等。"""
    return _merge_by_key(left, right, ("id",))


def merge_claims(
    left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Claim 合并：按 id 去重，幂等。"""
    return _merge_by_key(left, right, ("id",))


def merge_trace(
    left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """智能体轨迹：按 node+seq 去重，幂等（seq 由 _trace 生成）。"""
    return _merge_by_key(left, right, ("node", "seq"))


def merge_recording_refs(
    left: list[dict[str, Any]] | None, right: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """每轮只绑定一段原声，重放 checkpoint 时不重复。"""
    return _merge_by_key(left, right, ("turn_id",))


class Claim(TypedDict, total=False):
    """一条从讲述中抽取的事实。

    quote 必须是讲述原文中的**原样子串**，turn_id 指向它来自哪一轮；
    两者构成证据链的根，校验不过的内容不允许进入草稿。
    """

    id: str
    element: str  # time/place/people/event/result/impact/feeling
    text: str  # 归一化后的表述（用于成文）
    quote: str  # 原文片段（证据）
    turn_id: str
    confidence: float


class EvidenceTask(TypedDict):
    """Send fan-out 的输入：一轮讲述的抽取任务。"""

    session_id: str
    family_id: str
    consent_version: int
    turn_id: str
    transcript: str


class AuditFinding(TypedDict, total=False):
    sentence_id: str
    kind: str  # unsupported / invalid_citation / quote_mismatch
    message: str
    excerpt: str


class DraftSentence(TypedDict, total=False):
    """草稿中的一句话。

    must_cite=True 的句子必须带引用（additive 句），否则审计判为无证据。
    transition 类句子（过渡、标题、来源说明）不需要引用。
    """

    id: str
    text: str
    claim_ids: list[str]
    must_cite: bool


class MemoryBankState(TypedDict, total=False):
    # --- 会话与授权 ---
    session_id: str
    family_id: str
    actor_id: str
    subject_name: str
    topic: str
    consent_version: int
    consent_ok: bool
    stop_requested: bool
    stop_reason: str | None
    duration_ms: int

    # --- 阶段控制 ---
    stage: Stage
    round_index: int
    max_rounds: int
    next_action: str | None
    review_action: str | None
    target_element: str | None
    closing: str | None

    # --- 采访导演 ---
    current_question: str
    asked_questions: Annotated[list[str], merge_unique]

    # --- 讲述与证据 ---
    # 注意：turns / claims / claim_ids / agent_trace 都必须用**幂等** reducer，
    # 因为图在多次 resume 之间会重复应用 reducer（langgraph#4007 类问题）
    turns: Annotated[list[dict[str, Any]], merge_turns]
    claims: Annotated[list[dict[str, Any]], merge_claims]
    claim_ids: Annotated[list[str], merge_unique]
    missing_fields: list[str]
    conflicts: list[dict[str, Any]]
    recording_refs: Annotated[list[dict[str, Any]], merge_recording_refs]

    # --- 写作与审计 ---
    draft_sentences: list[dict[str, Any]]
    draft_text: str
    pending_text: str | None
    audit_findings: list[dict[str, Any]]
    audit_passed: bool

    # --- 人工确认 ---
    decision: dict[str, Any]
    approved_text: str | None
    delivery: str | None

    # --- 运行信息 ---
    agent_trace: Annotated[list[dict[str, Any]], merge_trace]
    errors: Annotated[list[dict[str, Any]], operator.add]


__all__ = [
    "AuditFinding",
    "Claim",
    "DraftSentence",
    "ELEMENT_LABELS",
    "ELEMENT_PRIORITY",
    "EvidenceTask",
    "MemoryBankState",
    "SEVEN_ELEMENTS",
    "Stage",
    "merge_claims",
    "merge_recording_refs",
    "merge_unique",
]
