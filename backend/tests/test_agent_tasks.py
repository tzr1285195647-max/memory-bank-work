"""Task transport tests use disposable data, blocked workers and a virtual slow graph."""
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="memory-bank-tasks-")
os.environ["MEMORY_BANK_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path(_TMP) / 'test.db'}"


@pytest.fixture
def client():
    from backend.app import create_app
    with TestClient(create_app()) as client:
        yield client


def login(client, phone="13800008899"):
    response = client.post("/api/auth/login", json={"phone": phone, "password": "123456"}).json()
    return {"Authorization": f"Bearer {response['token']}"}, response


def payload(**kwargs):
    return {"requestKey": str(uuid.uuid4()), "method": "POST", "path": "/api/agent/interviews",
            "payload": {"topicId": "school", "consentVersion": 1}, **kwargs}


def wait_result(client, headers, task_id):
    for _ in range(200):
        response = client.get(f"/api/agent/tasks/{task_id}", headers=headers)
        assert response.status_code == 200, response.text
        if response.json()["status"] not in {"queued", "running"}:
            return response.json()
        time.sleep(.01)
    pytest.fail("worker did not finish")


def test_slow_worker_does_not_hold_http_and_duplicate_submission_is_idempotent(client, monkeypatch):
    from backend import agent_tasks
    entered, release = threading.Event(), threading.Event()
    calls = []

    def dispatch(task, session, auth):
        calls.append(task.id)
        entered.set()
        assert release.wait(5)
        return {"saved": "one-result"}

    monkeypatch.setattr(agent_tasks, "_dispatch", dispatch)
    headers, _ = login(client)
    data = payload()
    try:
        response = client.post("/api/agent/tasks", headers=headers, json=data)
        assert response.status_code == 202
        task_id = response.json()["taskId"]
        assert entered.wait(2)
        assert client.get("/api/health").status_code == 200
        assert client.get(f"/api/agent/tasks/{task_id}", headers=headers).json()["status"] == "running"
        repeated = client.post("/api/agent/tasks", headers=headers, json=data)
        assert repeated.json()["taskId"] == task_id
        changed = {**data, "payload": {"topicId": "work", "consentVersion": 1}}
        assert client.post("/api/agent/tasks", headers=headers, json=changed).status_code == 409
    finally:
        release.set()
    result = wait_result(client, headers, task_id)
    assert result["status"] == "succeeded" and result["result"] == {"saved": "one-result"}
    assert client.post("/api/agent/tasks", headers=headers, json=data).json()["result"] == result["result"]
    assert len(calls) == 1


def test_tasks_require_auth_schema_and_owner_scope(client):
    headers, _ = login(client)
    assert client.post("/api/agent/tasks", json=payload()).status_code == 401
    assert client.post("/api/agent/tasks", headers=headers, json=payload(path="/api/consent/revoke")).status_code == 422
    assert client.post("/api/agent/tasks", headers=headers, json=payload(payload={"topicId": "school"})).status_code == 422
    response = client.post("/api/agent/tasks", headers=headers, json=payload())
    task_id = response.json()["taskId"]
    assert wait_result(client, headers, task_id)["status"] == "succeeded"
    family_headers, _ = login(client, "13700006677")
    assert client.get(f"/api/agent/tasks/{task_id}", headers=family_headers).status_code == 404
    other = client.post("/api/auth/register", json={"phone": "135" + uuid.uuid4().hex[:8].translate(str.maketrans("abcdef", "123456")),
        "password": "abcdef", "displayName": "其他家庭", "role": "elder", "gender": "female", "age": 68}).json()
    other_headers = {"Authorization": f"Bearer {other['token']}"}
    assert client.get(f"/api/agent/tasks/{task_id}", headers=other_headers).status_code == 404


def test_completed_results_survive_restart_but_inflight_writes_are_not_replayed(client):
    from backend.agent_tasks import TaskRunner
    from backend.database import AgentTask, SessionLocal
    headers, identity = login(client)
    result_id, interrupted_id = str(uuid.uuid4()), str(uuid.uuid4())
    with SessionLocal() as session:
        for task_id, state in [(result_id, "succeeded"), (interrupted_id, "running")]:
            session.add(AgentTask(id=task_id, family_id=identity["familyId"], user_id=identity["user"]["id"],
                request_key=task_id, method="POST", path="/api/agent/interviews", payload_json='{}',
                status=state, result_json='{"persisted": true}' if state == "succeeded" else None))
        session.commit()
    runner = TaskRunner()
    try:
        runner.recover()
    finally:
        runner.close()
    assert client.get(f"/api/agent/tasks/{result_id}", headers=headers).json()["result"] == {"persisted": True}
    result = client.get(f"/api/agent/tasks/{interrupted_id}", headers=headers).json()
    assert result["status"] == "interrupted" and result["errorStatus"] == 409


def test_worker_rechecks_membership_after_queueing(client, monkeypatch):
    from backend import agent_tasks
    from backend.database import SessionLocal, User
    entered, release = threading.Event(), threading.Event()
    executed = []

    def dispatch(task, session, auth):
        executed.append(auth.user_id)
        entered.set()
        assert release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(agent_tasks, "_dispatch", dispatch)
    headers, _ = login(client)
    other_headers, other = login(client, "13600005566")
    first_id = client.post("/api/agent/tasks", headers=headers, json=payload()).json()["taskId"]
    try:
        assert entered.wait(2)
        second_id = client.post("/api/agent/tasks", headers=other_headers, json=payload()).json()["taskId"]
        with SessionLocal() as session:
            session.get(User, other["user"]["id"]).active = 0
            session.commit()
        release.set()
        wait_result(client, headers, first_id)
        for _ in range(200):
            from backend.database import AgentTask
            with SessionLocal() as session:
                task = session.get(AgentTask, second_id)
                if task.status == "failed":
                    assert task.error_status == 403
                    break
            time.sleep(.01)
        else:
            pytest.fail("removed user's queued task did not fail")
        assert other["user"]["id"] not in executed
        assert client.get(f"/api/agent/tasks/{second_id}", headers=other_headers).status_code == 403
    finally:
        release.set()
        with SessionLocal() as session:
            session.get(User, other["user"]["id"]).active = 1
            session.commit()


def test_model_failure_keeps_already_confirmed_fragment(client, monkeypatch):
    from backend import api
    headers, identity = login(client)
    recording = client.post("/api/recordings", headers=headers,
        data={"topicId": "school", "durationMs": "1000", "consentVersion": str(identity["consentVersion"])},
        files={"file": ("synthetic-failure.mp3", b"ID3-test-failure", "audio/mpeg")}).json()

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic extraction failure")

    monkeypatch.setattr(api, "extract_recording_evidence", unavailable)
    request = payload(method="PUT", path=f"/api/recordings/{recording['assetId']}/fragment",
        payload={"transcript": "1959年秋天，我第一次上学。", "consentVersion": identity["consentVersion"]})
    submitted = client.post("/api/agent/tasks", headers=headers, json=request).json()
    result = wait_result(client, headers, submitted["taskId"])
    assert result["status"] == "failed"
    fragments = client.get("/api/fragments", headers=headers).json()
    fragment = next(item for item in fragments if item["recordingId"] == recording["assetId"])
    assert fragment["confirmedText"] == request["payload"]["transcript"]
    assert fragment["narratorUserId"] == identity["user"]["id"]


def test_five_call_revision_chain_can_complete_beyond_old_300_second_budget(client, monkeypatch, tmp_path):
    from backend import agent_service
    from backend.agents.mock import MockAgentProvider
    from backend.agents.runtime import AgentRuntime

    class SlowProvider(MockAgentProvider):
        def __init__(self):
            self.calls = []
            self.audits = 0
        def resolve_conflicts(self, **kwargs):
            self.calls.append("conflicts")
            return super().resolve_conflicts(**kwargs)
        def compose_draft(self, **kwargs):
            self.calls.append("write")
            return super().compose_draft(**kwargs)
        def audit_draft(self, **kwargs):
            self.calls.append("audit")
            self.audits += 1
            if self.audits == 1:
                return [{"kind": "unsupported", "message": "测试修订分支", "excerpt": "测试"}]
            return super().audit_draft(**kwargs)

    headers, identity = login(client)
    recording = client.post("/api/recordings", headers=headers,
        data={"topicId": "school", "durationMs": "1000", "consentVersion": str(identity["consentVersion"])},
        files={"file": ("synthetic.mp3", b"ID3-test", "audio/mpeg")}).json()
    recording_id = recording["assetId"]
    confirmed = client.put(f"/api/recordings/{recording_id}/fragment", headers=headers,
        json={"transcript": "1959年秋天，我去村里的学校上学。", "consentVersion": identity["consentVersion"]})
    assert confirmed.status_code == 200
    provider = SlowProvider()
    runtime = AgentRuntime(tmp_path / "slow-checkpoint.sqlite", provider=provider)
    monkeypatch.setattr(agent_service, "_runtime", runtime)
    try:
        request = payload(path="/api/agent/fragments/generate", payload={"recordingIds": [recording_id],
            "topicId": "school", "style": "raw", "consentVersion": identity["consentVersion"]})
        response = client.post("/api/agent/tasks", headers=headers, json=request)
        assert response.status_code == 202
        result = wait_result(client, headers, response.json()["taskId"])
        assert result["status"] == "succeeded", result
        assert provider.calls == ["conflicts", "write", "audit", "write", "audit"]
        assert len(provider.calls) * 65 > 300  # one 60s timeout + 5s successful retry per call
        assert result["result"]["status"] == "pending_review"
        assert result["result"]["narratorUserId"] == identity["user"]["id"]
    finally:
        runtime.close()
