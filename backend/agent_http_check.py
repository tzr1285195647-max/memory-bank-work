"""HTTP 端到端验证：多智能体流程通过接口跑通。

用法：python backend/agent_http_check.py
"""

from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8787"
passed = failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}  {detail}")


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=30)
    print("=" * 70)
    print("多智能体 HTTP 端到端验证")
    print("=" * 70)

    login = client.post(
        "/api/auth/login", json={"phone": "13800008899", "password": "123456", "role": "elder"}
    ).json()
    auth = {"Authorization": f"Bearer {login['token']}"}
    consent = login["consentVersion"]
    print(f"\n登录 OK  familyId={login['familyId'][:8]}  consentVersion={consent}")

    print("\n=== 1. 开启采访（采访导演提第一个问题）===")
    started = client.post(
        "/api/agent/interviews",
        headers=auth,
        json={"topicId": "hometown", "subjectName": "林阿姨", "maxRounds": 3, "consentVersion": consent},
    )
    check("POST /api/agent/interviews 201", started.status_code == 201, started.text[:200])
    view = started.json()
    session_id = view["session_id"]
    questions = [i["value"].get("question") for i in view.get("interrupts", []) if i["value"].get("kind") == "interview"]
    check("返回采访问题", bool(questions and questions[0]), str(view.get("interrupts"))[:120])
    print(f"  问题：{questions[0] if questions else '(无)'}")

    print("\n=== 2. 逐轮作答（每轮都会并行抽取证据）===")
    answers = [
        "那年秋天，院子里的桂花开得很早。我和妹妹每天放学都绕路去看一眼。",
        "外婆总是在门口等我们，她会把落下来的桂花扫成一堆。",
    ]
    for index, text in enumerate(answers, 1):
        resp = client.post(
            "/api/agent/interviews/answers",
            headers=auth,
            json={
                "sessionId": session_id,
                "answer": text,
                "finish": False,
                "topicId": "hometown",
                "consentVersion": consent,
            },
        )
        check(f"第 {index} 轮提交 200", resp.status_code == 200, resp.text[:200])
        view = resp.json()
        print(
            f"  轮次={len(view['claims']) and len(view.get('turns', []))} "
            f"证据={len(view['claims'])} 缺失={view['missing_fields']} stage={view['stage']}"
        )

    print("\n=== 3. 结束采访（finish=true 触发写作与审计）===")
    resp = client.post(
        "/api/agent/interviews/answers",
        headers=auth,
        json={
            "sessionId": session_id,
            "answer": "后来外婆走了，我每次闻到桂花香，心里都特别想念她。",
            "finish": True,
            "topicId": "hometown",
            "consentVersion": consent,
        },
    )
    view = resp.json()
    check("进入确认点", view["stage"] == "review", str(view.get("stage")))
    check("产出草稿", bool(view.get("draft_text")), "")
    check("审计通过", view.get("audit_passed") is True, str(view.get("audit_findings"))[:120])
    check("草稿已落库为待确认故事", bool(view.get("storyId")), str(view.get("storyId")))
    print(f"  草稿：\n{view.get('draft_text', '')[:160]}")

    print("\n=== 4. 证据链（每条事实可回指原句）===")
    claims = view.get("claims", [])
    check("有证据", len(claims) > 0, str(len(claims)))
    for claim in claims[:4]:
        turn = next((t for t in view.get("turns", []) if t["id"] == claim["turn_id"]), None)
        in_turn = bool(turn) and claim["quote"] in turn["answer"]
        check(f"「{claim['text'][:14]}…」的原文可追溯", in_turn, f"turn={claim.get('turn_id')}")

    print("\n=== 5. 改写引入无证据内容 → 必须被拒 ===")
    blocked = client.post(
        "/api/agent/interviews/review",
        headers=auth,
        json={
            "sessionId": session_id,
            "action": "edit",
            "editedText": "他后来去了北京，再也没有回来，那年他二十五岁。",
            "consentVersion": consent,
        },
    )
    check("无证据改写被拒 403", blocked.status_code == 403, f"HTTP {blocked.status_code} {blocked.text[:160]}")

    print("\n=== 6. 改写被拒后流程退回采访；此时发确认动作应被明确拒绝 ===")
    wrong_state = client.post(
        "/api/agent/interviews/review",
        headers=auth,
        json={"sessionId": session_id, "action": "approve", "consentVersion": consent},
    )
    check(
        "非确认点调用确认动作被拒（409）",
        wrong_state.status_code == 409,
        f"HTTP {wrong_state.status_code} {wrong_state.text[:140]}",
    )

    # 另起会话：走一遍干净的"采访 → 草稿 → 批准 → 交付"完整流程
    fresh = client.post(
        "/api/agent/interviews",
        headers=auth,
        json={"topicId": "hometown", "subjectName": "林阿姨", "maxRounds": 1, "consentVersion": consent},
    ).json()
    fresh_id = fresh["session_id"]
    fresh_view = client.post(
        "/api/agent/interviews/answers",
        headers=auth,
        json={
            "sessionId": fresh_id,
            "answer": "那年秋天，院子里的桂花开得很早。我和妹妹每天放学都绕路去看一眼。",
            "finish": True,
            "topicId": "hometown",
            "consentVersion": consent,
        },
    ).json()
    check("新会话产出草稿", bool(fresh_view.get("draft_text")), "")
    approved = client.post(
        "/api/agent/interviews/review",
        headers=auth,
        json={"sessionId": fresh_id, "action": "approve", "consentVersion": consent},
    )
    check("干净草稿批准成功", approved.status_code == 200, approved.text[:160])
    delivered = approved.json()
    check("产生交付内容", bool(delivered.get("delivery")), str(delivered.get("stage")))
    check("交付内容标注人工确认", "经过人工确认" in str(delivered.get("delivery")), "")
    story_id = fresh_view.get("storyId")
    if story_id:
        detail = client.get(f"/api/stories/{story_id}", headers=auth).json()
        print(f"  故事状态={detail.get('status')}  正文前 40 字={detail.get('body', '')[:40]}")

    print("\n=== 7. 故事书里的确认：无证据内容不得发布 ===")
    if story_id:
        bad = client.post(
            f"/api/agent/stories/{story_id}/review",
            headers=auth,
            json={"body": "这段内容完全是编造的，没有任何讲述依据。", "consentVersion": consent},
        )
        check("无证据正文被拒（403/409）", bad.status_code in {403, 409}, f"HTTP {bad.status_code}")

        good = client.post(
            f"/api/agent/stories/{story_id}/review",
            headers=auth,
            json={"body": detail.get("body", ""), "consentVersion": consent},
        )
        check("有证据正文可确认", good.status_code == 200, good.text[:200])

    print("\n=== 8. 停止意愿 ===")
    started2 = client.post(
        "/api/agent/interviews",
        headers=auth,
        json={"topicId": "school", "subjectName": "林阿姨", "maxRounds": 4, "consentVersion": consent},
    ).json()
    session2 = started2["session_id"]
    client.post(
        "/api/agent/interviews/answers",
        headers=auth,
        json={
            "sessionId": session2,
            "answer": "那年秋天，院子里的桂花开得很早。",
            "finish": False,
            "topicId": "school",
            "consentVersion": consent,
        },
    )
    stopped = client.post(
        "/api/agent/interviews/answers",
        headers=auth,
        json={
            "sessionId": session2,
            "answer": "算了，我不想讲了，今天就到这儿。",
            "finish": False,
            "topicId": "school",
            "consentVersion": consent,
        },
    ).json()
    check("识别停止意愿", stopped.get("stop_requested") is True, str(stopped.get("stop_requested")))
    check(
        "停止后不再追问",
        all(i["value"].get("kind") != "interview" for i in stopped.get("interrupts", [])),
        str(stopped.get("interrupts"))[:160],
    )

    print("\n" + "=" * 70)
    print(f"通过 {passed} 项，失败 {failed} 项")
    client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
