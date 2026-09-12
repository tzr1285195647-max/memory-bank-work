"""多智能体层测试：锁住证据链与协同行为的不变量。

运行：python -m pytest backend/tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.agents import AgentRuntime  # noqa: E402
from backend.agents.mock import MockAgentProvider  # noqa: E402
from backend.agents.state import SEVEN_ELEMENTS  # noqa: E402


@pytest.fixture()
def runtime(tmp_path: Path):
    rt = AgentRuntime(tmp_path / "checkpoints.sqlite3")
    yield rt
    rt.close()


def start(runtime: AgentRuntime, session_id: str = "s1", **kwargs):
    return runtime.start_session(
        session_id=session_id,
        family_id="family-1",
        subject_name="林阿姨",
        topic="我的家乡",
        max_rounds=kwargs.pop("max_rounds", 4),
        **kwargs,
    )


# --------------------------------------------------------------- 采访导演


def test_interview_asks_exactly_one_question_and_waits(runtime):
    view = start(runtime)
    assert view["stage"] == "interview"
    assert len(view["interrupts"]) == 1
    payload = view["interrupts"][0]["value"]
    assert payload["kind"] == "interview"
    assert payload["question"]
    # 一次只问一个问题
    assert payload["question"].count("？") <= 1


def test_stop_intent_ends_interview_without_more_questions(runtime):
    """停止意愿在「选下一个问题时」被识别：不再追问，转为确认或收尾。"""
    start(runtime, "stop-1", max_rounds=5)
    # 第一轮正常讲述：采访循环会继续追问
    view = runtime.resume("stop-1", {"answer": "那年秋天，院子里的桂花开得很早。"})
    assert view["stage"] == "interview", "要素未齐时应继续追问"
    assert any(item["value"].get("kind") == "interview" for item in view["interrupts"])

    # 第二轮表达停止意愿：必须不再提问
    view = runtime.resume("stop-1", {"answer": "算了，我不想讲了，今天就到这儿。"})
    assert view["stop_requested"] is True, "应识别到停止意愿"
    assert view["stop_reason"]
    pending = [item["value"].get("kind") for item in view["interrupts"]]
    assert "interview" not in pending, "停止后不允许再出现采访问题"
    assert view["stage"] in {"review", "delivered", "rejected"}


def test_interview_does_not_repeat_same_follow_up(runtime):
    start(runtime, "dup-1", max_rounds=4)
    asked: list[str] = []
    for _ in range(3):
        view = runtime.view("dup-1")
        if not view["interrupts"]:
            break
        payload = view["interrupts"][0]["value"]
        if payload.get("kind") != "interview":
            break
        asked.append(payload["question"])
        view = runtime.resume("dup-1", {"answer": "那年秋天，院子里的桂花开得很早。"})
    # 同一缺失要素的追问模板不应重复
    assert len(asked) == len(set(asked)), f"追问重复：{asked}"


# --------------------------------------------------------------- 证据抽取


def test_claims_carry_original_quote_and_turn_id(runtime):
    start(runtime, "ev-1")
    view = runtime.resume(
        "ev-1",
        {"answer": "那年秋天，院子里的桂花开得很早。我和妹妹每天放学都绕路去看一眼。", "finish": True},
    )
    assert view["claims"], "应当抽取到证据"
    for claim in view["claims"]:
        # 证据链的根：每条事实必须带原文片段与来源轮次
        assert claim["quote"], "缺少原文片段"
        assert claim["turn_id"], "缺少来源轮次"
        assert claim["element"] in SEVEN_ELEMENTS
        # quote 必须是该轮讲述的原样子串
        turn = next(t for t in view["turns"] if t["id"] == claim["turn_id"])
        assert claim["quote"] in turn["answer"]


def test_missing_elements_stay_missing_and_are_never_invented(runtime):
    start(runtime, "miss-1")
    view = runtime.resume("miss-1", {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True})
    covered = {claim["element"] for claim in view["claims"]}
    # 没讲到的要素必须留在 missing_fields，不允许被补造
    for element in SEVEN_ELEMENTS:
        if element not in covered:
            assert element in view["missing_fields"], f"{element} 应保持缺失"


def test_no_duplicate_turns_or_claims_across_resumes(runtime):
    """回归测试：子图回传父图会重复应用 reducer（langgraph#4007），
    reducer 必须幂等，否则轮次与证据会成倍增长。"""
    start(runtime, "dup-2", max_rounds=4)
    for index in range(3):
        view = runtime.resume("dup-2", {"answer": f"第{index}轮讲述，那年秋天桂花开得很早。", "finish": index == 2})
    turn_ids = [turn["id"] for turn in view["turns"]]
    claim_ids = [claim["id"] for claim in view["claims"]]
    assert len(turn_ids) == len(set(turn_ids)), f"轮次重复：{turn_ids}"
    assert len(claim_ids) == len(set(claim_ids)), f"证据重复：{claim_ids}"
    assert len(turn_ids) == 3


def test_answers_survive_multiple_resumes(runtime):
    start(runtime, "resume-1", max_rounds=4)
    runtime.resume("resume-1", {"answer": "第一轮：那年秋天桂花开得早。"})
    runtime.resume("resume-1", {"answer": "第二轮：外婆在门口等我们。"})
    view = runtime.resume("resume-1", {"answer": "第三轮：后来外婆走了。", "finish": True})
    answers = [turn["answer"] for turn in view["turns"]]
    assert len(answers) == 3
    assert any("第一轮" in a for a in answers)
    assert any("第三轮" in a for a in answers)


# --------------------------------------------------------------- 写作与审计


def test_draft_sentences_cite_claims_and_audit_passes(runtime):
    start(runtime, "write-1")
    view = runtime.resume(
        "write-1",
        {"answer": "那年秋天，院子里的桂花开得很早。我和妹妹每天放学都绕路去看一眼。", "finish": True},
    )
    assert view["draft_sentences"], "应当生成草稿"
    claim_ids = {claim["id"] for claim in view["claims"]}
    factual = [s for s in view["draft_sentences"] if s["must_cite"]]
    assert factual, "应当存在需要引用的事实句"
    for sentence in factual:
        assert sentence["claim_ids"], f"事实句缺少引用：{sentence['text']}"
        assert set(sentence["claim_ids"]) <= claim_ids
    # 所有事实句都有引用时，审计必须通过
    assert view["audit_passed"] is True
    assert view["audit_findings"] == []


def test_auditor_rejects_unsupported_sentence():
    """审计智能体必须真的检查：无引用的事实句、引用了不存在的证据，都要报出来。"""
    provider = MockAgentProvider()
    claims = [{"id": "c1", "element": "time", "text": "那年秋天", "quote": "那年秋天。", "turn_id": "t1"}]
    sentences = [
        {"id": "s1", "text": "那年秋天。", "claim_ids": ["c1"], "must_cite": True},
        {"id": "s2", "text": "他后来去了北京。", "claim_ids": [], "must_cite": True},
        {"id": "s3", "text": "这是过渡句。", "claim_ids": [], "must_cite": False},
        {"id": "s4", "text": "引用不存在的证据。", "claim_ids": ["c999"], "must_cite": True},
    ]
    findings = provider.audit_draft(sentences=sentences, claims=claims)
    kinds = {finding["kind"] for finding in findings}
    ids = {finding["sentence_id"] for finding in findings}
    assert "unsupported" in kinds
    assert "invalid_citation" in kinds
    assert ids == {"s2", "s4"}, "过渡句不应被判定为问题"


def test_rejected_edit_does_not_overwrite_draft(runtime):
    """改写引入无证据内容时必须被拒，且**不得覆盖**原有可用草稿。"""
    start(runtime, "audit-1")
    view = runtime.resume(
        "audit-1",
        {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True},
    )
    assert view["stage"] == "review"
    assert view["audit_passed"] is True
    original = view["draft_text"]

    view = runtime.resume("audit-1", {"action": "edit", "edited_text": "他后来去了北京，那年他二十五岁。"})
    # 改写被审计拒绝，草稿保持原样（用户的可用内容不能因为一次错误改写而丢失）
    assert view["audit_passed"] is False
    assert view["audit_findings"], "改写引入新事实必须被审计报出"
    assert view["draft_text"] == original, "被拒的改写不应覆盖原草稿"

    # 此时批准也必须被拒（批准以当前正文重新核对）
    view = runtime.resume("audit-1", {"action": "approve"})
    assert not view.get("delivery"), "存在无证据内容时不得产出交付物"


def test_edit_with_evidence_backed_text_is_accepted(runtime):
    """只做措辞润色、不引入新事实的改写应当通过，并更新正文。"""
    start(runtime, "audit-2")
    view = runtime.resume(
        "audit-2",
        {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True},
    )
    original = view["draft_text"]
    polished = original.replace(
        "这是林阿姨亲口讲述并等待确认的一段家庭记忆。", "这是林阿姨亲口讲述的一段家庭记忆。"
    )
    view = runtime.resume("audit-2", {"action": "edit", "edited_text": polished})
    assert view["audit_passed"] is True, str(view["audit_findings"])
    assert view["draft_text"] == polished, "通过审计的改写应当更新正文"


# --------------------------------------------------------------- 确认与交付


def test_approve_produces_delivery(runtime):
    start(runtime, "approve-1")
    runtime.resume("approve-1", {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True})
    view = runtime.resume("approve-1", {"action": "approve"})
    assert view["stage"] == "delivered"
    assert view["delivery"]
    assert "经过人工确认" in view["delivery"]


def test_reject_ends_without_delivery(runtime):
    start(runtime, "reject-1")
    runtime.resume("reject-1", {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True})
    view = runtime.resume("reject-1", {"action": "reject"})
    assert view["stage"] == "rejected"
    assert not view["delivery"]


def test_request_more_returns_to_interview(runtime):
    start(runtime, "more-1")
    runtime.resume("more-1", {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True})
    view = runtime.resume("more-1", {"action": "request_more"})
    assert view["stage"] == "interview"


# --------------------------------------------------------------- 授权闸门


def test_revoked_consent_takes_delete_path_without_writing(runtime):
    start(runtime, "revoke-1", consent_ok=False)
    view = runtime.view("revoke-1")
    assert view["stage"] == "revoked"
    assert not view["draft_text"], "撤回后不得产生草稿"


# --------------------------------------------------------------- 协同轨迹


def test_agent_trace_records_who_did_what(runtime):
    start(runtime, "trace-1")
    view = runtime.resume("trace-1", {"answer": "那年秋天，院子里的桂花开得很早。", "finish": True})
    nodes = {step["node"] for step in view["agent_trace"]}
    # 五类智能体各自留下的轨迹
    assert "interview.select_question" in nodes
    assert "interview.commit_turn" in nodes
    assert "evidence.extract_claims" in nodes
    assert "evidence.merge" in nodes
    assert "writing.draft" in nodes
    assert "writing.audit" in nodes
    # 轨迹不允许重复（幂等 reducer）
    seqs = [(step["node"], step["seq"]) for step in view["agent_trace"]]
    assert len(seqs) == len(set(seqs)), "轨迹出现重复"
