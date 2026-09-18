"""Exercise task HTTP submission/polling in a disposable database.

python backend/task_http_check.py          # deterministic, no external calls
python backend/task_http_check.py --llm    # one real-model interview prompt
Never writes demo material into the user's business database.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path


def main() -> int:
    use_llm = "--llm" in sys.argv
    logging.disable(logging.CRITICAL)
    if not use_llm:
        os.environ["AGENT_MODE"] = "mock"
        os.environ["LLM_API_KEY"] = ""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="memory-bank-task-http-") as temp:
        os.environ["MEMORY_BANK_DATA_DIR"] = temp
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{Path(temp) / 'check.db'}"
        from fastapi.testclient import TestClient
        from backend.app import create_app
        from backend.agent_service import reset_runtime
        from backend.database import engine
        try:
            with TestClient(create_app()) as client:
                identity = client.post("/api/auth/login", json={"phone": "13800008899", "password": "123456"}).json()
                headers = {"Authorization": f"Bearer {identity['token']}"}
                before = client.get("/api/agent/status", headers=headers).json()
                if use_llm and not before["llmEnabled"]:
                    print("Real-model check requires AGENT_MODE=llm and configured credentials.")
                    return 1
                started = time.monotonic()
                response = client.post("/api/agent/tasks", headers=headers, json={
                    "requestKey": str(uuid.uuid4()), "method": "POST", "path": "/api/agent/interviews",
                    "payload": {"topicId": "school", "consentVersion": identity["consentVersion"]},
                })
                if response.status_code != 202:
                    print(f"Task submission failed: HTTP {response.status_code}")
                    return 1
                task = response.json()
                while task["status"] in {"queued", "running"} and time.monotonic() - started < 300:
                    time.sleep(.2)
                    task = client.get(f"/api/agent/tasks/{task['taskId']}", headers=headers).json()
                after = client.get("/api/agent/status", headers=headers).json()
                result = task.get("result") or {}
                report = {
                    "isolatedDatabase": True, "taskStatus": task["status"],
                    "provider": after["provider"], "llmEnabled": after["llmEnabled"],
                    "fallbackIncrease": after["fallbackCount"] - before["fallbackCount"],
                    "questionPresent": bool(result.get("question")),
                    "narratorMatches": result.get("actor_id") == identity["user"]["id"],
                    "durationSeconds": round(time.monotonic() - started, 2),
                }
                print(json.dumps(report, ensure_ascii=False))
                return 0 if (report["taskStatus"] == "succeeded" and report["questionPresent"]
                             and report["narratorMatches"] and (not use_llm or report["fallbackIncrease"] == 0)) else 1
        finally:
            reset_runtime()
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
