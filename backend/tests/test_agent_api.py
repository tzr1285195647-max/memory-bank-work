"""多智能体 HTTP 接口测试：把端到端要点固化成回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="memory-bank-agent-api-")
os.environ["MEMORY_BANK_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path(_TMP) / 'test.db'}"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def client():
    from backend.agent_service import reset_runtime
    from backend.app import create_app

    reset_runtime()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_runtime()


@pytest.fixture(scope="module")
def auth(client):
    login = client.post(
        "/api/auth/login", json={"phone": "13600009999", "password": "abcdef", "role": "elder"}
    ).json()
    return {
        "headers": {"Authorization": f"Bearer {login['token']}"},
        "consent": login["consentVersion"],
    }


def start_session(client, auth, topic: str = "hometown", rounds: int = 1) -> dict:
    resp = client.post(
        "/api/agent/interviews",
        headers=auth["headers"],
        json={
            "topicId": topic,
            "subjectName": "林阿姨",
            "maxRounds": rounds,
            "consentVersion": auth["consent"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def answer(client, auth, session_id: str, text: str, finish: bool = False, topic: str = "hometown") -> dict:
    resp = client.post(
        "/api/agent/interviews/answers",
        headers=auth["headers"],
        json={
            "sessionId": session_id,
            "answer": text,
            "finish": finish,
            "topicId": topic,
            "consentVersion": auth["consent"],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_interview_starts_with_one_question(client, auth):
    view = start_session(client, auth)
    kinds = [item["value"].get("kind") for item in view["interrupts"]]
    assert kinds == ["interview"]
    assert view["interrupts"][0]["value"]["question"]


def test_answer_produces_evidence_with_traceable_quotes(client, auth):
    view = start_session(client, auth, topic="school")
    view = answer(client, auth, view["session_id"], "那年秋天，院子里的桂花开得很早。", finish=True, topic="school")
    assert view["claims"], "应当抽取到证据"
    turns = {turn["id"]: turn["answer"] for turn in view["turns"]}
    for claim in view["claims"]:
        assert claim["quote"] in turns[claim["turn_id"]]


def test_unauthenticated_is_rejected(client):
    assert client.post("/api/agent/interviews", json={}).status_code == 401


def test_stale_consent_version_is_rejected(client, auth):
    resp = client.post(
        "/api/agent/interviews",
        headers=auth["headers"],
        json={"topicId": "hometown", "consentVersion": 999, "maxRounds": 1},
    )
    assert resp.status_code == 403


def test_edit_with_new_facts_is_rejected_by_audit(client, auth):
    view = start_session(client, auth, topic="work")
    session_id = view["session_id"]
    view = answer(client, auth, session_id, "那年秋天，院子里的桂花开得很早。", finish=True, topic="work")
    assert view["audit_passed"] is True

    resp = client.post(
        "/api/agent/interviews/review",
        headers=auth["headers"],
        json={
            "sessionId": session_id,
            "action": "edit",
            "editedText": "他后来去了北京，那年他二十五岁。",
            "consentVersion": auth["consent"],
        },
    )
    assert resp.status_code == 403, resp.text
    assert "无证据" in resp.json()["detail"]


def test_review_action_outside_review_point_is_rejected(client, auth):
    """改写被拒后流程退回采访，此时确认动作必须被明确拒绝。"""
    view = start_session(client, auth, topic="family", rounds=2)
    session_id = view["session_id"]
    answer(client, auth, session_id, "那年秋天，院子里的桂花开得很早。", topic="family")
    answer(client, auth, session_id, "外婆总是在门口等我们。", finish=True, topic="family")

    # 第一次改写被拒 → 退回采访
    blocked = client.post(
        "/api/agent/interviews/review",
        headers=auth["headers"],
        json={
            "sessionId": session_id,
            "action": "edit",
            "editedText": "他后来去了北京，那年他二十五岁。",
            "consentVersion": auth["consent"],
        },
    )
    assert blocked.status_code == 403

    # 此时不在确认点，再次调用确认动作必须 409
    wrong = client.post(
        "/api/agent/interviews/review",
        headers=auth["headers"],
        json={"sessionId": session_id, "action": "approve", "consentVersion": auth["consent"]},
    )
    assert wrong.status_code == 409, wrong.text


def test_approve_produces_delivery_and_story(client, auth):
    view = start_session(client, auth, topic="hometown")
    session_id = view["session_id"]
    view = answer(client, auth, session_id, "那年秋天，院子里的桂花开得很早。", finish=True)
    assert view["storyId"], "草稿应落库为待确认故事"

    approved = client.post(
        "/api/agent/interviews/review",
        headers=auth["headers"],
        json={"sessionId": session_id, "action": "approve", "consentVersion": auth["consent"]},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["delivery"]
    assert "经过人工确认" in body["delivery"]

    story = client.get(f"/api/stories/{view['storyId']}", headers=auth["headers"]).json()
    assert story["status"] == "pending_review", "故事仍需人工在故事书里确认"


def test_story_review_rejects_invented_text(client, auth):
    view = start_session(client, auth, topic="hometown")
    session_id = view["session_id"]
    view = answer(client, auth, session_id, "那年秋天，院子里的桂花开得很早。", finish=True)
    story_id = view["storyId"]

    bad = client.post(
        f"/api/agent/stories/{story_id}/review",
        headers=auth["headers"],
        json={"body": "这段内容完全是编造的。", "consentVersion": auth["consent"]},
    )
    assert bad.status_code in {403, 409}

    good = client.post(
        f"/api/agent/stories/{story_id}/review",
        headers=auth["headers"],
        json={"body": view["draft_text"], "consentVersion": auth["consent"]},
    )
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "confirmed"


def test_stop_intent_ends_interview(client, auth):
    view = start_session(client, auth, topic="school", rounds=4)
    session_id = view["session_id"]
    answer(client, auth, session_id, "那年秋天，院子里的桂花开得很早。", topic="school")
    stopped = answer(client, auth, session_id, "算了，我不想讲了，今天就到这儿。", topic="school")
    assert stopped["stop_requested"] is True
    kinds = [item["value"].get("kind") for item in stopped["interrupts"]]
    assert "interview" not in kinds
