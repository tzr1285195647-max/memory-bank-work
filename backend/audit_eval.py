"""审计拦截率评测（可信机制量化基准）。

对一份已确认故事构造 50 条编辑（25 条事实篡改 + 25 条正常润色），
逐条送入审计闸门，报告拦截率与误拦率。

用法：
    python backend/audit_eval.py local        # 本地规则层（无需服务与密钥）
    python backend/audit_eval.py prepare-db   # 向本机演示库插入评测故事（供 llm 模式，先停后端）
    python backend/audit_eval.py llm          # 驱动运行中的后端复审接口（需 AGENT_MODE=llm 与密钥）

判定标准（与产品规则一致）：
    篡改类编辑引入任何证据都不支持的事实 -> 审计应当拦截
    润色类编辑不引入新事实、逐句可追溯   -> 审计应当放行
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------- 固定装置
# 口述原文（已确认碎片，同一讲述者）——claims 的 quote 必须是这些句子的真子串
NARRATOR_LINES = [
    "那是1965年的秋天，稻子熟得很晚。",
    "家里收了稻谷，我跟着母亲去村口的晒谷场。",
    "天暗下来，眼看着要下雨。",
    "母亲拿起木耙把谷子往一块拢，我跟在后面帮忙。",
    "那年我十二岁，第一次觉得大人的活计这么累。",
    "晒好的谷子装进了麻袋，母亲说今年能换两担新米。",
]

CLAIMS = [
    {"id": "c1", "element": "time",   "text": "1965年的秋天",           "quote": NARRATOR_LINES[0]},
    {"id": "c2", "element": "place",  "text": "村口的晒谷场",           "quote": NARRATOR_LINES[1]},
    {"id": "c3", "element": "people", "text": "我、母亲",               "quote": NARRATOR_LINES[1]},
    {"id": "c4", "element": "event",  "text": "家里收了稻谷",           "quote": NARRATOR_LINES[1]},
    {"id": "c5", "element": "event",  "text": "天暗下来，眼看着要下雨",   "quote": NARRATOR_LINES[2]},
    {"id": "c6", "element": "event",  "text": "母亲拿起木耙把谷子往一块拢", "quote": NARRATOR_LINES[3]},
    {"id": "c7", "element": "feeling", "text": "第一次觉得大人的活计这么累", "quote": NARRATOR_LINES[4]},
    {"id": "c8", "element": "event",  "text": "晒好的谷子装进了麻袋",    "quote": NARRATOR_LINES[5]},
    {"id": "c9", "element": "impact", "text": "今年能换两担新米",        "quote": NARRATOR_LINES[5]},
]

TITLE = "《晒谷的秋天》"
SOURCE_NOTE = "——根据林奶奶确认过的口述原文。"
ORIGINAL_BODY = "\n".join([
    TITLE, SOURCE_NOTE,
    NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
    NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
])

L_TITLE, L_NOTE, L1, L2, L3, L4, L5, L6 = 0, 1, 2, 3, 4, 5, 6, 7

# ---------------------------------------------------------------- 50 条编辑
# (编号, 类别, 变体说明, 变体正文)。篡改 = kind "tamper"；润色 = kind "benign"。
EDITS: list[dict] = []


def _edit(eid: str, category: str, kind: str, note: str, body: list[str]) -> None:
    EDITS.append({"id": eid, "category": category, "kind": kind, "note": note,
                  "body": "\n".join(body)})


# —— 篡改：篡改年份/时间（5）
_edit("T01", "篡改年份", "tamper", "1965 改为 1966",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0].replace("1965", "1966"), NARRATOR_LINES[1],
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("T02", "篡改年份", "tamper", "1965 改为 1958",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0].replace("1965", "1958"), NARRATOR_LINES[1],
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("T03", "篡改年份", "tamper", "新增 1970 年翻修情节",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "到了1970年，晒谷场翻修成了水泥地。"])
_edit("T04", "篡改年龄", "tamper", "十二岁改为十四岁",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4].replace("十二岁", "十四岁"), NARRATOR_LINES[5]])
_edit("T05", "篡改年份", "tamper", "新增 1967 年再去情节",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "1967年开春，我们又去了一次晒谷场。"])

# —— 篡改：编造人物（5）
_edit("T06", "编造人物", "tamper", "新增王婶帮忙",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "隔壁的王婶也赶过来帮忙。"])
_edit("T07", "编造人物", "tamper", "新增父亲从县里回来",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "父亲从县里赶回来收稻子。"])
_edit("T08", "编造人物", "tamper", "新增妹妹拍手笑",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "妹妹蹲在旁边拍手笑。"])
_edit("T09", "编造人物", "tamper", "句内插入三姑（长句掩护）",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0],
       NARRATOR_LINES[1].replace("我跟着母亲去", "我跟着母亲和三姑去"),
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("T10", "编造人物", "tamper", "新增陈老师路过",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "村里的陈老师正好路过，帮我们撑开了麻袋。"])

# —— 篡改：编造地点（4）
_edit("T11", "编造地点", "tamper", "新增祠堂门口",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "晒谷场就在祠堂门口的空地上。"])
_edit("T12", "编造地点", "tamper", "晒谷场改为学校操场（长句掩护）",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0],
       "我跟着母亲去学校操场上的晒谷场。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("T13", "编造地点", "tamper", "新增粮站晾晒",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "后来谷子又运到镇上的粮站去晾晒。"])
_edit("T14", "编造地点", "tamper", "新增老井",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "晒谷场边上还有一口老井，大家打水喝。"])

# —— 篡改：编造事件经过（6）
_edit("T15", "编造事件", "tamper", "新增轰麻雀",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "我们还轰走了一群来偷吃的麻雀。"])
_edit("T16", "编造事件", "tamper", "新增落冰雹",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "说到一半，天上竟然落了冰雹。"])
_edit("T17", "编造事件", "tamper", "新增磨出血泡",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       "母亲的手被木耙磨出了血泡，还在把谷子往一块拢。",
       NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("T18", "编造事件", "tamper", "新增当晚吃新米饭、父亲喝酒",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "当晚全家煮了新米饭，父亲多喝了一碗酒。"])
_edit("T19", "编造事件", "tamper", "新增晒到腊月",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "那批谷子一直晒到腊月才入仓。"])
_edit("T20", "编造事件", "tamper", "新增记双倍工分",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "生产队还给我们记了双倍工分。"])

# —— 篡改：编造感受/评价（5）
_edit("T21", "编造感受", "tamper", "新增一生中最快乐的一天",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "那是我一生中最快乐的一天。"])
_edit("T22", "编造感受", "tamper", "新增爱上农村生活",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "从那以后，我爱上了农村的生活。"])
_edit("T23", "编造感受", "tamper", "新增最苦的岁月",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "现在回想，那是最苦的岁月。"])
_edit("T24", "编造感受", "tamper", "新增桂花香",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "我至今记得那天巷子里的桂花香。"])
_edit("T25", "编造感受", "tamper", "新增教会吃苦耐劳",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
       "那段日子教会了我吃苦耐劳。"])

# —— 润色：同义微调（5）
_edit("B01", "同义微调", "benign", "熟得很晚 -> 熟得很迟",
      [TITLE, SOURCE_NOTE, "那是1965年的秋天，稻子熟得很迟。",
       NARRATOR_LINES[1], NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B02", "同义微调", "benign", "跟着母亲去 -> 跟着母亲一块去",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "家里收了稻谷，我跟着母亲一块去村口的晒谷场。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B03", "同义微调", "benign", "装进了麻袋 -> 装进了麻袋里",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], "晒好的谷子装进了麻袋里，母亲说今年能换两担新米。"])
_edit("B04", "同义微调", "benign", "句尾补出去帮忙语义（保留原文片段）",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "我跟着母亲去村口的晒谷场帮忙。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B05", "同义微调", "benign", "第一次 -> 头一回",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], "那年我十二岁，头一回觉得大人的活计这么累。", NARRATOR_LINES[5]])

# —— 润色：语序调整（5）
_edit("B06", "语序调整", "benign", "感受与年龄句内换位",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], "第一次觉得大人的活计这么累——那年我十二岁。", NARRATOR_LINES[5]])
_edit("B07", "语序调整", "benign", "收稻谷与晒谷场分句换位",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "家里收了稻谷，村口的晒谷场，我跟着母亲去了。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B08", "语序调整", "benign", "下雨与天暗换位",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1],
       "眼看着要下雨，天暗下来了。", NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B09", "语序调整", "benign", "秋天与熟得晚换位",
      ["《晒谷的秋天》", SOURCE_NOTE, "稻子熟得很晚，那是1965年的秋天。", NARRATOR_LINES[1],
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B10", "语序调整", "benign", "木耙句拆成三个短句",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       "母亲拿起木耙，把谷子往一块拢，我跟在后面帮忙。",
       NARRATOR_LINES[4], NARRATOR_LINES[5]])

# —— 润色：拆分与合并（5）
_edit("B11", "拆分合并", "benign", "收稻谷句拆两行",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "家里收了稻谷。", "我跟着母亲去村口的晒谷场。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B12", "拆分合并", "benign", "下雨句与拢谷句合并",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1],
       "天暗下来，眼看着要下雨，母亲拿起木耙把谷子往一块拢，我跟在后面帮忙。",
       NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B13", "拆分合并", "benign", "装袋句拆两行",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], "晒好的谷子装进了麻袋。", "母亲说今年能换两担新米。"])
_edit("B14", "拆分合并", "benign", "年龄句拆两行",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], "那年我十二岁。", "第一次觉得大人的活计这么累。", NARRATOR_LINES[5]])
_edit("B15", "拆分合并", "benign", "秋天句与收稻谷句合并",
      [TITLE, SOURCE_NOTE, "那是1965年的秋天，稻子熟得很晚，家里收了稻谷。",
       "我跟着母亲去村口的晒谷场。", NARRATOR_LINES[2], NARRATOR_LINES[3],
       NARRATOR_LINES[4], NARRATOR_LINES[5]])

# —— 润色：过渡语气（4，含预期会被规则层误拦的短过渡语样本）
_edit("B16", "过渡语气", "benign", "句首加那时候（短过渡语）",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "那时候，家里收了稻谷，我跟着母亲去村口的晒谷场。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B17", "过渡语气", "benign", "下雨句直接接拢谷句",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1],
       "眼看着要下雨，母亲拿起木耙把谷子往一块拢，我跟在后面帮忙。",
       NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B18", "过渡语气", "benign", "第一次 -> 第一回（换位句式）",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], "第一回觉得大人的活计这么累，那年我十二岁。", NARRATOR_LINES[5]])
_edit("B19", "过渡语气", "benign", "下雨句接等着拢谷",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1],
       "天暗下来，眼看着要下雨，谷子等着往一块拢。",
       NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])

# —— 润色：摘要复述（6）
_edit("B20", "摘要复述", "benign", "跟着母亲拢谷子",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "我跟着母亲，把谷子往一块拢。",
       NARRATOR_LINES[2], "那年我十二岁，第一次觉得大人的活计这么累。", NARRATOR_LINES[5]])
_edit("B21", "摘要复述", "benign", "母亲把谷子往一块拢",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1],
       "天暗下来，母亲把谷子往一块拢。", NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B22", "摘要复述", "benign", "收稻谷与装袋合并复述",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], "家里收了稻谷，晒好的谷子装进了麻袋。",
       NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4],
       "母亲说今年能换两担新米。"])
_edit("B23", "摘要复述", "benign", "年龄与帮忙合并复述",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       "那年我十二岁，跟在母亲后面帮忙。",
       "第一次觉得大人的活计这么累。", NARRATOR_LINES[5]])
_edit("B24", "摘要复述", "benign", "熟得晚与收稻谷合并复述",
      [TITLE, SOURCE_NOTE, "稻子熟得很晚，家里收了稻谷。",
       NARRATOR_LINES[1], NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5]])
_edit("B25", "摘要复述", "benign", "母亲的话原样保留",
      [TITLE, SOURCE_NOTE, NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
       NARRATOR_LINES[3], NARRATOR_LINES[4], "母亲说今年能换两担新米。"])

# ---------------------------------------------------------------- 本地规则层
def run_local() -> list[dict]:
    from backend.evidence_audit import audit_text

    results = []
    for item in EDITS:
        findings = audit_text(body=item["body"], claims=CLAIMS)
        blocked = any(f.get("kind") == "unsupported" for f in findings)
        expected_block = item["kind"] == "tamper"
        results.append({
            **item,
            "blocked": blocked,
            "expected_block": expected_block,
            "correct": blocked == expected_block,
            "finding_count": len(findings),
            "excerpts": [f.get("excerpt", "") for f in findings[:3]],
        })
    return results


# ---------------------------------------------------------------- LLM 层
def prepare_db() -> None:
    """向本机演示库插入评测故事（先停止后端）。供 llm 模式复审使用。"""
    import uuid

    from backend.database import DEMO_PHONE, SessionLocal, init_database
    from backend.models import Membership, Story, User

    init_database()
    with SessionLocal() as session:
        demo = session.query(User).filter_by(phone=DEMO_PHONE).one()
        family_id = session.query(Membership).filter_by(user_id=demo.id).one().family_id
        existing = session.query(Story).filter_by(family_id=family_id, title="晒谷的秋天").one_or_none()
        if existing:
            session.delete(existing)
            session.commit()
        story = Story(
            id=str(uuid.uuid4()), family_id=family_id, topic_id="hometown",
            title="晒谷的秋天", body=ORIGINAL_BODY, mode="原味口述",
            status="pending_review", duration_ms=0, sort_order=99,
            memory_year=1965, life_stage="童年",
        )
        story.claims_json = json.dumps(
            [{k: v for k, v in c.items() if k != "id"} | {"id": c["id"]} for c in CLAIMS],
            ensure_ascii=False)
        story.conflicts_json = "[]"
        story.findings_json = "[]"
        story.audit_passed = 0
        story.session_id = "eval-audit-gate-1"
        session.add(story)
        session.commit()
        print("评测故事已插入：id=%s family=%s" % (story.id, family_id))


def run_llm(base_url: str) -> list[dict]:
    """驱动运行中的后端（AGENT_MODE=llm）：逐条走 /stories/{id}/review 双层审计。"""
    import httpx

    from backend.database import DEMO_PHONE, DEMO_PASSWORD, SessionLocal
    from backend.models import Story

    with SessionLocal() as session:
        story = session.query(Story).filter_by(title="晒谷的秋天").one_or_none()
        if story is None:
            sys.exit("库里没有评测故事：先执行 prepare-db")
        story_id = story.id

    with httpx.Client(base_url=base_url, timeout=180) as client:
        login = client.post("/api/auth/login",
                            json={"phone": DEMO_PHONE, "password": DEMO_PASSWORD, "role": "elder"}).json()
        headers = {"Authorization": "Bearer " + login["token"]}
        consent = login["consentVersion"]

        results = []
        for index, item in enumerate(EDITS, 1):
            local_blocked = any(
                f.get("kind") == "unsupported"
                for f in __import__("backend.evidence_audit", fromlist=["audit_text"])
                .audit_text(body=item["body"], claims=CLAIMS))
            resp = client.post(f"/api/agent/stories/{story_id}/review",
                               headers=headers,
                               json={"body": item["body"], "consentVersion": consent})
            api_blocked = resp.status_code == 409
            layer = "规则层" if local_blocked else ("模型层" if api_blocked else "未拦截")
            results.append({**item,
                            "blocked": api_blocked,
                            "expected_block": item["kind"] == "tamper",
                            "correct": api_blocked == (item["kind"] == "tamper"),
                            "layer": layer,
                            "http_status": resp.status_code})
            print("[%02d/%d] %s %s -> %s" % (index, len(EDITS), item["id"], item["note"],
                                             "拦截(%s)" % layer if api_blocked else "放行"))
        return results


# ---------------------------------------------------------------- 报告
def summarize(results: list[dict], mode: str) -> str:
    tamper = [r for r in results if r["kind"] == "tamper"]
    benign = [r for r in results if r["kind"] == "benign"]
    intercepted = sum(1 for r in tamper if r["blocked"])
    false_blocked = sum(1 for r in benign if r["blocked"])
    lines = [
        "# 审计拦截率评测报告", "",
        "- 评测对象：证据审计闸门（`backend/evidence_audit.py`）"
        + (" + 写作审计 Agent（真实模型）" if mode == "llm" else "（本地确定性规则层）"),
        "- 评测集：50 条编辑 = 25 条事实篡改（应拦截）+ 25 条正常润色（应放行）",
        "- 评测故事：《晒谷的秋天》（6 句口述原文、8 条证据），全部编辑随代码提交，可完整复现", "",
        "| 指标 | 数值 |", "| --- | --- |",
        "| 篡改拦截率 | %d / %d = %.0f%% |" % (intercepted, len(tamper), 100 * intercepted / len(tamper)),
        "| 润色误拦率 | %d / %d = %.0f%% |" % (false_blocked, len(benign), 100 * false_blocked / len(benign)),
        "| 判定一致率 | %d / %d = %.0f%% |" % (
            sum(1 for r in results if r["correct"]), len(results),
            100 * sum(1 for r in results if r["correct"]) / len(results)), "",
        "## 分类明细", "",
        "| 类别 | 条数 | 拦截 | 拦截率 |", "| --- | --- | --- | --- |",
    ]
    categories: dict[str, list[dict]] = {}
    for r in results:
        categories.setdefault(r["category"], []).append(r)
    for cat, items in categories.items():
        hit = sum(1 for r in items if r["blocked"])
        lines.append("| %s | %d | %d | %.0f%% |" % (cat, len(items), hit, 100 * hit / len(items)))

    misses = [r for r in tamper if not r["blocked"]]
    if misses:
        lines += ["", "## 未拦截的篡改（漏报）", ""]
        for r in misses:
            lines.append("- **%s** %s：%s" % (r["id"], r["note"],
                                              "、".join("「%s」" % e for e in r["excerpts"][:2]) or "整句与证据重合度过低未被触发"))
    false_positives = [r for r in benign if r["blocked"]]
    if false_positives:
        lines += ["", "## 被误拦的润色（误报）", ""]
        for r in false_positives:
            lines.append("- **%s** %s：%s" % (r["id"], r["note"],
                                              "、".join("「%s」" % e for e in r["excerpts"][:2])))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="审计拦截率评测")
    parser.add_argument("mode", choices=["local", "prepare-db", "llm"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()

    if args.mode == "prepare-db":
        prepare_db()
        return 0
    if args.mode == "local":
        results = run_local()
    else:
        results = run_llm(args.base_url)

    report = summarize(results, args.mode)
    out = Path(__file__).resolve().parents[1] / "docs" / "evaluation" / ("audit-benchmark-%s.md" % args.mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print("报告已写入：", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
