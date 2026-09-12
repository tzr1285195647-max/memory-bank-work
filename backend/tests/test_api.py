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
        json={"body": "改后的正文", "consentVersion": session_data["consentVersion"]},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "pending_review"

    confirmed = client.post(
        f"/api/stories/{story_id}/confirm",
        headers=auth_header(session_data),
        json={"consentVersion": session_data["consentVersion"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


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


def test_revoke_removes_content_and_audio_files(client, session_data):
    from backend.config import settings

    # 先确认撤回前存在内容与文件
    listing = client.get("/api/stories", headers=auth_header(session_data)).json()
    assert listing["total"] > 0
    files_before = [p for p in settings.objects_dir.rglob("*") if p.is_file()]
    assert files_before, "上传的录音应已落盘"

    revoked = client.post("/api/consent/revoke", headers=auth_header(session_data))
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["deletedStories"] > 0

    # 内容清空、录音文件删除
    after = client.get("/api/stories", headers=auth_header(session_data)).json()
    assert after["total"] == 0
    files_after = [p for p in settings.objects_dir.rglob("*") if p.is_file()]
    assert files_after == [], f"撤回后录音文件必须删除，剩余：{files_after}"

    # 撤回后写操作必须被拒
    denied = client.post(
        "/api/stories/draft",
        headers=auth_header(session_data),
        data={"topicId": "hometown", "durationMs": "1000", "consentVersion": "1"},
    )
    assert denied.status_code == 403
