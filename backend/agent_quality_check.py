"""展示真实模型下的抽取与写作质量（同时也是降级情况检查）。"""

from __future__ import annotations

import json

import httpx

BASE = "http://127.0.0.1:8787"

with httpx.Client(base_url=BASE, timeout=120) as client:
    login = client.post(
        "/api/auth/login", json={"phone": "13800008899", "password": "123456", "role": "elder"}
    ).json()
    headers = {"Authorization": f"Bearer {login['token']}"}
    consent = login["consentVersion"]

    status = client.get("/api/agent/status", headers=headers).json()
    print("=== 智能体实现状态 ===")
    print(json.dumps(status, ensure_ascii=False, indent=2))

    started = client.post(
        "/api/agent/interviews",
        headers=headers,
        json={"topicId": "school", "subjectName": "林阿姨", "maxRounds": 2, "consentVersion": consent},
    ).json()
    session_id = started["session_id"]
    question = next(
        (i["value"]["question"] for i in started["interrupts"] if i["value"].get("kind") == "interview"), ""
    )
    print(f"\n=== 采访导演的提问（真实模型）===\n  {question}")

    view = client.post(
        "/api/agent/interviews/answers",
        headers=headers,
        json={
            "sessionId": session_id,
            "answer": "1968年，我在村里的小学读书。教室是土坯房，黑板是木板刷的墨。"
                      "老师姓陈，他每天走十里山路来给我们上课。",
            "finish": True,
            "topicId": "school",
            "consentVersion": consent,
        },
    ).json()

    print(f"\n=== 证据抽取（{len(view['claims'])} 条）===")
    for claim in view["claims"]:
        print(f"  [{claim['element']:<7}] {claim['text']}")
        print(f"            原文：{claim['quote']}")

    print(f"\n=== 七要素完整度 ===")
    print(f"  缺失：{view['missing_fields']}")

    print(f"\n=== 写作结果（审计通过={view['audit_passed']}）===")
    for line in (view["draft_text"] or "").splitlines():
        print(f"  {line}")

    print(f"\n=== 审计发现 ===")
    if view["audit_findings"]:
        for finding in view["audit_findings"]:
            print(f"  [{finding['kind']}] {finding['message']} -> {finding['excerpt']}")
    else:
        print("  无（每句话都有证据支撑）")

    after = client.get("/api/agent/status", headers=headers).json()
    print(f"\n=== 本轮之后降级次数 = {after['fallbackCount']}，最近错误 = {after['lastError']} ===")
