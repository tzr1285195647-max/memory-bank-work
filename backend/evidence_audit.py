"""人工改写后的证据核对（被图与业务层共用）。

产品规则：无论是 AI 生成还是人工改写，**正文里不允许出现无法追溯到原声的事实**。

判定规则：
- 标题（《…》）、来源说明（"这是…"开头）、分隔线（——）属于过渡内容，不需要证据
- 其余每一句必须能对应到某条证据：与证据表述一致、包含证据原文，或包含证据原文的关键片段
  （允许在证据基础上润色，但不允许新增事实）
"""

from __future__ import annotations

import re
from typing import Any

SOURCE_NOTE_SUFFIXES = (
    "确认过的口述原文。", "亲口讲述并等待确认的一段家庭记忆。",
    "亲口讲述的一段家庭记忆。", "讲述的一段记忆。",
    "亲口讲述整理、等待确认的一段家庭记忆。",
)

# 句子与证据重合度的下限（按句长自适应），低于该值视为新事实
MIN_OVERLAP_RATIO = 0.34


def is_transition(text: str) -> bool:
    # 只豁免独立标题和规范来源说明；“这是……，后来去了北京”必须被审计。
    if re.fullmatch(r"《[^》\n]{1,80}》", text) or text.startswith("——"):
        return True
    if text.startswith("这是") and "，" not in text and "。" not in text[:-1]:
        for suffix in SOURCE_NOTE_SUFFIXES:
            if text.endswith(suffix):
                prefix = text[2:-len(suffix)]
                if prefix.startswith("根据"):
                    prefix = prefix[2:]
                return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z·]{2,8}", prefix))
    return False


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
        # 逐个分句核对，避免“有依据的前半句，接一个编造的后半句”蒙混过关。
        clauses = [part.strip() for part in re.split(r"[，,。！？!?；;：:\n]", text) if part.strip()]
        for clause_index, normalized in enumerate(clauses):
            anchors = re.findall(r"(?<!\d)(?:18|19|20)\d{2}年|\d+岁|\d+月\d+日", normalized)
            if anchors and any(not any(anchor in item for item in evidence) for anchor in anchors):
                matched = False
            else:
                matched = any(
                    normalized == item
                    or normalized in item
                    or (item in normalized and len(normalized) <= len(item) + 4)
                    or (
                        len(normalized) >= 4
                        and _longest_common_run(normalized, item)
                        >= max(4, int(len(normalized) * MIN_OVERLAP_RATIO))
                    )
                    for item in evidence if item
                )
            if not matched:
                findings.append({
                    "sentence_id": f"edited-s{index:02d}-c{clause_index:02d}",
                    "kind": "unsupported",
                    "message": "这处表述在已确认讲述中找不到完整依据，不能直接发布。",
                    "excerpt": normalized[:80],
                })
    return findings


def unresolved_conflict_findings(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """冲突尚未由人澄清时，作为发布闸门而非仅作旁注。"""
    return [
        {
            "sentence_id": f"conflict-{index:02d}",
            "kind": "unresolved_conflict",
            "status": "needs_confirmation",
            "message": "两段已确认讲述存在事实冲突，请核对原声并修正碎片后重新生成。",
            "excerpt": f"{item.get('quote_a', '')} / {item.get('quote_b', '')}"[:80],
        }
        for index, item in enumerate(conflicts)
    ]


__all__ = ["audit_text", "is_transition", "unresolved_conflict_findings"]
