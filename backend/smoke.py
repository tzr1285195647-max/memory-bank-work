"""冒烟检查：登录后打印故事列表，确认演示数据已就绪。"""

from __future__ import annotations

import httpx

with httpx.Client(base_url="http://127.0.0.1:8787", timeout=20) as client:
    login = client.post(
        "/api/auth/login",
        json={"phone": "13800008899", "password": "123456", "role": "elder"},
    ).json()
    headers = {"Authorization": f"Bearer {login['token']}"}
    print("登录 OK  familyId:", login["familyId"][:8], " consentVersion:", login["consentVersion"])
    stories = client.get("/api/stories", headers=headers).json()
    print("故事数:", stories["total"])
    for story in stories["items"]:
        print(
            f"  {story['index']} {story['title']}  {story['durationText']} · "
            f"{story['mode']}  [{story['status']}]"
        )
    home = client.get("/api/home", headers=headers).json()
    print("首页今日叙事:", home["today"]["title"], "| 最近:", [s["title"] for s in home["recent"]])
    family = client.get("/api/family", headers=headers).json()
    print("家庭看板:", family["doneStories"], "/", family["totalStories"], "个故事")
