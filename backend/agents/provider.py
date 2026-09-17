"""五类智能体的能力契约（端口）。

架构约束：智能体只依赖这个 Protocol，不依赖任何模型 SDK；
换用真模型时只替换实现（见 mock.py / llm.py），图与业务层不动。

五类智能体：
  InterviewDirector  采访导演：选下一个问题，尊重停止意愿
  EvidenceExtractor  证据抽取：七要素，每条必须回指原句
  WritingAgent       写作：只用已确认事实，不得新增
  WritingAuditor     写作审计：逐句检查是否有证据支撑
  ConflictResolver   冲突处理：并列矛盾讲述，不裁定
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


class QuestionDecision(TypedDict, total=False):
    should_stop: bool
    stop_reason: str | None
    question: str | None
    target_element: str | None
    closing: str | None


class ExtractedClaim(TypedDict, total=False):
    element: str
    text: str
    quote: str
    confidence: float


class ExtractResult(TypedDict, total=False):
    claims: list[ExtractedClaim]
    missing_fields: list[str]


class DraftSentenceDraft(TypedDict, total=False):
    text: str
    claim_ids: list[str]
    must_cite: bool


class DraftResult(TypedDict, total=False):
    title: str
    sentences: list[DraftSentenceDraft]


class ConflictGroup(TypedDict, total=False):
    element: str
    quote_a: str
    turn_a: str
    quote_b: str
    turn_b: str
    note: str


@runtime_checkable
class AgentProvider(Protocol):
    """智能体提供方。演示用 Mock 确定性实现；生产替换为真实模型适配器。"""

    name: str

    def clean_transcript(self, *, asr_raw_text: str, narrator_name: str, topic: str) -> dict[str, Any]:
        """口述校对：保留原始事实，只返回可供用户修改和确认的建议。"""
        ...

    def choose_question(
        self,
        *,
        subject_name: str,
        topic: str,
        round_index: int,
        asked_questions: list[str],
        previous_answers: list[str],
        missing_fields: list[str],
        confirmed_fragments: list[str] | None = None,
        confirmed_facts: list[dict[str, Any]] | None = None,
        minimum_fragments: int = 7,
    ) -> QuestionDecision:
        """采访导演：返回至多一个问题；老人明确表示停止时必须 should_stop。"""
        ...

    def extract_claims(self, *, transcript: str, turn_id: str) -> ExtractResult:
        """证据抽取：只从 transcript 中找证据，找不到就留空，禁止推测。"""
        ...

    def compose_draft(
        self,
        *,
        subject_name: str,
        topic: str,
        claims: list[dict[str, Any]],
        style: str = "raw",
    ) -> DraftResult:
        """写作：只能用给定 claims 里的事实，每条事实句必须引用 claim id。"""
        ...

    def audit_draft(
        self,
        *,
        sentences: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """写作审计：逐句校验引用是否存在、quote 是否真的在原文里。"""
        ...

    def resolve_conflicts(self, *, claims: list[dict[str, Any]]) -> list[ConflictGroup]:
        """冲突处理：同一要素出现互相矛盾的讲述时并列，不裁定谁对。"""
        ...


__all__ = [
    "AgentProvider",
    "ConflictGroup",
    "DraftResult",
    "DraftSentenceDraft",
    "ExtractResult",
    "ExtractedClaim",
    "QuestionDecision",
]
