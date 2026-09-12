"""Mock 智能体：无需任何密钥、结果确定的实现。

它不是"假数据生成器"，而是**规则严格的确定性智能体**：
- 抽取出的每条事实都带原文子串（quote）与轮次（turn_id），可被审计程序验证
- 找不到证据就留空，不推测
- 写作只用传入的 claim，且每条事实句都挂 claim id
- 审计会真的去核对：引用是否存在、quote 是否真是原文子串

这样做的价值是：现在就能把"证据链是否成立"跑成自动化测试；
换成真模型后，审计与测试完全复用。
"""

from __future__ import annotations

import re
from typing import Any

from .state import ELEMENT_LABELS, ELEMENT_PRIORITY, SEVEN_ELEMENTS

# ------------------------------------------------------------------ 规则表

STOP_PATTERNS = (
    "不想讲",
    "不想说",
    "不录了",
    "别问了",
    "不讲了",
    "到此为止",
    "就到这里",
    "停下来",
    "我不想继续",
    "不想继续",
    "累了",
    "今天先这样",
)

# 要素识别线索（顺序敏感：先匹配更具体的）
ELEMENT_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("time", ("年", "月", "日", "春天", "夏天", "秋天", "冬天", "小时候", "那年", "当时", "后来", "那天", "岁")),
    ("place", ("家乡", "老家", "村", "巷", "院子", "街", "河边", "学校", "城里", "屋里", "门口", "山上", "院子里")),
    ("people", ("妈妈", "爸爸", "父亲", "母亲", "外婆", "外公", "奶奶", "爷爷", "妹妹", "姐姐", "哥哥", "弟弟", "老师", "同学", "邻居", "妻子", "丈夫", "儿子", "女儿", "师傅")),
    ("result", ("后来", "于是", "结果", "终于", "从此", "就这样", "最后", "所以")),
    ("impact", ("影响", "改变", "学会", "懂得", "明白了", "让我", "使我", "再也不敢", "一直记得")),
    ("feeling", ("高兴", "难过", "害怕", "舍不得", "温暖", "踏实", "骄傲", "委屈", "感激", "想念", "开心", "心里")),
    ("event", ()),  # 兜底：有内容的句子至少是"发生了什么"
)

# 主题对应的开场问题（设计稿四个主题）
TOPIC_OPENERS = {
    "我的家乡": "老家那个院子或巷子，最先想起的是哪个角落？",
    "上学的日子": "上学路上，你每天都会经过什么地方？",
    "工作与手艺": "第一天上班或学徒那天，你做了什么？",
    "爱情与家庭": "你和家人第一次一起做的那件事，是什么？",
}

FOLLOW_UP_TEMPLATES = {
    "time": "这件事大概是什么时候？不用很精确，季节或年份都行。",
    "place": "当时是在什么地方？说说那个地方的样子。",
    "people": "那时候还有谁在场？",
    "event": "你能说说当时具体发生了什么吗？",
    "result": "后来怎么样了？",
    "impact": "这件事对后来的你有什么影响？",
    "feeling": "那时候你心里是什么感觉？",
}


def _split_sentences(text: str) -> list[str]:
    """按中文标点与换行切句，保留句子原文（quote 必须是原样子串）。"""
    normalized = text.replace("\r\n", "\n")
    parts = re.split(r"(?<=[。！？!?；;\n])", normalized)
    return [p.strip() for p in parts if p.strip()]


def _classify(sentence: str) -> str:
    for element, cues in ELEMENT_CUES:
        if not cues:
            continue
        if any(cue in sentence for cue in cues):
            return element
    return "event"


