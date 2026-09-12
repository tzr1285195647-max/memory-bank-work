"""LLM 适配器测试：验证「模型输出不可信」的四道校验与降级策略。

测试不联网：用桩替换 `_chat`，直接喂各种不合规的模型输出。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.agents.llm import (  # noqa: E402
    FallbackAgentProvider,
    LLMAgentProvider,
    LLMUnavailableError,
)
from backend.agents.mock import MockAgentProvider  # noqa: E402
from backend.agents.state import SEVEN_ELEMENTS  # noqa: E402

TRANSCRIPT = "那年秋天，院子里的桂花开得很早。我和妹妹每天放学都绕路去看一眼。"


def provider_with(responses: list) -> LLMAgentProvider:
    """构造一个用固定序列代替真实 HTTP 的适配器。

    注意签名要与 `_chat` 保持一致（含 schema 参数），否则真实调用会穿透到网络。
    """
    provider = LLMAgentProvider(api_key="test-key")
    queue = list(responses)

    def fake_chat(*, user_prompt: str, max_tokens: int = 2000, schema: dict | None = None):
        if not queue:
            raise LLMUnavailableError("桩：没有更多响应")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    provider._chat = fake_chat  # type: ignore[method-assign]
    return provider


# --------------------------------------------------------------- 抽取校验


def test_claims_with_quote_not_in_transcript_are_dropped():
    """quote 不在原文里的证据必须丢弃（宁可缺失，不许编造）。"""
    provider = provider_with(
        [
            {
                "claims": [
                    {"element": "time", "text": "那年秋天", "quote": "那年秋天"},  # 真实子串
                    {"element": "place", "text": "北京", "quote": "北京的一个院子"},  # 编造
                ]
            }
        ]
    )
    result = provider.extract_claims(transcript=TRANSCRIPT, turn_id="t1")
    quotes = [claim["quote"] for claim in result["claims"]]
    assert "那年秋天" in quotes
    assert all("北京" not in quote for quote in quotes), "编造的原文必须被丢弃"


def test_illegal_element_is_dropped():
    provider = provider_with([{"claims": [{"element": "weather", "text": "很热", "quote": "很热"}]}])
    with pytest.raises(LLMUnavailableError):
        provider.extract_claims(transcript="那天很热。", turn_id="t1")


def test_missing_fields_stay_missing_when_model_omits_them():
    provider = provider_with(
        [{"claims": [{"element": "time", "text": "那年秋天", "quote": "那年秋天"}]}]
    )
    result = provider.extract_claims(transcript=TRANSCRIPT, turn_id="t1")
    assert "time" not in result["missing_fields"]
    for element in SEVEN_ELEMENTS:
        if element != "time":
            assert element in result["missing_fields"], "未讲到的要素必须保持缺失"


def test_quote_with_punctuation_variance_is_accepted():
    """模型常把标点写成半角或漏标点，这属于格式差异，不应误判为编造。"""
    transcript = "那年秋天，院子里的桂花开得很早。"
    provider = provider_with(
        [{"claims": [{"element": "time", "text": "那年秋天", "quote": "那年秋天,院子里的桂花开得很早"}]}]
    )
    result = provider.extract_claims(transcript=transcript, turn_id="t1")
    assert len(result["claims"]) == 1, "仅标点差异应被接受"


def test_paraphrased_quote_is_still_rejected():
    """放宽只针对标点与空白；改写用词仍然必须被拒。"""
    transcript = "那年秋天，院子里的桂花开得很早。"
    provider = provider_with(
        [{"claims": [{"element": "time", "text": "那年秋天", "quote": "那年秋天院子里桂花盛开得很早"}]}]
    )
    with pytest.raises(LLMUnavailableError):
        provider.extract_claims(transcript=transcript, turn_id="t1")


# --------------------------------------------------------------- 写作校验


def test_citations_pointing_to_unknown_claims_are_stripped():
    provider = provider_with(
        [
            {
                "title": "《我的家乡》",
                "sentences": [
                    {"text": "《我的家乡》", "claim_ids": [], "must_cite": False},
                    {"text": "那年秋天，桂花开得很早。", "claim_ids": ["claim-real", "claim-fake"]},
                ],
            }
        ]
    )
    claims = [{"id": "claim-real", "element": "time", "text": "那年秋天", "quote": "那年秋天"}]
    result = provider.compose_draft(subject_name="林阿姨", topic="我的家乡", claims=claims)
    factual = [s for s in result["sentences"] if s["must_cite"]]
    assert factual, "应当有事实句"
    assert factual[0]["claim_ids"] == ["claim-real"], "不存在的引用必须剔除"


def test_compose_without_claims_is_refused():
    provider = provider_with([])
    with pytest.raises(LLMUnavailableError):
        provider.compose_draft(subject_name="林阿姨", topic="我的家乡", claims=[])


def test_heading_is_not_marked_as_fact():
    provider = provider_with(
        [
            {
                "title": "《我的家乡》",
                "sentences": [
                    {"text": "《我的家乡》", "claim_ids": []},
                    {"text": "这是林阿姨讲述的一段记忆。", "claim_ids": []},
                    {"text": "那年秋天，桂花开得很早。", "claim_ids": ["c1"]},
                ],
            }
        ]
    )
    claims = [{"id": "c1", "element": "time", "text": "那年秋天", "quote": "那年秋天"}]
    result = provider.compose_draft(subject_name="林阿姨", topic="我的家乡", claims=claims)
    must_cite = [s["must_cite"] for s in result["sentences"]]
    assert must_cite == [False, False, True], "标题与来源说明不应强制引用"


# --------------------------------------------------------------- 停止意愿


def test_stop_intent_is_detected_locally_not_by_model():
    """停止意愿由本地规则判定：模型即使给出问题，也不允许继续追问。"""
    provider = provider_with([{"question": "要不要再讲讲别的？"}])
    decision = provider.choose_question(
        subject_name="林阿姨",
        topic="我的家乡",
        round_index=1,
        asked_questions=[],
        previous_answers=["算了，我不想讲了，今天就到这儿。"],
        missing_fields=["time"],
    )
    assert decision["should_stop"] is True
    assert decision["question"] is None


def test_duplicate_question_is_suppressed():
    asked = ["那时候是在什么地方？"]
    provider = provider_with([{"question": "那时候是在什么地方？", "target_element": "place"}])
    decision = provider.choose_question(
        subject_name="林阿姨",
        topic="我的家乡",
        round_index=1,
        asked_questions=asked,
        previous_answers=["那年秋天桂花开得很早。"],
        missing_fields=["place"],
    )
    assert decision["question"] is None, "同一问题不得重复追问"


# --------------------------------------------------------------- 降级策略


def test_fallback_provider_uses_mock_when_model_fails():
    failing = provider_with([LLMUnavailableError("桩：网络失败"), LLMUnavailableError("桩：网络失败")])
    wrapper = FallbackAgentProvider(failing, MockAgentProvider())
    result = wrapper.extract_claims(transcript=TRANSCRIPT, turn_id="t1")
    assert result["claims"], "回落实现应当产出证据"
    assert wrapper.fallback_count == 1
    assert wrapper.last_error


def test_audit_always_uses_deterministic_implementation():
    """审计不允许交给模型自证。"""
    exploding = provider_with([LLMUnavailableError("不该被调用")])
    wrapper = FallbackAgentProvider(exploding, MockAgentProvider())
    claims = [{"id": "c1", "element": "time", "text": "那年秋天", "quote": "那年秋天", "turn_id": "t1"}]
    sentences = [{"id": "s1", "text": "他去了北京。", "claim_ids": [], "must_cite": True}]
    findings = wrapper.audit_draft(sentences=sentences, claims=claims)
    assert findings and findings[0]["kind"] == "unsupported"
    assert wrapper.fallback_count == 0, "审计不该触发模型调用"
