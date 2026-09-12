"""人工改写后的证据核对（被图与业务层共用）。

产品规则：无论是 AI 生成还是人工改写，**正文里不允许出现无法追溯到原声的事实**。

判定规则：
- 标题（《…》）、来源说明（"这是…"开头）、分隔线（——）属于过渡内容，不需要证据
- 其余每一句必须能对应到某条证据：与证据表述一致、包含证据原文，或包含证据原文的关键片段
  （允许在证据基础上润色，但不允许新增事实）
"""

from __future__ import annotations

from typing import Any

TRANSITION_PREFIXES = ("《", "这是", "——", "———")

# 句子与证据重合度的下限（按句长自适应），低于该值视为新事实
MIN_OVERLAP_RATIO = 0.34


def is_transition(text: str) -> bool:
    return text.startswith(TRANSITION_PREFIXES)


def _evidence_texts(claims: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for claim in claims:
        for key in ("quote", "text"):
            value = str(claim.get(key, "") or "").strip()
            if value:
                texts.append(value.strip("。！？；; "))
    return texts


def _longest_common_run(a: str, b: str) -> int:
    """最长公共子串长度（句子短，直接做滑动窗口即可）。"""
    if not a or not b:
        return 0
    best = 0
    window = min(len(a), len(b))
    while window > best:
        found = False
        for start in range(len(a) - window + 1):
            if a[start : start + window] in b:
                best = window
                found = True
                break
        if not found:
            window -= 1
    return best


def audit_text(*, body: str, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐句核对改写后的正文，返回问题列表（空列表表示全部可追溯）。"""
    evidence = _evidence_texts(claims)
    findings: list[dict[str, Any]] = []

    for index, line in enumerate(body.splitlines()):
        text = line.strip()
        if not text or is_transition(text):
            continue
        normalized = text.strip("。！？；; ")
        if not normalized:
            continue

        matched = False
        for item in evidence:
            if not item:
                continue
            if normalized == item or normalized in item or item in normalized:
                matched = True
                break
            # 允许润色：与证据的关键片段重合即可，但重合度不足视为新增事实
            overlap = _longest_common_run(normalized, item)
            if overlap >= max(4, int(len(item) * MIN_OVERLAP_RATIO)):
                matched = True
                break

        if not matched:
            findings.append(
                {
                    "sentence_id": f"edited-s{index:02d}",
                    "kind": "unsupported",
                    "message": "该句在讲述证据里找不到对应来源，不能直接发布。",
                    "excerpt": normalized[:80],
                }
            )
    return findings


__all__ = ["audit_text", "is_transition"]
