from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from memory_bank.api import create_app
from memory_bank.config import Settings


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        root_dir=tmp_path,
        data_dir=tmp_path,
        database_path=tmp_path / "memory.sqlite3",
        checkpoint_path=tmp_path / "checkpoint.sqlite3",
        mode="mock",
    )
    return TestClient(create_app(settings))


def create_project(client: TestClient, max_rounds: int = 3) -> dict:
    response = client.post(
        "/api/projects",
        json={
            "subject_name": "外婆",
            "topic": "童年的夏天",
            "consent_by": "外婆本人",
            "max_rounds": max_rounds,
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["project"]["stage"] == "interview"
    assert data["workflow"]["interrupts"][0]["value"]["kind"] == "interview"
    return data


def test_complete_evidence_grounded_flow_and_restart(tmp_path: Path):
    client = make_client(tmp_path)
    data = create_project(client)
    project_id = data["project"]["id"]

    first = client.post(
        f"/api/projects/{project_id}/respond",
        json={"answer": "我们小时候常在河边的大槐树下乘凉，外婆会带一把蒲扇。", "finish": False},
    )
    assert first.status_code == 200, first.text
    assert first.json()["workflow"]["interrupts"][0]["value"]["kind"] == "interview"

    second = client.post(
        f"/api/projects/{project_id}/respond",
        json={"answer": "傍晚蝉声最大，表兄妹会把西瓜放进井水里冰着。", "finish": True},
    )
    assert second.status_code == 200, second.text
    review = second.json()
    assert review["project"]["stage"] == "review"
    assert review["workflow"]["interrupts"][0]["value"]["kind"] == "review"
    assert len(review["claims"]) >= 2
    assert "〔证据:" in review["draft"]["content"]
    assert review["workflow"]["state"]["audit_findings"] == []

    approved = client.post(f"/api/projects/{project_id}/review", json={"action": "approve"})
    assert approved.status_code == 200, approved.text
    final = approved.json()
    assert final["project"]["stage"] == "delivered"
    assert final["delivery"] is not None
    assert "经过人工确认" in final["delivery"]["content"]

    restarted_client = make_client(tmp_path)
    restored = restarted_client.get(f"/api/projects/{project_id}")
    assert restored.status_code == 200
    assert restored.json()["project"]["stage"] == "delivered"
    assert restored.json()["delivery"]["id"] == final["delivery"]["id"]


def test_restart_resumes_pending_interview_interrupt(tmp_path: Path):
    first_client = make_client(tmp_path)
    data = create_project(first_client, max_rounds=1)
    project_id = data["project"]["id"]

    restarted_client = make_client(tmp_path)
    pending = restarted_client.get(f"/api/projects/{project_id}")
    assert pending.status_code == 200
    assert pending.json()["workflow"]["interrupts"][0]["value"]["kind"] == "interview"

    resumed = restarted_client.post(
        f"/api/projects/{project_id}/respond",
        json={"answer": "院子中央有一棵很高的梧桐树。", "finish": True},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["project"]["stage"] == "review"
    assert resumed.json()["workflow"]["interrupts"][0]["value"]["kind"] == "review"


def test_revoke_invalidates_consent_and_deletes_content(tmp_path: Path):
    client = make_client(tmp_path)
    data = create_project(client, max_rounds=2)
    project_id = data["project"]["id"]

    response = client.post(
        f"/api/projects/{project_id}/respond",
        json={"answer": "那年全家住在山脚下。", "finish": False},
    )
    assert response.status_code == 200
    assert len(response.json()["turns"]) == 1

    revoked = client.post(
        f"/api/projects/{project_id}/revoke",
        json={"reason": "测试撤回"},
    )
    assert revoked.status_code == 200, revoked.text
    result = revoked.json()
    assert result["project"]["stage"] == "revoked"
    assert result["project"]["revoked"] == 1
    assert result["turns"] == []
    assert result["claims"] == []
    assert result["draft"] is None
    assert any(event["event_type"] == "consent.revoked_and_content_deleted" for event in result["events"])

    blocked = client.post(
        f"/api/projects/{project_id}/respond",
        json={"answer": "不应再写入", "finish": True},
    )
    assert blocked.status_code == 409


def test_unsupported_edit_routes_back_to_interview(tmp_path: Path):
    client = make_client(tmp_path)
    data = create_project(client, max_rounds=1)
    project_id = data["project"]["id"]
    review = client.post(
        f"/api/projects/{project_id}/respond",
        json={"answer": "父亲在院子里种过一棵石榴树。", "finish": True},
    ).json()
    edited = review["draft"]["content"] + "\n\n后来他还独自去过南极。"

    response = client.post(
        f"/api/projects/{project_id}/review",
        json={"action": "edit", "edited_text": edited},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["project"]["stage"] == "interview"
    assert result["workflow"]["interrupts"][0]["value"]["kind"] == "interview"
    assert result["workflow"]["state"]["audit_findings"]
