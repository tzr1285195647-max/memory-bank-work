"""后端契约与产品规则测试。

运行：python -m pytest backend/tests -q
测试不依赖网络，使用临时数据目录与内存 SQLite。
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 让测试使用独立的数据目录，避免污染开发库
_TMP = tempfile.mkdtemp(prefix="memory-bank-test-")
os.environ["MEMORY_BANK_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path(_TMP) / 'test.db'}"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def client():
    from backend.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def session_data(client):
    resp = client.post(
        "/api/auth/login",
        json={"phone": "13900001111", "password": "abcdef", "role": "elder"},
    )
    assert resp.status_code == 200
    return resp.json()


def auth_header(session_data: dict) -> dict:
    return {"Authorization": f"Bearer {session_data['token']}"}


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_requires_token(client):
    assert client.get("/api/stories").status_code == 401
    assert client.get("/api/topics").status_code == 401
    assert client.get("/api/family").status_code == 401


def test_login_returns_scoped_identity(client, session_data):
    assert session_data["token"]
    assert session_data["familyId"]
    assert session_data["consentVersion"] >= 1
    # 手机号必须脱敏后返回，不得出现明文
    assert session_data["user"]["phoneMasked"] == "139****1111"
    assert "13900001111" not in str(session_data["user"])


def test_topics_seeded_from_design(client, session_data):
    topics = client.get("/api/topics", headers=auth_header(session_data)).json()
    assert len(topics) == 4
    assert [t["glyph"] for t in topics] == ["乡", "校", "业", "家"]


def test_four_demo_accounts_share_one_family_and_liu_is_admin(client):
    sessions = []
    for phone in ("13800008899", "13900007788", "13700006677", "13600005566"):
        response = client.post(
            "/api/auth/login", json={"phone": phone, "password": "123456"}
        )
        assert response.status_code == 200, response.text
        sessions.append(response.json())
    assert len({item["familyId"] for item in sessions}) == 1
    assert [item["user"]["displayName"] for item in sessions] == ["林奶奶", "王爷爷", "小刘", "小李"]
    assert sessions[2]["user"]["isAdmin"] is True
    assert sessions[0]["user"]["avatarKey"] == "elder-female"


def test_admin_phone_invite_register_update_and_remove(client):
    admin = client.post(
        "/api/auth/login", json={"phone": "13700006677", "password": "123456"}
    ).json()
    headers = auth_header(admin)
    phone = "13100001234"
    invited = client.post(
        "/api/family/invitations", headers=headers,
        json={"phone": phone, "role": "family"},
    )
    assert invited.status_code == 201, invited.text
    registered = client.post(
        "/api/auth/register",
        json={
            "phone": phone, "password": "abcdef", "displayName": "小周",
            "role": "family", "gender": "female", "age": 28,
        },
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["familyId"] == admin["familyId"]
    member_id = registered.json()["user"]["id"]
    updated = client.patch(
        f"/api/family/members/{member_id}", headers=headers,
        json={"displayName": "小周周", "age": 29},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["displayName"] == "小周周"
    removed = client.delete(f"/api/family/members/{member_id}", headers=headers)
    assert removed.status_code == 200, removed.text


def test_recording_upload_and_media_access(client, session_data):
    payload = b"ID3\x03\x00\x00\x00" + b"\xff" * 512
    resp = client.post(
        "/api/recordings",
        headers=auth_header(session_data),
        files={"file": ("voice.mp3", io.BytesIO(payload), "audio/mpeg")},
        data={
            "topicId": "hometown",
            "durationMs": "9000",
            "consentVersion": str(session_data["consentVersion"]),
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["assetId"]
    media = client.get(body["audioUrl"])
    assert media.status_code == 200
    assert media.content == payload


def test_memory_fragment_crud_and_persistent_listing(client, session_data):
    headers = auth_header(session_data)
    upload = client.post(
        "/api/recordings", headers=headers,
        files={"file": ("fragment.mp3", io.BytesIO(b"ID3" + b"x" * 256), "audio/mpeg")},
        data={
            "topicId": "school", "durationMs": "3000",
            "consentVersion": str(session_data["consentVersion"]),
        },
    )
    assert upload.status_code == 201, upload.text
    recording_id = upload.json()["assetId"]
    confirmed = client.put(
        f"/api/recordings/{recording_id}/fragment", headers=headers,
        json={"transcript": "这是第一段校对文字。", "consentVersion": session_data["consentVersion"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmedText"] == "这是第一段校对文字。"
    listed = client.get("/api/fragments?topicId=school", headers=headers)
    assert recording_id in {item["recordingId"] for item in listed.json()}
    fragment = next(item for item in listed.json() if item["recordingId"] == recording_id)
    assert fragment["facts"]
    assert all(item["quote"] in fragment["confirmedText"] for item in fragment["facts"])
    assert all(item["fragmentId"] == recording_id for item in fragment["facts"])
    updated = client.patch(
        f"/api/fragments/{recording_id}", headers=headers,
        json={"transcript": "这是修改后的文字。", "topicId": "work", "consentVersion": session_data["consentVersion"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["topicId"] == "work"
    deleted = client.delete(
        f"/api/fragments/{recording_id}?consentVersion={session_data['consentVersion']}", headers=headers,
    )
    assert deleted.status_code == 200, deleted.text


def test_recording_transcription_flow_without_network(client, session_data, monkeypatch):
    from backend import service
    from backend.asr import TranscriptionResult

    class FakeAsrClient:
        def submit(self, audio: bytes) -> str:
            assert audio.startswith(b"ID3")
            return "998877"

        def query(self, task_id: str) -> TranscriptionResult:
            assert task_id == "998877"
            return TranscriptionResult(status="success", transcript="这是经过校对前的云端识别文字。")

    monkeypatch.setattr(service, "create_client", lambda: FakeAsrClient())
    upload = client.post(
        "/api/recordings",
        headers=auth_header(session_data),
        files={"file": ("asr.mp3", io.BytesIO(b"ID3-real-audio"), "audio/mpeg")},
        data={
            "topicId": "hometown",
            "durationMs": "3000",
            "consentVersion": str(session_data["consentVersion"]),
        },
    ).json()

    started = client.post(
        f"/api/recordings/{upload['assetId']}/transcription",
        headers=auth_header(session_data),
        json={"consentVersion": session_data["consentVersion"]},
    )
    assert started.status_code == 200
    assert started.json()["status"] == "waiting"

    completed = client.get(
        f"/api/recordings/{upload['assetId']}/transcription",
        headers=auth_header(session_data),
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "success"
    assert completed.json()["transcript"] == "这是经过校对前的云端识别文字。"
    assert completed.json()["asrRawText"] == "这是经过校对前的云端识别文字。"
    assert completed.json()["agentCleanText"] == "这是经过校对前的云端识别文字。"
    assert completed.json()["confirmedText"] == ""
    assert completed.json()["cleanStatus"] == "success"


def test_stale_consent_version_is_rejected(client, session_data):
    resp = client.post(
        "/api/recordings",
        headers=auth_header(session_data),
        files={"file": ("voice.mp3", io.BytesIO(b"x" * 32), "audio/mpeg")},
        data={"topicId": "hometown", "durationMs": "1000", "consentVersion": "999"},
    )
    assert resp.status_code == 403


def test_draft_requires_confirmation_before_visible(client, session_data):
    resp = client.post(
        "/api/stories/draft",
        headers=auth_header(session_data),
        data={
            "topicId": "school",
            "durationMs": "3000",
            "consentVersion": str(session_data["consentVersion"]),
        },
    )
    assert resp.status_code == 201
    draft = resp.json()
    # 未确认内容必须停在 pending_review，且不出现在首页「最近的故事」里
    assert draft["status"] == "pending_review"
    home = client.get("/api/home", headers=auth_header(session_data)).json()
    assert all(item["id"] != draft["id"] for item in home["recent"])


def test_edit_resets_status_and_confirm_sets_it(client, session_data):
    draft = client.post(
        "/api/stories/draft",
        headers=auth_header(session_data),
        data={
            "topicId": "work",
            "durationMs": "1000",
            "consentVersion": str(session_data["consentVersion"]),
        },
    ).json()
    story_id = draft["id"]

    patched = client.patch(
        f"/api/stories/{story_id}",
        headers=auth_header(session_data),
        json={
            "body": "改后的正文",
            "mode": "适合成书",
            "memoryYear": 1986,
            "lifeStage": "工作",
            "consentVersion": session_data["consentVersion"],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "pending_review"
    assert patched.json()["mode"] == "适合成书"
    assert patched.json()["memoryYear"] == 1986
    assert patched.json()["lifeStage"] == "工作"

    invalid_mode = client.patch(
        f"/api/stories/{story_id}",
        headers=auth_header(session_data),
        json={
            "body": "改后的正文",
            "mode": "自动编写",
            "consentVersion": session_data["consentVersion"],
        },
    )
    assert invalid_mode.status_code == 422

    invalid_timeline = client.patch(
        f"/api/stories/{story_id}",
        headers=auth_header(session_data),
        json={
            "body": "改后的正文",
            "memoryYear": 1800,
            "lifeStage": "幻想期",
            "consentVersion": session_data["consentVersion"],
        },
    )
    assert invalid_timeline.status_code == 422

    confirmed = client.post(
        f"/api/stories/{story_id}/confirm",
        headers=auth_header(session_data),
        json={"consentVersion": session_data["consentVersion"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    enriched = client.patch(
        f"/api/stories/{story_id}",
        headers=auth_header(session_data),
        json={
            "body": "改后的正文",
            "memoryYear": 1987,
            "lifeStage": "家庭",
            "consentVersion": session_data["consentVersion"],
        },
    )
    assert enriched.status_code == 200
    assert enriched.json()["status"] == "confirmed"
    assert enriched.json()["memoryYear"] == 1987


def test_cross_family_story_is_not_visible(client, session_data):
    other = client.post(
        "/api/auth/login",
        json={"phone": "13700002222", "password": "abcdef", "role": "family"},
    ).json()
    stories = client.get("/api/stories", headers=auth_header(other)).json()
    mine = client.get("/api/stories", headers=auth_header(session_data)).json()
    my_ids = {item["id"] for item in mine["items"]}
    # 另一个家庭的列表里不允许出现我的故事
    assert all(item["id"] not in my_ids for item in stories["items"])

    target_id = next(iter(my_ids))
    other_headers = auth_header(other)
    audit = client.post(
        f"/api/stories/{target_id}/audit",
        headers=other_headers,
        json={"body": "试图跨家庭读取", "consentVersion": other["consentVersion"]},
    )
    discard = client.post(
        f"/api/stories/{target_id}/discard",
        headers=other_headers,
        json={"consentVersion": other["consentVersion"]},
    )
    assert audit.status_code == 404
    assert discard.status_code == 404


def test_audit_events_are_scoped_and_do_not_expose_story_body(client, session_data):
    response = client.get("/api/audit-events", headers=auth_header(session_data))
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] > 0
    assert all(item["category"] in {"story", "family", "privacy", "audio"} for item in payload["items"])
    assert "改后的正文" not in str(payload)

    other = client.post(
        "/api/auth/login",
        json={"phone": "13500004444", "password": "abcdef", "role": "elder"},
    ).json()
    other_events = client.get(
        "/api/audit-events", headers=auth_header(other)
    ).json()
    assert other_events["total"] == 1
    assert other_events["items"][0]["action"] == "consent_granted"


def test_family_suggestions_preserve_elder_confirmation_boundary(client):
    phone = "13600003333"
    elder = client.post(
        "/api/auth/login",
        json={"phone": phone, "password": "abcdef", "role": "elder"},
    ).json()
    elder_headers = auth_header(elder)
    draft = client.post(
        "/api/stories/draft",
        headers=elder_headers,
        data={
            "topicId": "family",
            "durationMs": "1000",
            "consentVersion": str(elder["consentVersion"]),
        },
    ).json()
    story_id = draft["id"]

    family = client.post(
        "/api/auth/login",
        json={"phone": phone, "password": "abcdef", "role": "family"},
    ).json()
    family_headers = auth_header(family)

    elder_cannot_suggest = client.post(
        f"/api/stories/{story_id}/family-notes",
        headers=elder_headers,
        json={
            "kind": "supplement",
            "content": "我还记得院子里有一棵树。",
            "consentVersion": elder["consentVersion"],
        },
    )
    assert elder_cannot_suggest.status_code == 403

    created = client.post(
        f"/api/stories/{story_id}/family-notes",
        headers=family_headers,
        json={
            "kind": "correction",
            "content": "这里的人名建议再和妈妈确认一下。",
            "consentVersion": family["consentVersion"],
        },
    )
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["status"] == "pending"

    family_cannot_confirm = client.post(
        f"/api/stories/{story_id}/confirm",
        headers=family_headers,
        json={"consentVersion": family["consentVersion"]},
    )
    assert family_cannot_confirm.status_code == 403

    unresolved_cannot_confirm = client.post(
        f"/api/stories/{story_id}/confirm",
        headers=elder_headers,
        json={"consentVersion": elder["consentVersion"]},
    )
    assert unresolved_cannot_confirm.status_code == 409

    family_cannot_resolve = client.post(
        f"/api/stories/{story_id}/family-notes/{note['id']}/resolve",
        headers=family_headers,
        json={"action": "accept", "consentVersion": family["consentVersion"]},
    )
    assert family_cannot_resolve.status_code == 403

    listed = client.get(
        f"/api/stories/{story_id}/family-notes", headers=elder_headers
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert client.get(f"/api/stories/{story_id}", headers=elder_headers).json()[
        "familyNoteCount"
    ] == 1

    resolved = client.post(
        f"/api/stories/{story_id}/family-notes/{note['id']}/resolve",
        headers=elder_headers,
        json={"action": "accept", "consentVersion": elder["consentVersion"]},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "accepted"

    confirmed = client.post(
        f"/api/stories/{story_id}/confirm",
        headers=elder_headers,
        json={"consentVersion": elder["consentVersion"]},
    )
    assert confirmed.status_code == 200


def test_revoke_removes_content_and_audio_files(client, session_data):
    from sqlalchemy import select

    from backend.config import settings
    from backend.database import AuditEvent, SessionLocal

    # 先确认撤回前存在内容与文件
    listing = client.get("/api/stories", headers=auth_header(session_data)).json()
    assert listing["total"] > 0
    family_dir = settings.objects_dir / session_data["familyId"]
    files_before = [p for p in family_dir.rglob("*") if p.is_file()]
    assert files_before, "上传的录音应已落盘"

    revoked = client.post("/api/consent/revoke", headers=auth_header(session_data))
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["deletedStories"] > 0

    # 内容清空、录音文件删除
    after = client.get("/api/stories", headers=auth_header(session_data)).json()
    assert after["total"] == 0
    files_after = [p for p in family_dir.rglob("*") if p.is_file()] if family_dir.exists() else []
    assert files_after == [], f"撤回后录音文件必须删除，剩余：{files_after}"

    # 撤回后写操作必须被拒
    denied = client.post(
        "/api/stories/draft",
        headers=auth_header(session_data),
        data={"topicId": "hometown", "durationMs": "1000", "consentVersion": "1"},
    )
    assert denied.status_code == 403

    with SessionLocal() as session:
        events = session.scalars(
            select(AuditEvent).filter_by(family_id=session_data["familyId"])
        ).all()
        assert any(item.action == "consent_revoked" for item in events)
        assert "改后的正文" not in str([item.summary for item in events])
