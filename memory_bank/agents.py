from __future__ import annotations

import re
from typing import Any


BASE_QUESTIONS = [
    "如果从这段记忆里只选一个画面，你最先看到的是什么？",
    "当时还有谁在场？你记得他们说过什么或做过什么？",
    "那个时刻最鲜明的声音、气味或物件是什么？",
    "这件事发生前后，有什么变化让你一直记到现在？",
    "如果要把这段经历讲给晚辈，你最希望他们理解什么？",
    "有没有一个细节容易被别人记错，值得现在说明清楚？",
    "这段记忆中还有哪位家人值得单独讲一讲？",
    "回头看，当时的你和现在的你对这件事有什么不同理解？",
]

TOPIC_QUESTIONS = {
    "童年": "小时候最常去、如今可能已经改变的地方是什么样的？",
    "家庭": "这个家庭里有哪些只有家人才懂的习惯或说法？",
    "工作": "第一次真正觉得自己能够独当一面，是在哪件事之后？",
    "爱情": "你第一次意识到对方对你很重要，是在什么时刻？",
    "迁徙": "离开熟悉的地方那天，你带走了什么，又留下了什么？",
}


class MockIntelligence:
    """无需外部密钥的确定性实现，接口可被真实模型适配器替换。"""

    name = "mock-intelligence"

    def choose_question(
        self,
        *,
        subject_name: str,
        topic: str,
        round_index: int,
        previous_answers: list[str],
    ) -> str:
        if round_index == 0:
            for keyword, question in TOPIC_QUESTIONS.items():
                if keyword in topic:
                    return f"{subject_name}，{question}"
            return f"{subject_name}，关于“{topic}”，你最想先留住的是哪个具体时刻？"
        base = BASE_QUESTIONS[(round_index - 1) % len(BASE_QUESTIONS)]
        if previous_answers:
            hint = self._short_hint(previous_answers[-1])
            return f"你刚才提到“{hint}”。{base}"
        return base

    def extract_claims(self, answer: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", answer).strip()
        parts = [item.strip(" ，。；！？!?;") for item in re.split(r"[。！？!?；;\n]+", cleaned)]
        ignored = {"不知道", "不记得", "想不起来", "没有", "没什么"}
        claims = [part for part in parts if len(part) >= 4 and part not in ignored]
        if not claims and len(cleaned) >= 4 and cleaned not in ignored:
            claims = [cleaned]
        return claims[:5]

    def compose_draft(
        self,
        *,
        subject_name: str,
        topic: str,
        claims: list[dict[str, Any]],
    ) -> str:
        title = f"《{topic}》"
        intro = f"这是{subject_name}亲口讲述并等待确认的一段家庭记忆。"
        paragraphs = [title, intro]
        for index, claim in enumerate(claims, start=1):
            marker = f"〔证据:{claim['id']}·第{claim['round_index']}轮〕"
            lead = "记忆里，" if index == 1 else "随后，" if index == 2 else "他/她还提到，"
            paragraphs.append(f"{lead}{claim['text']}。{marker}")
        return "\n\n".join(paragraphs)

    def audit_draft(self, content: str, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
        known_ids = {claim["id"] for claim in claims}
        findings: list[dict[str, Any]] = []
        blocks = [block.strip() for block in content.split("\n\n") if block.strip()]
        for index, block in enumerate(blocks):
            if index <= 1:
                continue
            cited = set(re.findall(r"证据:([\w_]+)", block))
            if not cited:
                findings.append(
                    {
                        "type": "unsupported",
                        "message": "该段缺少证据标记，需要补充采访或绑定来源。",
                        "excerpt": block[:100],
                    }
                )
            elif not cited.issubset(known_ids):
                findings.append(
                    {
                        "type": "invalid_citation",
                        "message": "该段引用了不存在的 Claim。",
                        "excerpt": block[:100],
                    }
                )
        return findings

    def create_delivery(self, approved_draft: str, subject_name: str) -> str:
        return (
            f"{approved_draft}\n\n"
            "——\n"
            f"这段记忆由{subject_name}的口述证据生成，并经过人工确认。"
        )

    @staticmethod
    def _short_hint(answer: str) -> str:
        normalized = re.sub(r"\s+", " ", answer).strip(" ，。；！？")
        return normalized[:18] + ("…" if len(normalized) > 18 else "")

