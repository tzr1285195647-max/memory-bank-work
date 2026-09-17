"""后端契约自测：登录 -> 主题 -> 上传录音 -> 生成草稿 -> 列表 -> 确认 -> 家庭 -> 撤回。

用 httpx 直接打本机 8787，验证小程序将要调用的每个接口。
"""

from __future__ import annotations

import io
import sys

import httpx

BASE = "http://127.0.0.1:8787"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}  {detail}")


with httpx.Client(base_url=BASE, timeout=20) as client:
    print("=== 1. 健康检查 ===")
    health = client.get("/api/health").json()
    check("GET /api/health", health.get("status") == "ok", str(health))

    print("\n=== 2. 登录（演示账号 13800008899 / 123456）===")
    resp = client.post(
        "/api/auth/login",
        json={"phone": "13800008899", "password": "123456", "role": "elder"},
    )
    check("POST /api/auth/login 200", resp.status_code == 200, resp.text[:200])
    login = resp.json()
    token = login.get("token", "")
    family_id = login.get("familyId", "")
    consent_version = login.get("consentVersion", 1)
    check("返回 token", bool(token))
    check("返回 familyId", bool(family_id))
    check("返回 consentVersion", consent_version >= 1, str(consent_version))
    check("手机号已脱敏", login["user"]["phoneMasked"] == "138****8899", login["user"]["phoneMasked"])

    auth = {"Authorization": f"Bearer {token}"}

    print("\n=== 3. 未授权访问必须被拒 ===")
    check("无 token 访问 /api/stories -> 401", client.get("/api/stories").status_code == 401)

    print("\n=== 4. 主题列表 ===")
    topics = client.get("/api/topics", headers=auth).json()
    check("GET /api/topics 包含 4 个推荐主题", {"hometown", "school", "work", "family"}.issubset({t["id"] for t in topics}), str(len(topics)))
    check("主题含「我的家乡」", any(t["title"] == "我的家乡" for t in topics))

    print("\n=== 5. 上传录音 ===")
    fake_audio = b"ID3\x03\x00\x00\x00" + b"\x00" * 2048  # 伪 mp3 载荷，仅验证落盘与契约
    resp = client.post(
        "/api/recordings",
        headers=auth,
        files={"file": ("test.mp3", io.BytesIO(fake_audio), "audio/mpeg")},
        data={"topicId": "hometown", "durationMs": "12000", "consentVersion": str(consent_version)},
    )
    check("POST /api/recordings 201", resp.status_code == 201, resp.text[:200])
    recording = resp.json() if resp.status_code == 201 else {}
    check("返回 assetId", bool(recording.get("assetId")))
    check("返回 audioUrl", str(recording.get("audioUrl", "")).startswith("/media/"))

    if recording.get("audioUrl"):
        media = client.get(recording["audioUrl"])
        check("录音文件可通过 /media 读取", media.status_code == 200 and len(media.content) == len(fake_audio))

    print("\n=== 6. 生成待确认草稿 ===")
    resp = client.post(
        "/api/stories/draft",
        headers=auth,
        data={
            "topicId": "hometown",
            "durationMs": "12000",
            "consentVersion": str(consent_version),
            "recordingId": recording.get("assetId", ""),
        },
    )
    check("POST /api/stories/draft 201", resp.status_code == 201, resp.text[:200])
    draft = resp.json() if resp.status_code == 201 else {}
    check("草稿状态为 pending_review", draft.get("status") == "pending_review", str(draft.get("status")))
    check("草稿带原声", draft.get("hasAudio") is True)

    print("\n=== 7. 故事列表与详情 ===")
    listing = client.get("/api/stories", headers=auth).json()
    check("GET /api/stories 有数据", listing.get("total", 0) >= 4, str(listing.get("total")))
    check("列表项带序号", listing["items"][0]["index"] == "01", listing["items"][0]["index"])
    story_id = draft.get("id") or listing["items"][0]["id"]
    detail = client.get(f"/api/stories/{story_id}", headers=auth).json()
    check("GET /api/stories/{id}", detail.get("id") == story_id)

    print("\n=== 8. 修改与确认（写路径需校验授权版本）===")
    bad = client.patch(
        f"/api/stories/{story_id}",
        headers=auth,
        json={"body": "改过的内容", "consentVersion": 999},
    )
    check("错误授权版本被拒 403", bad.status_code == 403, str(bad.status_code))
    ok = client.patch(
        f"/api/stories/{story_id}",
        headers=auth,
        json={"body": "那年秋天，院子里的桂花开得很早。", "consentVersion": consent_version},
    )
    check("PATCH 成功", ok.status_code == 200, ok.text[:200])
    check("修改后状态回到 pending_review", ok.json().get("status") == "pending_review")
    confirm = client.post(
        f"/api/stories/{story_id}/confirm",
        headers=auth,
        json={"consentVersion": consent_version},
    )
    check("确认后状态为 confirmed", confirm.status_code == 200 and confirm.json()["status"] == "confirmed")

    print("\n=== 9. 首页、家庭看板、个人中心 ===")
    home = client.get("/api/home", headers=auth).json()
    check("GET /api/home 返回今日叙事", bool(home["today"]["title"]), str(home["today"]))
    check("GET /api/home 返回最近故事", isinstance(home["recent"], list))
    family = client.get("/api/family", headers=auth).json()
    check("GET /api/family 有成员与进度", family["totalStories"] > 0, str(family))
    check("已确认数 <= 总数", family["doneStories"] <= family["totalStories"])
    me = client.get("/api/me", headers=auth).json()
    check("GET /api/me 返回统计", me["stats"]["storyCount"] >= 1, str(me["stats"]))
    check("个人中心手机号脱敏", me["phoneMasked"] == "138****8899")

print("\n" + "=" * 60)
print(f"通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