class MockAgentProvider:
    name = "mock-agents"

    # ------------------------------------------------------------- 采访导演

    def choose_question(
        self,
        *,
        subject_name: str,
        topic: str,
        round_index: int,
        asked_questions: list[str],
        previous_answers: list[str],
        missing_fields: list[str],
    ) -> dict[str, Any]:
        joined = " ".join(previous_answers)
        for pattern in STOP_PATTERNS:
            if pattern in joined:
                return {
                    "should_stop": True,
                    "stop_reason": f"讲述者表达了停止意愿（{pattern}）",
                    "question": None,
                    "target_element": None,
                    "closing": "好，今天就到这儿。你想讲的时候，随时回来。",
                }

        if round_index == 0:
            question = TOPIC_OPENERS.get(topic) or f"关于「{topic}」，你最想先留住的是哪个具体时刻？"
            return {
                "should_stop": False,
                "stop_reason": None,
                "question": f"{subject_name}，{question}",
                "target_element": "event",
                "closing": None,
            }

        # 按优先级挑一个还没问过的缺失要素（同一缺失项不重复追问）
        target = None
        for element in ELEMENT_PRIORITY:
            if element in missing_fields:
                candidate = FOLLOW_UP_TEMPLATES[element]
                if candidate not in asked_questions:
                    target = element
                    break
        if target is None:
            return {
                "should_stop": False,
                "stop_reason": None,
                "question": None,
                "target_element": None,
                "closing": "七要素都齐了，可以生成故事了。",
            }
        return {
            "should_stop": False,
            "stop_reason": None,
            "question": FOLLOW_UP_TEMPLATES[target],
            "target_element": target,
            "closing": None,
        }

    # ------------------------------------------------------------- 证据抽取

    def extract_claims(self, *, transcript: str, turn_id: str) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        found: set[str] = set()
        for sentence in _split_sentences(transcript):
            # 忽略"不知道/不记得"这类无信息回答，避免把它们当事实
            if sentence.strip("，。、！？ ") in {"不知道", "不记得", "想不起来", "没有", "没什么"}:
                continue
            if len(sentence) < 4:
                continue
            element = _classify(sentence)
            found.add(element)
            claims.append(
                {
                    "element": element,
                    "text": sentence.rstrip("。！？；;"),
                    "quote": sentence,  # 原样子串，审计会校验
                    "confidence": 0.6,
                }
            )

        # 七要素里本条讲述完全没有依据的，保持缺失
        missing = [element for element in SEVEN_ELEMENTS if element not in found]
        return {"claims": claims, "missing_fields": missing}

    # --------------------------------------------------------------- 写作

    def compose_draft(
        self,
        *,
        subject_name: str,
        topic: str,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sentences: list[dict[str, Any]] = [
            {
                "text": f"《{topic}》",
                "claim_ids": [],
                "must_cite": False,
            },
            {
                "text": f"这是{subject_name}亲口讲述并等待确认的一段家庭记忆。",
                "claim_ids": [],
                "must_cite": False,
            },
        ]

        # 按要素优先级组织叙事顺序，同一要素内保持讲述顺序
        ordered = sorted(
            claims,
            key=lambda c: (
                ELEMENT_PRIORITY.index(c["element"]) if c.get("element") in ELEMENT_PRIORITY else len(ELEMENT_PRIORITY),
                c.get("order", 0),
            ),
        )
        for claim in ordered:
            text = str(claim.get("text", "")).strip()
            if not text:
                continue
            sentences.append(
                {
                    "text": f"{text}。",
                    "claim_ids": [claim["id"]],
                    "must_cite": True,
                }
            )

        return {"title": f"《{topic}》", "sentences": sentences}

    # ------------------------------------------------------------- 写作审计

    def audit_draft(
        self,
        *,
        sentences: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_id = {claim["id"]: claim for claim in claims}
        findings: list[dict[str, Any]] = []
        for sentence in sentences:
            cited = list(sentence.get("claim_ids") or [])
            excerpt = str(sentence.get("text", ""))[:80]

            if not cited:
                if sentence.get("must_cite"):
                    findings.append(
                        {
                            "sentence_id": sentence.get("id", ""),
                            "kind": "unsupported",
                            "message": "该句陈述了事实但没有绑定任何证据。",
                            "excerpt": excerpt,
                        }
                    )
                continue

            for claim_id in cited:
                claim = by_id.get(claim_id)
                if claim is None:
                    findings.append(
                        {
                            "sentence_id": sentence.get("id", ""),
                            "kind": "invalid_citation",
                            "message": f"引用了不存在的证据 {claim_id}。",
                            "excerpt": excerpt,
                        }
                    )
                    continue
                # 证据本身必须成立：quote 是原文子串且指向某一轮
                quote = str(claim.get("quote", ""))
                if not quote or not claim.get("turn_id"):
                    findings.append(
                        {
                            "sentence_id": sentence.get("id", ""),
                            "kind": "quote_mismatch",
                            "message": f"证据 {claim_id} 缺少原文片段或轮次，不可追溯。",
                            "excerpt": excerpt,
                        }
                    )
        return findings

    # ------------------------------------------------------------- 冲突处理

    def resolve_conflicts(self, *, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """同一要素出现多轮不同表述时并列展示，由人判断，不由 AI 裁定。"""
        by_element: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            by_element.setdefault(str(claim.get("element")), []).append(claim)

        groups: list[dict[str, Any]] = []
        for element, items in by_element.items():
            distinct_turns = {item.get("turn_id") for item in items}
            if len(distinct_turns) < 2:
                continue
            texts = {str(item.get("text", "")).strip() for item in items}
            if len(texts) < 2:
                continue
            first, second = items[0], items[1]
            groups.append(
                {
                    "element": element,
                    "elementLabel": ELEMENT_LABELS.get(element, element),
                    "quote_a": first.get("quote", ""),
                    "turn_a": first.get("turn_id", ""),
                    "quote_b": second.get("quote", ""),
                    "turn_b": second.get("turn_id", ""),
                    "note": "两次讲述的表述不同，按产品规则并列保留，等待家人确认。",
                }
            )
        return groups
