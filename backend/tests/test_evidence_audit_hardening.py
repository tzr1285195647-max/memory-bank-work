"""审计规则加固的回归用例（对应评测基准 docs/evaluation/ 的漏报与误报修复）。

覆盖三类修复：
- 汉字数字年龄锚点（十二岁 -> 十四岁 之类篡改必须拦截）
- 高重合度长句中插入证据外的人物/地点（常见实体词残差核对）
- 句首短过渡语豁免（那时候 / 当时 / 后来 / 记得 / 如今 不再误拦）
"""

from __future__ import annotations

from backend.audit_eval import CLAIMS, NARRATOR_LINES, ORIGINAL_BODY, TITLE
from backend.evidence_audit import audit_text

SOURCE_NOTE = "——根据林奶奶确认过的口述原文。"


def _lines(*body_lines: str) -> list[str]:
    return [TITLE, SOURCE_NOTE, *body_lines]


def _has_unsupported(body: str) -> bool:
    return any(f["kind"] == "unsupported" for f in audit_text(body=body, claims=CLAIMS))


# ---------- 汉字数字年龄锚点 ----------

def test_chinese_numeral_age_tamper_blocked():
    body = _lines(*[line.replace("十二岁", "十四岁") if "十二岁" in line else line
                    for line in NARRATOR_LINES])
    assert _has_unsupported("\n".join(body))


def test_chinese_numeral_age_tamper_variant_blocked():
    body = _lines(NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
                  NARRATOR_LINES[3], "那年我二十岁，第一次觉得大人的活计这么累。",
                  NARRATOR_LINES[5])
    assert _has_unsupported("\n".join(body))


def test_original_chinese_age_passes():
    assert not _has_unsupported(ORIGINAL_BODY)


# ---------- 高重合度长句插入人物 / 地点 ----------

def test_insert_person_in_long_sentence_blocked():
    body = _lines(NARRATOR_LINES[0],
                  "家里收了稻谷，我跟着母亲和三姑去村口的晒谷场。",
                  NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert _has_unsupported("\n".join(body))


def test_insert_surname_appellation_blocked():
    body = _lines(NARRATOR_LINES[0],
                  "家里收了稻谷，我跟着母亲和邻居王婶去村口的晒谷场。",
                  NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert _has_unsupported("\n".join(body))


def test_replace_place_in_long_sentence_blocked():
    body = _lines(NARRATOR_LINES[0], "我跟着母亲去学校操场上的晒谷场。",
                  NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert _has_unsupported("\n".join(body))


def test_insert_place_after_match_blocked():
    body = _lines(NARRATOR_LINES[0],
                  "我跟着母亲先去了学校，再去村口的晒谷场。",
                  NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert _has_unsupported("\n".join(body))


# ---------- 短过渡语豁免 ----------

def test_standalone_transition_clause_passes():
    body = _lines(NARRATOR_LINES[0], "那时候，家里收了稻谷，我跟着母亲去村口的晒谷场。",
                  NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert not _has_unsupported("\n".join(body))


def test_transition_prefix_stripped_passes():
    body = _lines(NARRATOR_LINES[0], NARRATOR_LINES[1],
                  "当时天暗下来，眼看着要下雨。",
                  NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert not _has_unsupported("\n".join(body))


def test_transition_with_fabrication_still_blocked():
    body = _lines(NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
                  NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
                  "那时候，隔壁王婶也赶过来帮忙。")
    assert _has_unsupported("\n".join(body))


# ---------- 残差只拦新增实体，不拦功能词与同义表达 ----------

def test_functional_residue_passes():
    body = _lines(NARRATOR_LINES[0], "家里收了稻谷，我跟着母亲一块去村口的晒谷场。",
                  NARRATOR_LINES[2], NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5])
    assert not _has_unsupported("\n".join(body))


def test_synonym_residue_passes():
    body = _lines(NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
                  NARRATOR_LINES[3], "那年我十二岁，头一回觉得大人的活计这么累。",
                  NARRATOR_LINES[5])
    assert not _has_unsupported("\n".join(body))


# ---------- 原有行为回归 ----------

def test_arabic_age_tamper_still_blocked():
    body = _lines(NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
                  NARRATOR_LINES[3], "那年我14岁，第一次觉得大人的活计这么累。",
                  NARRATOR_LINES[5])
    assert _has_unsupported("\n".join(body))


def test_arabic_year_insert_still_blocked():
    body = _lines(NARRATOR_LINES[0], NARRATOR_LINES[1], NARRATOR_LINES[2],
                  NARRATOR_LINES[3], NARRATOR_LINES[4], NARRATOR_LINES[5],
                  "到了1970年，晒谷场翻修成了水泥地。")
    assert _has_unsupported("\n".join(body))


def test_title_and_source_note_exempt():
    body = "\n".join([TITLE, SOURCE_NOTE, NARRATOR_LINES[0]])
    assert not _has_unsupported(body)
