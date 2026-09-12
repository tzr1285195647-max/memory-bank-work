"""真实大模型适配器：兼容 OpenAI 协议（DeepSeek / 通义 / 智谱 / 月之暗面…）。

设计原则（对应产品与安全基线）：
- **供应商输出不可信**：结构化输出校验失败就重试，仍失败则丢弃该次结果并记录错误，
  绝不让不合规内容进入证据链
- **证据必须有原文**：抽取出的每条事实都要校验 quote 是否真的出现在讲述里，
  不在就丢掉这条（宁可缺失，不许编造）
- **不记录敏感原文**：日志只记长度、条数与错误类别，不打印讲述正文与密钥

与 Mock 实现共用同一个 AgentProvider 协议，图与业务层无需改动。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import settings
from .mock import MockAgentProvider
from .state import ELEMENT_LABELS, ELEMENT_PRIORITY, SEVEN_ELEMENTS

LOGGER = logging.getLogger("memory_bank.agents.llm")

# 领域规则（写进系统提示，但最终由本地校验兜底）
SYSTEM_RULES = """你是「记忆银行」的智能体，帮助家庭保存长辈的口述记忆。必须遵守：

1. 只依据用户给出的讲述内容工作，**绝不补充、推测或想象任何事实**。
2. 七要素为：时间(time)、地点(place)、人物(people)、事件(event)、结果(result)、影响(impact)、感受(feeling)。
3. 抽取事实时，每条必须给出讲述原文中的**原样片段**作为证据，不得改写该片段。
4. 讲述里没提到的要素就留空，不要为了完整而补写。
5. 语气温和、口语化，避免医学或心理诊断式表述。
只输出 JSON，不要输出任何解释文字。"""


class LLMUnavailableError(RuntimeError):
    """模型调用失败或输出不合规，调用方可据此降级或重试。"""


def _extract_json(text: str) -> Any:
    """从模型输出里取出 JSON（容忍 ```json 包裹与前后废话）。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 退一步：截取第一个 { 或 [ 到最后一个配对的 } 或 ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMUnavailableError("模型输出不是合法 JSON")


class LLMAgentProvider(MockAgentProvider):
    """真实模型实现；任何环节不合规都抛出 LLMUnavailableError。

    继承 Mock 作为**降级基类**：调用方可以选择在模型不可用时回落到确定性实现，
    也可以在需要严格性时把异常暴露给上层（由 runtime 决定）。
    """

    name = "llm-agents"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None) -> None:
        self.api_key = (api_key if api_key is not None else settings.llm_api_key).strip()
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        if not self.api_key:
            raise LLMUnavailableError("未配置 LLM_API_KEY")

    # ------------------------------------------------------------ 底层调用

    def _chat(self, *, user_prompt: str, max_tokens: int = 2000) -> Any:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_RULES},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=settings.llm_timeout_seconds,
                )
                if response.status_code >= 400:
                    # 不打印响应体，避免把敏感内容写进日志
                    raise LLMUnavailableError(f"模型返回 HTTP {response.status_code}")
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                return _extract_json(content)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, LLMUnavailableError) as exc:
                last_error = exc
                LOGGER.warning(
                    "LLM 调用失败 attempt=%s/%s error=%s",
                    attempt + 1,
                    settings.llm_max_retries + 1,
                    type(exc).__name__,
                )
        raise LLMUnavailableError(f"模型调用失败：{type(last_error).__name__}")

    # ------------------------------------------------------------ 采访导演

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
        # 停止意愿先由本地规则判定：绝不能让模型误判后继续追问
        for pattern in self._stop_patterns():
            if pattern in joined:
                return {
                    "should_stop": True,
                    "stop_reason": f"讲述者表达了停止意愿（{pattern}）",
                    "question": None,
                    "target_element": None,
                    "closing": "好，今天就到这儿。你想讲的时候，随时回来。",
                }

        missing_labels = [ELEMENT_LABELS.get(item, item) for item in missing_fields] or ["（要素已齐）"]
        prompt = (
            f"讲述者：{subject_name}\n主题：{topic}\n当前是第 {round_index + 1} 轮。\n"
            f"已经问过的问题：{json.dumps(asked_questions, ensure_ascii=False)}\n"
            f"已经讲到的内容摘要：{json.dumps([a[:80] for a in previous_answers], ensure_ascii=False)}\n"
            f"仍然缺失的要素：{json.dumps(missing_labels, ensure_ascii=False)}\n\n"
            "请生成至多一个温和的追问，只针对仍缺失的要素，不要重复已问过的问题，"
            "不要暗示不存在的事实。若认为已经没有必要继续，把 question 设为 null。\n"
            '输出格式：{"question": "...", "target_element": "time|place|people|event|result|impact|feeling|null"}'
        )
        data = self._chat(user_prompt=prompt, max_tokens=400)

        question = data.get("question")
        target = data.get("target_element")
        if target is not None and target not in SEVEN_ELEMENTS:
            target = None
        if isinstance(question, str) and question.strip():
            # 同一缺失项不重复追问（本地强制）
            if question.strip() in asked_questions:
                return {"should_stop": False, "stop_reason": None, "question": None, "target_element": None, "closing": None}
            return {
                "should_stop": False,
                "stop_reason": None,
                "question": question.strip(),
                "target_element": target,
                "closing": None,
            }
        return {"should_stop": False, "stop_reason": None, "question": None, "target_element": None, "closing": None}

    @staticmethod
    def _stop_patterns() -> tuple[str, ...]:
        from .mock import STOP_PATTERNS

        return STOP_PATTERNS

    # ------------------------------------------------------------ 证据抽取

    def extract_claims(self, *, transcript: str, turn_id: str) -> dict[str, Any]:
        prompt = (
            "请从下面这段讲述中抽取事实，按七要素分类。\n"
            "**quote 必须是讲述原文里的原样片段**（复制粘贴，不要改写、不要加标点以外的东西）。\n"
            "讲述里没有的要素不要输出。\n\n"
            f"讲述内容：\n{transcript}\n\n"
            '输出格式：{"claims":[{"element":"time","text":"归一化表述","quote":"原文片段"}]}'
        )
        data = self._chat(user_prompt=prompt, max_tokens=1500)
        raw_claims = data.get("claims")
        if not isinstance(raw_claims, list):
            raise LLMUnavailableError("模型未返回 claims 列表")

        claims: list[dict[str, Any]] = []
        dropped = 0
        for item in raw_claims:
            if not isinstance(item, dict):
                dropped += 1
                continue
            element = str(item.get("element", "")).strip()
            quote = str(item.get("quote", "")).strip()
            text = str(item.get("text", "")).strip() or quote.strip("。！？；; ")
            # 硬校验：要素合法 + quote 必须真的出现在讲述里
            if element not in SEVEN_ELEMENTS or not quote or quote not in transcript:
                dropped += 1
                continue
            claims.append({"element": element, "text": text, "quote": quote, "confidence": 0.8})

        if dropped:
            LOGGER.info("证据抽取丢弃 %s 条不合规结果（quote 不在原文或要素非法）", dropped)
        if not claims and transcript.strip():
            raise LLMUnavailableError("模型未产出任何可校验的证据")

        covered = {claim["element"] for claim in claims}
        return {
            "claims": claims,
            "missing_fields": [element for element in SEVEN_ELEMENTS if element not in covered],
        }

    # --------------------------------------------------------------- 写作

    def compose_draft(
        self,
        *,
        subject_name: str,
        topic: str,
        claims: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not claims:
            raise LLMUnavailableError("没有可用证据，不能写作")
        payload = [
            {"id": claim["id"], "element": claim["element"], "text": claim["text"], "quote": claim["quote"]}
            for claim in claims
        ]
        prompt = (
            f"请把下列**已经确认的事实**整理成一段家庭记忆，主题《{topic}》，讲述者：{subject_name}。\n"
            "硬性要求：\n"
            "1. 只能使用下面给出的事实，**绝对不允许新增任何事实**（人名、时间、地点、因果都不能编）。\n"
            "2. 第一句是标题，格式为《主题》；第二句是来源说明，以「这是」开头。\n"
            "3. 其余每句话都必须是事实句，并挂上它所依据的 claim id（可多对一）。\n"
            "4. 语气温和、口语化，像给家人看的文字。\n\n"
            f"事实列表：\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            '输出格式：{"title":"《...》","sentences":[{"text":"...","claim_ids":["claim-xxx"],"must_cite":true}]}'
        )
        data = self._chat(user_prompt=prompt, max_tokens=2000)
        raw_sentences = data.get("sentences")
        if not isinstance(raw_sentences, list) or not raw_sentences:
            raise LLMUnavailableError("模型未返回 sentences")

        valid_ids = {claim["id"] for claim in claims}
        sentences: list[dict[str, Any]] = []
        for index, item in enumerate(raw_sentences):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            cited = [str(cid) for cid in (item.get("claim_ids") or [])]
            # 引用必须真实存在（不存在的引用会被审计判为 invalid_citation）
            cited = [cid for cid in cited if cid in valid_ids]
            is_heading = text.startswith("《") or text.startswith("这是")
            sentences.append(
                {
                    "text": text,
                    "claim_ids": cited,
                    "must_cite": False if is_heading else True,
                }
            )
        if not sentences:
            raise LLMUnavailableError("模型输出的句子全部不合规")
        return {"title": str(data.get("title") or f"《{topic}》"), "sentences": sentences}


class FallbackAgentProvider:
    """模型优先、失败自动回落到确定性实现。

    演示友好：网络抖动、额度用尽、输出不合规都不会让流程断掉，
    但每次回落都会记日志并累计计数，便于排查（不会静默假装成功）。
    """

    name = "llm-with-fallback"

    def __init__(self, primary: "LLMAgentProvider", fallback: MockAgentProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or MockAgentProvider()
        self.fallback_count = 0
        self.last_error: str | None = None

    def _call(self, method: str, **kwargs: Any) -> Any:
        try:
            return getattr(self.primary, method)(**kwargs)
        except LLMUnavailableError as exc:
            self.fallback_count += 1
            self.last_error = str(exc)
            LOGGER.warning("模型不可用，回落到确定性实现 method=%s reason=%s", method, exc)
            return getattr(self.fallback, method)(**kwargs)

    def choose_question(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("choose_question", **kwargs)

    def extract_claims(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("extract_claims", **kwargs)

    def compose_draft(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("compose_draft", **kwargs)

    def audit_draft(self, **kwargs: Any) -> list[dict[str, Any]]:
        # 审计始终用确定性实现：审计规则必须是可解释、可复现的，不能交给模型自证
        return self.fallback.audit_draft(**kwargs)

    def resolve_conflicts(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._call("resolve_conflicts", **kwargs)


def build_provider() -> Any:
    """按配置选择智能体实现。

    - 配置了 LLM_API_KEY：真实模型 + 失败回落
    - 未配置：纯确定性 Mock（离线可跑，测试用这个）
    """
    if not settings.llm_enabled:
        return MockAgentProvider()
    try:
        return FallbackAgentProvider(LLMAgentProvider())
    except LLMUnavailableError as exc:  # pragma: no cover - 配置缺失时
        LOGGER.warning("模型不可用，使用确定性实现：%s", exc)
        return MockAgentProvider()


__all__ = ["FallbackAgentProvider", "LLMAgentProvider", "LLMUnavailableError", "build_provider"]
