"""人工改写后的证据核对（被图与业务层共用）。

产品规则：无论是 AI 生成还是人工改写，**正文里不允许出现无法追溯到原声的事实**。

判定规则：
- 标题（《…》）、来源说明（"这是…"开头）、分隔线（——）属于过渡内容，不需要证据
- 句首的短过渡语（那时候 / 当时 / 后来 / 记得 / 如今）先剥离再核对，不因过渡语本身误拦
- 其余每一句必须能对应到某条证据：与证据表述一致、包含证据原文，或包含证据原文的关键片段
  （允许在证据基础上润色，但不允许新增事实）
- 年份与年龄锚点同时识别阿拉伯数字与汉字数字（十二岁 -> 12 岁），锚点必须能在证据中找到同值
- 高重合度句子的未匹配残差若包含证据中没有的常见人物称谓或地点词，视为新增人物/地点
"""

from __future__ import annotations

import difflib
import re
from typing import Any

SOURCE_NOTE_SUFFIXES = (
    "确认过的口述原文。", "亲口讲述并等待确认的一段家庭记忆。",
    "亲口讲述的一段家庭记忆。", "讲述的一段记忆。",
    "亲口讲述整理、等待确认的一段家庭记忆。",
)

# 句子与证据重合度的下限（按句长自适应），低于该值视为新事实
MIN_OVERLAP_RATIO = 0.34

# 句首短过渡语：功能性开头，剥离后再逐分句核对
TRANSITION_PREFIXES = ("那时候", "当时", "后来", "记得", "如今")

# 汉字数字（含"两"），用于年龄/年份锚点的归一化
_HAN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
               "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 常见人物称谓与亲属后缀（配合数字可匹配 三姑 / 二叔 等）
PERSON_TERMS = (
    "爸爸妈妈父亲母亲爷爷奶奶外公外婆姥姥姥爷",
    "哥哥姐姐弟弟妹妹大哥大姐小弟小妹兄弟姊妹",
    "叔叔伯伯婶婶姑姑姑妈舅舅姨妈师傅徒弟",
    "老师同学同事邻居队长会计司机医生护士",
)
_KIN_PATTERN = re.compile(r"[一二两三四五六七八九十]{1,3}[姑叔舅姨爷奶伯婶哥姐弟妹]")
_SURNAME = "王李张刘陈杨赵黄周吴徐孙马朱胡郭何林罗郑梁宋唐许韩冯邓曹彭曾萧田董潘袁蔡蒋余于杜叶程苏魏吕丁任卢姚沈钟姜崔谭陆范汪廖石金韦贾夏付方邹熊白孟秦邱侯江尹薛闫段雷龙黎史陶贺毛郝顾龚邵万钱严覃武戴莫孔向汤"
_SURNAME_KIN = re.compile(
    "[" + _SURNAME + "](?:爸爸|妈妈|爷爷|奶奶|姥姥|姥爷|叔|伯|婶|姑|舅|姨|哥|姐|弟|妹|老师|师傅|医生)"
)

# 常见地点词（残差核对用；匹配的是证据中没出现过的词）
PLACE_TERMS = ("操场", "祠堂", "庙里", "粮站", "车站", "码头", "集市", "学校",
               "县城", "镇上", "城里", "河边", "山上", "田里", "地里", "井边", "碾坊")


def _han_to_int(text: str) -> int | None:
    """把 零〇一二两三四五六七八九十 的组合转成整数；十/百按单位处理。"""
    if not text:
        return None
    total, current = 0, 0
    for ch in text:
        if ch in _HAN_DIGITS:
            current = _HAN_DIGITS[ch]
        elif ch == "十":
            current = (current or 1) * 10
        elif ch == "百":
            current = (current or 1) * 100
        else:
            return None
    total += current
    return total if 0 < total < 10000 else None


def _normalized_anchors(text: str) -> set[str]:
    """提取句子里的年份/年龄锚点并归一化（汉字数字 -> 阿拉伯数字）。"""
    anchors: set[str] = set()
    for value in re.findall(r"(?<!\d)(?:18|19|20)\d{2}年", text):
        anchors.add(value)
    for value in re.findall(r"\d+岁", text):
        anchors.add(value)
    for value in re.findall(r"[零〇一二两三四五六七八九十]{1,3}岁", text):
        number = _han_to_int(value[:-1])
        if number is not None and 1 <= number <= 120:
            anchors.add(f"{number}岁")
    return anchors


