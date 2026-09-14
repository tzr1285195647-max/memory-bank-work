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


def _normalize_for_match(text: str) -> str:
    """去掉空白，并统一常见中英标点，用于容忍模型输出的格式差异。"""
    table = str.maketrans(
        {
            "，": ",",
            "。": ".",
            "！": "!",
            "？": "?",
            "；": ";",
            "：": ":",
            "、": ",",
            "（": "(",
            "）": ")",
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "—": "-",
            "～": "~",
        }
    )
    return "".join(ch for ch in text.translate(table) if not ch.isspace())


def _quote_in_transcript(quote: str, transcript: str) -> bool:
    """校验 quote 是否来自讲述原文。

    先做严格子串匹配；不中时退一步做「忽略空白与标点差异」的匹配——
    模型常把标点写成全角/半角或漏掉标点，这属于格式差异而非编造。
    **不允许改写**：归一后仍是子串才通过，语序与用词必须照旧。
    """
    if not quote:
        return False
    if quote in transcript:
        return True
    return _normalize_for_match(quote) in _normalize_for_match(transcript)


class LLMUnavailableError(RuntimeError):
    """模型调用失败或输出不合规，调用方可据此降级或重试。"""


class SchemaModeProbe(RuntimeError):
    """用于探测服务端是否支持严格 JSON Schema 的内部异常，不算降级。"""


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
        # 调试用：最近一次被丢弃的原因（不含讲述原文，避免敏感内容落盘）
        self.last_drop_reasons: list[str] = []
        self.last_missing_fields: dict[str, int] = {}
        # 服务端是否支持严格 JSON Schema（None = 未探测过）
        self._strict_schema_supported: bool | None = None
        if not self.api_key:
            raise LLMUnavailableError("未配置 LLM_API_KEY")

    # ------------------------------------------------------------ 底层调用

    def _chat(self, *, user_prompt: str, max_tokens: int = 2000, schema: dict[str, Any] | None = None) -> Any:
        """调用模型并取回 JSON。

        schema 优先用**严格 JSON Schema**（服务端保证结构）；若服务端不支持
        （例如 DeepSeek 目前返回 "This response_format type is unavailable now"），
        自动退回 json_object 模式并记住该能力，后续不再尝试。
        两种模式下本地校验都照常执行——服务端保证格式不等于内容可信。
        """
        payload_base = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_RULES},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        self.last_http_status: int | None = None
        self.last_error_detail: str = ""
        for attempt in range(settings.llm_max_retries + 1):
            # 每轮重新决定 response_format：上一轮可能刚探测出服务端不支持严格 schema
            strict = bool(schema) and self._strict_schema_supported is not False
            if strict:
                response_format: dict[str, Any] = {
                    "type": "json_schema",
                    "json_schema": {"name": "memory_bank_payload", "strict": True, "schema": schema},
                }
            else:
                response_format = {"type": "json_object"}
            LOGGER.debug("LLM attempt=%s strict_schema=%s", attempt + 1, strict)
            payload = {**payload_base, "response_format": response_format}
            try:
                response = httpx.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=settings.llm_timeout_seconds,
                )
                self.last_http_status = response.status_code
                if response.status_code >= 400:
                    # 只取错误类型与简短说明，不记录可能含敏感内容的完整响应
                    detail = ""
                    try:
                        err = response.json().get("error") or {}
                        detail = str(err.get("message") or err.get("type") or "")[:200]
                    except Exception:  # noqa: BLE001
                        detail = response.text[:200]
                    self.last_error_detail = f"HTTP {response.status_code}: {detail}"
                    # 服务端不支持严格 schema 时，标记并立即用 json_object 重试。
                    # 这属于能力探测，不算一次降级（不计入 fallbackCount）。
                    if response.status_code == 400 and "response_format" in detail and schema:
                        self._strict_schema_supported = False
                        LOGGER.info("服务端不支持 json_schema，改用 json_object 模式")
                        raise SchemaModeProbe()
                    raise LLMUnavailableError(f"模型返回 HTTP {response.status_code}")
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                return _extract_json(content)
            except SchemaModeProbe:
                # 探测结果已记录（_strict_schema_supported=False），下一轮直接用
                # json_object；这不算降级，也不记录错误详情
                LOGGER.info("已切换到 json_object 模式，继续重试")
                continue
            except (httpx.HTTPError, KeyError, IndexError, TypeError, LLMUnavailableError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPError):
                    self.last_error_detail = f"{type(exc).__name__}: {str(exc)[:200]}"
                elif not self.last_error_detail:
                    self.last_error_detail = f"{type(exc).__name__}: {str(exc)[:200]}"
                LOGGER.warning(
                    "LLM 调用失败 attempt=%s/%s error=%s detail=%s",
                    attempt + 1,
                    settings.llm_max_retries + 1,
                    type(exc).__name__,
                    self.last_error_detail[:160],
                )
        raise LLMUnavailableError(f"模型调用失败：{type(last_error).__name__}（{self.last_error_detail[:120]}）")

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
        data = self._chat(
            user_prompt=prompt,
            max_tokens=400,
            schema={
                "type": "object",
                "properties": {
                    "question": {"type": ["string", "null"]},
                    "target_element": {
                        "type": ["string", "null"],
                        "enum": [*SEVEN_ELEMENTS, None],
                    },
                },
                "required": ["question", "target_element"],
                "additionalProperties": False,
            },
        )

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
            "讲述里没有的要素不要输出；如果确实没有任何可抽取的事实，claims 返回空数组。\n\n"
            f"讲述内容：\n{transcript}\n\n"
            '输出格式：{"claims":[{"element":"time","text":"归一化表述","quote":"原文片段"}]}'
        )
        data = self._chat(
            user_prompt=prompt,
            max_tokens=1500,
            schema={
                "type": "object",
                "properties": {
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "element": {
                                    "type": "string",
                                    "enum": list(SEVEN_ELEMENTS),
                                },
                                "text": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["element", "text", "quote"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["claims"],
                "additionalProperties": False,
            },
        )
        raw_claims = data.get("claims")
        if not isinstance(raw_claims, list):
            raise LLMUnavailableError("模型未返回 claims 列表")

        claims: list[dict[str, Any]] = []
        dropped = 0
        reasons: list[str] = []
        missing_fields: dict[str, int] = {}
        for item in raw_claims:
            if not isinstance(item, dict):
                dropped += 1
                reasons.append("非对象条目")
                continue
            element = str(item.get("element", "")).strip()
            quote = str(item.get("quote", "")).strip()
            text = str(item.get("text", "")).strip() or quote.strip("。！？；; ")
            if element not in SEVEN_ELEMENTS:
                dropped += 1
                reasons.append(f"非法要素 {element!r}")
                missing_fields["element"] = missing_fields.get("element", 0) + 1
                continue
            if not quote:
                dropped += 1
                reasons.append("缺少 quote")
                missing_fields["empty_quote"] = missing_fields.get("empty_quote", 0) + 1
                continue
            # 硬校验：quote 必须是讲述原文的子串（允许标点/空白归一，不允许改写）
            if not _quote_in_transcript(quote, transcript):
                dropped += 1
                reasons.append("quote 不在原文")
                missing_fields["not_substring"] = missing_fields.get("not_substring", 0) + 1
                continue
            claims.append({"element": element, "text": text, "quote": quote, "confidence": 0.8})

        self.last_drop_reasons = reasons[-10:]
        self.last_missing_fields = missing_fields
        if dropped:
            LOGGER.info(
                "证据抽取丢弃 %s 条不合规结果，原因分布=%s",
                dropped,
                missing_fields,
            )
        if not claims and transcript.strip():
            raise LLMUnavailableError(
                f"模型未产出任何可校验的证据（丢弃 {dropped} 条：{missing_fields or '无条目'}）"
            )

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
        style: str = "natural",
    ) -> dict[str, Any]:
        if not claims:
            raise LLMUnavailableError("没有可用证据，不能写作")
        payload = [
            {"id": claim["id"], "element": claim["element"], "text": claim["text"], "quote": claim["quote"]}
            for claim in claims
        ]
        style_instruction = {
            "raw": "尽量保留原句、语气和讲述顺序，只修正明显标点，不改变说法。",
            "natural": "保持讲述顺序，去掉少量重复和口头停顿，使段落自然连贯。",
            "book": "在不新增事实的前提下优化段落、节奏和书面表达，适合收入家庭故事书。",
        }.get(style, "保持讲述顺序，去掉少量重复和口头停顿，使段落自然连贯。")
        prompt = (
            f"请把下列**已经确认的事实**整理成一段家庭记忆，主题《{topic}》，讲述者：{subject_name}。\n"
            "硬性要求：\n"
            "1. 只能使用下面给出的事实，**绝对不允许新增任何事实**（人名、时间、地点、因果都不能编）。\n"
            "2. 第一句是标题，格式为《主题》；第二句是来源说明，以「这是」开头。\n"
            "3. 其余每句话都必须是事实句，并挂上它所依据的 claim id（可多对一）。\n"
            f"4. 本次整理方式：{style_instruction}\n\n"
            f"事实列表：\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            '输出格式：{"title":"《...》","sentences":[{"text":"...","claim_ids":["claim-xxx"],"must_cite":true}]}'
        )
        data = self._chat(
            user_prompt=prompt,
            max_tokens=2000,
            schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sentences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "claim_ids": {"type": "array", "items": {"type": "string"}},
                                "must_cite": {"type": "boolean"},
                            },
                            "required": ["text", "claim_ids", "must_cite"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "sentences"],
                "additionalProperties": False,
            },
        )
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

    - AGENT_MODE=llm 且配置了 LLM_API_KEY：真实模型 + 失败回落
    - 其他情况：纯确定性 Mock（默认，离线可跑）
    """
    if not settings.llm_enabled:
        return MockAgentProvider()
    try:
        return FallbackAgentProvider(LLMAgentProvider())
    except LLMUnavailableError as exc:  # pragma: no cover - 配置缺失时
        LOGGER.warning("模型不可用，使用确定性实现：%s", exc)
        return MockAgentProvider()


__all__ = ["FallbackAgentProvider", "LLMAgentProvider", "LLMUnavailableError", "build_provider"]
