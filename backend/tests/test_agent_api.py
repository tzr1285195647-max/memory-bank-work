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


def answer(
    client,
    auth,
    session_id: str,
    text: str,
    finish: bool = False,
    topic: str = "hometown",
    duration_ms: int = 0,
    recording_id: str | None = None,
    speaker_label: str = "长辈",
) -> dict:
    resp = client.post(
        "/api/agent/interviews/answers",
        headers=auth["headers"],
        json={
            "sessionId": session_id,
            "answer": text,
            "finish": finish,
            "topicId": topic,
            "durationMs": duration_ms,
            "recordingId": recording_id,
            "speakerLabel": speaker_label,
            "consentVersion": auth["consent"],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def upload_recording(client, auth, topic: str, duration_ms: int, name: str) -> dict:
    resp = client.post(
        "/api/recordings",
        headers=auth["headers"],
        data={
            "topicId": topic,
            "durationMs": str(duration_ms),
            "consentVersion": str(auth["consent"]),
        },
        files={"file": (name, b"ID3-demo-audio", "audio/mpeg")},
    )
    assert resp.status_code == 201, resp.text
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


def test_story_detail_keeps_evidence_and_recording_duration(client, auth):
    view = start_session(client, auth, topic="hometown")
    view = answer(
        client,
        auth,
        view["session_id"],
        "那年秋天，院子里的桂花开得很早。",
        finish=True,
        duration_ms=42000,
    )
    story = client.get(f"/api/stories/{view['storyId']}", headers=auth["headers"])
    assert story.status_code == 200, story.text
    body = story.json()
    assert body["durationMs"] == 42000
    assert body["claims"]
    assert body["sessionId"] == view["session_id"]
    assert body["missingFields"] == view["missing_fields"]


def test_multi_round_story_keeps_each_recording_and_total_duration(client, auth):
    view = start_session(client, auth, topic="hometown", rounds=3)
    first_audio = upload_recording(client, auth, "hometown", 12000, "round-1.mp3")
    view = answer(
        client,
        auth,
        view["session_id"],
        "那年秋天，我回到了老家的院子。",
        topic="hometown",
        duration_ms=12000,
        recording_id=first_audio["assetId"],
    )
    assert view["stage"] == "interview"

    second_audio = upload_recording(client, auth, "hometown", 18000, "round-2.mp3")
    view = answer(
        client,
        auth,
        view["session_id"],
        "妹妹也在，她说桂花香让她想起小时候。",
        finish=True,
        topic="hometown",
        duration_ms=18000,
        recording_id=second_audio["assetId"],
        speaker_label="家人",
    )
    story = client.get(f"/api/stories/{view['storyId']}", headers=auth["headers"])
    assert story.status_code == 200, story.text
    body = story.json()
    assert body["durationMs"] == 30000
    assert len(body["recordings"]) == 2
    assert [item["roundIndex"] for item in body["recordings"]] == [0, 1]
    assert [item["speakerLabel"] for item in body["recordings"]] == ["长辈", "家人"]
    assert [item["transcript"] for item in body["recordings"]] == [
        "那年秋天，我回到了老家的院子。",
        "妹妹也在，她说桂花香让她想起小时候。",
    ]
    assert all(item["timeLabel"] for item in body["recordings"])
    assert {item["recordingId"] for item in body["recordings"]} == {
        first_audio["assetId"],
        second_audio["assetId"],
    }
    claim_turns = {claim["turn_id"] for claim in body["claims"]}
    assert {item["turnId"] for item in body["recordings"]}.issubset(claim_turns)


def test_selected_memory_fragments_generate_one_story(client, auth):
    first = upload_recording(client, auth, "work", 9000, "fragment-1.mp3")
    second = upload_recording(client, auth, "work", 8000, "fragment-2.mp3")
    third = upload_recording(client, auth, "work", 7000, "fragment-3.mp3")
    texts = {
        first["assetId"]: "第一天上班时，师傅带我熟悉了车间。",
        second["assetId"]: "中午食堂做了红烧肉。",
        third["assetId"]: "后来我学会了独立操作机器。",
    }
    for recording_id, transcript in texts.items():
        response = client.put(
            f"/api/recordings/{recording_id}/fragment",
            headers=auth["headers"],
            json={"transcript": transcript, "consentVersion": auth["consent"]},
        )
        assert response.status_code == 200, response.text

    generated = client.post(
        "/api/agent/fragments/generate",
        headers=auth["headers"],
        json={
            "recordingIds": [first["assetId"], third["assetId"]],
            "topicId": "work",
            "subjectName": "林阿姨",
            "style": "book",
            "consentVersion": auth["consent"],
        },
    )
    assert generated.status_code == 201, generated.text
    story = generated.json()
    assert story["mode"] == "适合成书"
    assert story["durationMs"] == 16000
    assert {item["recordingId"] for item in story["recordings"]} == {
        first["assetId"],
        third["assetId"],
    }
    assert second["assetId"] not in {item["recordingId"] for item in story["recordings"]}
    assert "红烧肉" not in story["body"]
    assert story["sentenceEvidence"]
    assert all(
        (not sentence["must_cite"]) or sentence["claim_ids"]
        for sentence in story["sentenceEvidence"]
    )


def test_family_reviewer_cannot_replace_original_narrator(client):
    """小刘代为确认和生成时，故事署名仍必须来自林奶奶的录音归属。"""

    elder_login = client.post(
        "/api/auth/login",
        json={"phone": "13800008899", "password": "123456", "role": "elder"},
    ).json()
    family_login = client.post(
        "/api/auth/login",
        json={"phone": "13700006677", "password": "123456", "role": "family"},
    ).json()
    elder_auth = {
        "headers": {"Authorization": f"Bearer {elder_login['token']}"},
        "consent": elder_login["consentVersion"],
    }
    recording = upload_recording(client, elder_auth, "school", 12000, "grandma-school.mp3")
    confirmed = client.put(
        f"/api/recordings/{recording['assetId']}/fragment",
        headers=elder_auth["headers"],
        json={
            "transcript": "我第一次上学是在一九五九年的秋天。",
            "consentVersion": elder_auth["consent"],
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    generated = client.post(
        "/api/agent/fragments/generate",
        headers={"Authorization": f"Bearer {family_login['token']}"},
        json={
            "recordingIds": [recording["assetId"]],
            "topicId": "school",
            # 即使旧客户端错误地提交当前审核人的名字，服务端也必须忽略。
            "subjectName": "小刘",
            "style": "book",
            "consentVersion": family_login["consentVersion"],
        },
    )
    assert generated.status_code == 201, generated.text
    story = generated.json()
    assert story["narratorName"] == "林奶奶"
    assert "林奶奶亲口讲述" in story["body"]
    assert "小刘亲口讲述" not in story["body"]


def test_fragments_from_two_narrators_cannot_generate_single_person_story(client):
    elder_a = client.post("/api/auth/login", json={"phone": "13800008899", "password": "123456"}).json()
    elder_b = client.post("/api/auth/login", json={"phone": "13900007788", "password": "123456"}).json()
    auth_a = {"headers": {"Authorization": f"Bearer {elder_a['token']}"}, "consent": elder_a["consentVersion"]}
    auth_b = {"headers": {"Authorization": f"Bearer {elder_b['token']}"}, "consent": elder_b["consentVersion"]}
    first = upload_recording(client, auth_a, "school", 3000, "elder-a.mp3")
    second = upload_recording(client, auth_b, "school", 3000, "elder-b.mp3")
    for auth_data, item, text in (
        (auth_a, first, "我第一次上学是在秋天。"),
        (auth_b, second, "我第一次上学是在春天。"),
    ):
        response = client.put(
            f"/api/recordings/{item['assetId']}/fragment", headers=auth_data["headers"],
            json={"transcript": text, "consentVersion": auth_data["consent"]},
        )
        assert response.status_code == 200, response.text
    mixed = client.post(
        "/api/agent/fragments/generate", headers=auth_a["headers"],
        json={"recordingIds": [first["assetId"], second["assetId"]], "topicId": "school",
              "style": "natural", "consentVersion": auth_a["consent"]},
    )
    assert mixed.status_code == 422
    assert "同一位讲述人" in mixed.json()["detail"]


def test_stop_interview_keeps_topic_for_fragment_generation(client, auth):
    view = start_session(client, auth, topic="school", rounds=3)
    view = answer(
        client,
        auth,
        view["session_id"],
        "第一次上学那天，是妈妈送我到学校门口的。",
        topic="school",
    )
    stopped = client.post(
        "/api/agent/interviews/stop",
        headers=auth["headers"],
        json={
            "sessionId": view["session_id"],
            "topicId": "school",
            "consentVersion": auth["consent"],
        },
    )
    assert stopped.status_code == 200, stopped.text
    story = client.get(
        f"/api/stories/{stopped.json()['storyId']}", headers=auth["headers"]
    )
    assert story.status_code == 200, story.text
    assert story.json()["topicId"] == "school"


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
    assert story["status"] == "confirmed", "长辈在 LangGraph 人工确认点批准后应正式发布"


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


def test_preview_audit_reports_unsupported_edit_without_publishing(client, auth):
    view = start_session(client, auth, topic="work")
    view = answer(
        client,
        auth,
        view["session_id"],
        "那年秋天，我在镇上的木工坊跟着师傅学手艺。",
        finish=True,
        topic="work",
    )
    story_id = view["storyId"]
    audited = client.post(
        f"/api/stories/{story_id}/audit",
        headers=auth["headers"],
        json={"body": "后来我去了北京开了一家公司。", "consentVersion": auth["consent"]},
    )
    assert audited.status_code == 200, audited.text
    assert audited.json()["auditPassed"] is False
    assert audited.json()["findings"]
    story = client.get(f"/api/stories/{story_id}", headers=auth["headers"]).json()
    assert story["status"] == "pending_review"


def test_discard_pending_story_removes_its_audio_only(client, auth):
    view = start_session(client, auth, topic="school")
    audio = upload_recording(client, auth, "school", 9000, "discard-me.mp3")
    view = answer(
        client,
        auth,
        view["session_id"],
        "十八岁那年，我第一次坐火车离开家去上学。",
        finish=True,
        topic="school",
        duration_ms=9000,
        recording_id=audio["assetId"],
    )
    story_id = view["storyId"]
    assert client.get(audio["audioUrl"]).status_code == 200

    discarded = client.post(
        f"/api/stories/{story_id}/discard",
        headers=auth["headers"],
        json={"consentVersion": auth["consent"]},
    )
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["deletedRecordings"] == 1
    assert client.get(f"/api/stories/{story_id}", headers=auth["headers"]).status_code == 404
    assert client.get(audio["audioUrl"]).status_code == 404


def test_request_more_resumes_same_story_and_appends_evidence(client, auth):
    view = start_session(client, auth, topic="family", rounds=3)
    first_audio = upload_recording(client, auth, "family", 7000, "before-review.mp3")
    view = answer(
        client,
        auth,
        view["session_id"],
        "结婚那天，家里来了很多亲戚。",
        finish=True,
        topic="family",
        duration_ms=7000,
        recording_id=first_audio["assetId"],
    )
    original_story_id = view["storyId"]

    resumed = client.post(
        "/api/agent/interviews/review",
        headers=auth["headers"],
        json={
            "sessionId": view["session_id"],
            "action": "request_more",
            "consentVersion": auth["consent"],
        },
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["stage"] == "interview"
    assert resumed.json()["question"]

    second_audio = upload_recording(client, auth, "family", 8000, "after-review.mp3")
    finished = answer(
        client,
        auth,
        view["session_id"],
        "那是在老家的院子里，我既紧张又高兴。",
        finish=True,
        topic="family",
        duration_ms=8000,
        recording_id=second_audio["assetId"],
    )
    assert finished["storyId"] == original_story_id
    story = client.get(f"/api/stories/{original_story_id}", headers=auth["headers"]).json()
    assert story["topicId"] == "family"
    assert story["durationMs"] == 15000
    assert len(story["recordings"]) == 2


def test_stop_intent_ends_interview(client, auth):
    view = start_session(client, auth, topic="school", rounds=4)
    session_id = view["session_id"]
    answer(client, auth, session_id, "那年秋天，院子里的桂花开得很早。", topic="school")
    stopped = answer(client, auth, session_id, "算了，我不想讲了，今天就到这儿。", topic="school")
    assert stopped["stop_requested"] is True
    kinds = [item["value"].get("kind") for item in stopped["interrupts"]]
    assert "interview" not in kinds
