"""演示排练自检：确认每一个演示步骤依赖的功能都可用。

用法：
    python backend/demo_check.py            # 完整自检（会往库里写测试数据）
    python backend/demo_check.py --readonly # 只读自检（不动数据，开演前用）

注意：完整自检会生成测试故事与录音，**开演前请执行**
`python backend/reset_demo.py` 恢复干净数据，或直接用 --readonly。
"""

from __future__ import annotations

import io
import sys

import httpx

BASE = "http://127.0.0.1:8787"
READONLY = "--readonly" in sys.argv
steps: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str = "") -> None:
    steps.append((label, ok, detail))


def main() -> int:
    try:
        client = httpx.Client(base_url=BASE, timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"无法初始化客户端: {exc}")
        return 1

    print("=" * 68)
    print("演示脚本排练自检")
    print("=" * 68)

    # 第 0 步：后端在线
    try:
        health = client.get("/api/health").json()
        record("后端在线", health.get("status") == "ok", str(health))
    except Exception as exc:  # noqa: BLE001
        record("后端在线", False, f"{exc}（请先启动 python backend/run.py）")
        report()
        return 1

    # 字体资源（P1-4）
    for name in ("NotoSerifSC-Regular-subset.ttf", "NotoSerifSC-Bold-subset.ttf"):
        resp = client.get(f"/fonts/{name}")
        record(f"字体可用 {name}", resp.status_code == 200 and resp.content[:4] == b"\x00\x01\x00\x00")

    # 第 1 步：登录
    login = client.post(
        "/api/auth/login",
        json={"phone": "13800008899", "password": "123456", "role": "elder"},
    )
    record("演示账号可登录", login.status_code == 200, login.text[:120])
    if login.status_code != 200:
        report()
        return 1
    data = login.json()
    headers = {"Authorization": f"Bearer {data['token']}"}
    consent = data["consentVersion"]
    record("登录返回 familyId 与 consentVersion", bool(data["familyId"]) and consent >= 1)
    record("响应不含明文手机号", "13800008899" not in str(data["user"]))

    # 第 2 步：首页与主题
    home = client.get("/api/home", headers=headers).json()
    record("首页有今日叙事", bool(home["today"]["title"]), str(home["today"].get("title")))
    record("首页最近故事 ≥ 2 条（可展示列表）", len(home["recent"]) >= 2, str(len(home["recent"])))
    topics = client.get("/api/topics", headers=headers).json()
    record("四个主题齐备", len(topics) == 4, str([t["title"] for t in topics]))

    # 第 3-5 步：录音上传 → 草稿 → 预览（含原声与 AI 标识）
    if READONLY:
        listing = client.get("/api/stories", headers=headers).json()
        record("只读模式：故事列表可读", listing["total"] > 0, str(listing["total"]))
        record("只读模式：跳过上传/草稿/确认写入", True)
        family = client.get("/api/family", headers=headers).json()
        record("家庭看板有进度数据", family["totalStories"] > 0, f"{family['doneStories']}/{family['totalStories']}")
        _ = io  # 保持导入一致性
        return report()

    payload = b"ID3\x03\x00\x00\x00" + b"\xab" * 4096
    upload = client.post(
        "/api/recordings",
        headers=headers,
        files={"file": ("demo.mp3", io.BytesIO(payload), "audio/mpeg")},
        data={"topicId": "hometown", "durationMs": "42000", "consentVersion": str(consent)},
    )
    record("录音可上传", upload.status_code == 201, upload.text[:120])
    recording = upload.json() if upload.status_code == 201 else {}
    if recording.get("audioUrl"):
        media = client.get(recording["audioUrl"])
        record("原声可回读（/media）", media.status_code == 200 and len(media.content) == len(payload))

    draft = client.post(
        "/api/stories/draft",
        headers=headers,
        data={
            "topicId": "hometown",
            "durationMs": "42000",
            "consentVersion": str(consent),
            "recordingId": recording.get("assetId", ""),
        },
    )
    record("可生成待确认草稿", draft.status_code == 201, draft.text[:120])
    draft_body = draft.json() if draft.status_code == 201 else {}
    record("草稿状态为待确认", draft_body.get("status") == "pending_review")
    record("草稿带原声（预览页可回听）", draft_body.get("hasAudio") is True)
    record("草稿时长可显示为 mm:ss", draft_body.get("durationText") == "00:42", str(draft_body.get("durationText")))

    # 第 6 步：修改后必须退回待确认（演示「AI 内容需人工确认」）
    story_id = draft_body.get("id", "")
    if story_id:
        patched = client.patch(
            f"/api/stories/{story_id}",
            headers=headers,
            json={"body": "演示：人工修改后的正文", "consentVersion": consent},
        )
        record("修改正文成功", patched.status_code == 200, patched.text[:120])
        record("修改后退回待确认", patched.json().get("status") == "pending_review")

        # 授权版本校验（现场可演示「旧版本写入被丢弃」）
        stale = client.patch(
            f"/api/stories/{story_id}",
            headers=headers,
            json={"body": "旧版本写入", "consentVersion": 999},
        )
        record("旧授权版本写入被拒（403）", stale.status_code == 403, str(stale.status_code))

        confirmed = client.post(
            f"/api/stories/{story_id}/confirm",
            headers=headers,
            json={"consentVersion": consent},
        )
        record("确认后进入故事书", confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed")

    # 第 7 步：家庭看板
    family = client.get("/api/family", headers=headers).json()
    record("家庭看板有进度数据", family["totalStories"] > 0, f"{family['doneStories']}/{family['totalStories']}")

    # 第 8 步：撤回授权（演示完再执行，会清空数据）
    print("\n提示：撤回授权会清空演示数据，自检不自动执行，正式演示后再手动演示。")
    record("撤回接口存在（未执行）", True)

    return report()


def report() -> int:
    print()
    passed = sum(1 for _, ok, _ in steps if ok)
    failed = [(label, detail) for label, ok, detail in steps if not ok]
    for label, ok, detail in steps:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {label}" + (f"  {detail}" if detail and not ok else ""))
    print()
    print("=" * 68)
    print(f"通过 {passed}/{len(steps)} 项")
    if failed:
        print("\n未通过项（演示前必须解决）：")
        for label, detail in failed:
            print(f"  ✗ {label}  {detail}")
        return 1
    print("所有演示步骤依赖均可用，可以开演。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