def _entity_terms(text: str) -> list[str]:
    """句子中出现的常见人物称谓 / 地点词（去重、保序）。"""
    terms: list[str] = []
    for term in PERSON_TERMS:
        start = 0
        while True:
            index = text.find(term, start)
            if index < 0:
                break
            terms.append(term)
            start = index + len(term)
    terms.extend(_KIN_PATTERN.findall(text))
    terms.extend(_SURNAME_KIN.findall(text))
    terms.extend(term for term in PLACE_TERMS if term in text)
    return sorted(set(terms))


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


_FUNC_RESIDUE = re.compile(
    r"[的了着和跟与也都还就又才再很挺呢吧啊呀嘛、，。！？!?；;：: \d]+"
)
_FUNC_RESIDUE_WORDS = (
    "一块", "一起", "那时候", "当时", "后来", "如今", "记得", "这边", "那边",
    "一次", "一下", "第一回", "头一回", "第一次", "先", "又去",
)


def _new_entity_residue(clause: str, item: str, evidence: list[str]) -> str | None:
    """高重合度分句里，找出证据中没有的新增人物/地点词；无则返回 None。"""
    matcher = difflib.SequenceMatcher(None, item, clause, autojunk=False)
    residue = "".join(
        clause[j1:j2] for tag, _, _, j1, j2 in matcher.get_opcodes() if tag in ("insert", "replace")
    )
    residue = _FUNC_RESIDUE.sub("", residue)
    for word in _FUNC_RESIDUE_WORDS:
        residue = residue.replace(word, "")
    clause_terms = [term for term in _entity_terms(clause) if not any(term in ev for ev in evidence)]
    for term in clause_terms:
        if term in residue or any(term in seg for seg in re.findall(r"[\u4e00-\u9fff]{2,}", residue)):
            return term
    return None


def audit_text(*, body: str, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐句核对改写后的正文，返回问题列表（空列表表示全部可追溯）。"""
    evidence = _evidence_texts(claims)
    evidence_anchors: set[str] = set()
    for item in evidence:
        evidence_anchors |= _normalized_anchors(item)
    findings: list[dict[str, Any]] = []

    for index, line in enumerate(body.splitlines()):
        text = line.strip()
        if not text or is_transition(text):
            continue
        # 逐个分句核对，避免“有依据的前半句，接一个编造的后半句”蒙混过关。
        clauses = [part.strip() for part in re.split(r"[，,。！？!?；;：:\n]", text) if part.strip()]
        for clause_index, raw_clause in enumerate(clauses):
            # 句首短过渡语先剥离：它们是功能性的，不该要求证据
            normalized = raw_clause
            stripped = True
            while stripped:
                stripped = False
                for prefix in TRANSITION_PREFIXES:
                    if normalized == prefix:
                        normalized = ""
                        stripped = True
                    elif normalized.startswith(prefix) and len(normalized) > len(prefix) + 1:
                        normalized = normalized[len(prefix) :].lstrip("，, ")
                        stripped = True
            if not normalized:
                continue
            anchors = _normalized_anchors(normalized)
            if anchors and any(anchor not in evidence_anchors for anchor in anchors):
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
                continue
            # 高重合度通过的分句，再核对未匹配残差里有没有证据外的新人物/地点
            best_item = max(
                (item for item in evidence if item),
                key=lambda item: _longest_common_run(normalized, item),
                default="",
            )
            if best_item:
                term = _new_entity_residue(normalized, best_item, evidence)
                if term:
                    is_person = bool(_KIN_PATTERN.fullmatch(term)) or any(
                        term in group for group in PERSON_TERMS
                    )
                    kind_label = "人物" if is_person else "地点"
                    findings.append({
                        "sentence_id": f"edited-s{index:02d}-c{clause_index:02d}",
                        "kind": "unsupported",
                        "message": f"这处表述新增了讲述中未提到的{kind_label}「{term}」，不能直接发布。",
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
