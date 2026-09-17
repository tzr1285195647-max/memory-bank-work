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

# 七要素齐全并不代表故事已经讲细。用于模型不可用或模型过早结束时的安全追问；
# 问句只邀请补充新细节，不预设已经发生过的事情。
DETAIL_FOLLOW_UP_TEMPLATES = (
    "这段经历里，还有哪个没讲到的小片段最值得留下？",
    "你刚讲的那段经历中，有没有一句当时听到的话还记得？",
    "那天的情景里，有没有一个动作或画面你至今记得？",
    "从这件事开始到结束，中间还有哪一步没有讲到？",
    "关于当时在场的人，还有什么具体互动想补充？",
    "这段经历中，有没有一个转折或意外值得再讲讲？",
    "回想这件事，还有什么细节是你希望家人记住的？",
    "刚才提到的经历，哪一小段你还想讲得更具体些？",
    "这段往事后来还有什么你没有提过的变化吗？",
    "关于这段经历，你还愿意补充一个真实的小细节吗？",
)


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

    def clean_transcript(self, *, asr_raw_text: str, narrator_name: str, topic: str) -> dict[str, Any]:
        """离线安全降级：只删明确语气词并补最少标点，不添加任何词。"""
        text = asr_raw_text.strip()
        changes: list[dict[str, str]] = []
        for filler in ("呃，", "嗯，", "那个，", "呃", "嗯"):
            if filler in text:
                text = text.replace(filler, "")
                changes.append({"type": "delete_filler", "before": filler, "after": "", "reason": "删除无意义语气词"})
        text = re.sub(r"(然后[，,]?\s*){2,}", "然后，", text)
        if text and text[-1] not in "。！？!?":
            text += "。"
            changes.append({"type": "punctuate", "before": "", "after": "。", "reason": "补充句末标点"})
        return {"cleanText": text or asr_raw_text, "changes": changes, "uncertainties": []}

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
        confirmed_fragments: list[str] | None = None,
        confirmed_facts: list[dict[str, Any]] | None = None,
        minimum_fragments: int = 7,
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

        if round_index == 0 and not confirmed_fragments:
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
        if target is None and len(confirmed_fragments or []) + len(previous_answers) < minimum_fragments:
            for offset in range(len(DETAIL_FOLLOW_UP_TEMPLATES)):
                candidate = DETAIL_FOLLOW_UP_TEMPLATES[(len(confirmed_fragments or []) + offset) % len(DETAIL_FOLLOW_UP_TEMPLATES)]
                if candidate not in asked_questions:
                    return {"should_stop": False, "question": candidate, "target_element": None,
                            "complete": False, "closing": None}
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
        style: str = "raw",
    ) -> dict[str, Any]:
        source_text = {
            "raw": f"这是{subject_name}确认过的口述原文。",
            "book": f"这是根据{subject_name}亲口讲述整理、等待确认的一段家庭记忆。",
        }.get(style, f"这是{subject_name}确认过的口述原文。")
        sentences: list[dict[str, Any]] = [
            {
                "text": f"《{topic}》",
                "claim_ids": [],
                "must_cite": False,
            },
            {
                "text": source_text,
                "claim_ids": [],
                "must_cite": False,
            },
        ]

        # 原味口述保持已确认碎片顺序；适合成书才按叙事要素轻度重排。
        if style == "book":
            ordered = sorted(
                claims,
                key=lambda c: (
                    ELEMENT_PRIORITY.index(c["element"]) if c.get("element") in ELEMENT_PRIORITY else len(ELEMENT_PRIORITY),
                    c.get("order", 0),
                ),
            )
        else:
            ordered = sorted(claims, key=lambda c: c.get("order", 0))
        for claim in ordered:
            text = str(claim.get("quote" if style == "raw" else "text", "")).strip()
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
        from ..evidence_audit import audit_text

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
            # 引用存在并不代表整句话都有依据；只用本句引用的事实逐个分句复核。
            if sentence.get("must_cite") and cited and all(cid in by_id for cid in cited):
                local = audit_text(
                    body=str(sentence.get("text", "")),
                    claims=[by_id[cid] for cid in cited],
                )
                findings.extend({**item, "sentence_id": sentence.get("id", "")} for item in local)
        return findings

    # ------------------------------------------------------------- 冲突处理

    def resolve_conflicts(self, *, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """只报告可确定的年份冲突；不同细节不等于互相矛盾。"""
        from difflib import SequenceMatcher

        by_element: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            by_element.setdefault(str(claim.get("element")), []).append(claim)

        groups: list[dict[str, Any]] = []
        for element, items in by_element.items():
            if element != "time":
                continue
            for index, first in enumerate(items):
                for second in items[index + 1:]:
                    if first.get("turn_id") == second.get("turn_id"):
                        continue
                    first_quote, second_quote = str(first.get("quote") or ""), str(second.get("quote") or "")
                    years_a = set(re.findall(r"(?<!\d)(?:18|19|20)\d{2}年", first_quote))
                    years_b = set(re.findall(r"(?<!\d)(?:18|19|20)\d{2}年", second_quote))
                    if not years_a or not years_b or years_a == years_b:
                        continue
                    a_context = re.sub(r"(?<!\d)(?:18|19|20)\d{2}年", "", first_quote)
                    b_context = re.sub(r"(?<!\d)(?:18|19|20)\d{2}年", "", second_quote)
                    if min(len(a_context.strip("，。  ")), len(b_context.strip("，。  "))) < 5:
                        continue
                    if SequenceMatcher(None, a_context, b_context).ratio() < 0.55:
                        continue
                    groups.append({
                        "element": element,
                        "elementLabel": ELEMENT_LABELS.get(element, element),
                        "quote_a": first_quote, "turn_a": first.get("turn_id", ""),
                        "quote_b": second_quote, "turn_b": second.get("turn_id", ""),
                        "note": "同一段经历出现不同年份，等待讲述者核对。",
                    })
        return groups
