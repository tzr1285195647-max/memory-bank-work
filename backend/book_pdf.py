"""把本家庭已确认故事排成可下载的 PDF；不调用模型，也不改写正文。"""

from __future__ import annotations

import io
import re
from collections import OrderedDict
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)

from .config import PROJECT_ROOT

_GREEN = colors.HexColor("#354A3E")
_RUST = colors.HexColor("#A97556")
_MUTED = colors.HexColor("#788175")
_PAPER = colors.HexColor("#FFFAF0")
_FONT_DIR = PROJECT_ROOT / "backend" / "assets"
_ART_DIR = PROJECT_ROOT / "assets" / "book"


def _register_fonts() -> None:
    if "MemorySerif" not in pdfmetrics.getRegisteredFontNames():
        full_font = str(_FONT_DIR / "NotoSerifSC-VF.ttf")
        pdfmetrics.registerFont(TTFont("MemorySerif", full_font))
        pdfmetrics.registerFont(TTFont("MemorySerifBold", full_font))
        pdfmetrics.registerFontFamily("MemorySerif", normal="MemorySerif", bold="MemorySerifBold")


def _safe_text(value: object) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(_safe_text(value)).replace("\n", "<br/>"), style)


def _year(story: dict) -> int | None:
    value = story.get("memoryYear")
    if isinstance(value, bool):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2100 else None


def _page_background(canvas, doc) -> None:
    width, height = A5
    canvas.saveState()
    canvas.setFillColor(_PAPER)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.restoreState()


def _page_decoration(canvas, doc) -> None:
    width, height = A5
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D8CBB1"))
    canvas.roundRect(23, 23, width - 46, height - 46, 8, fill=0, stroke=1)
    canvas.setStrokeColor(colors.HexColor("#E6DCC8"))
    canvas.line(40, height - 62, width - 40, height - 62)
    canvas.setFillColor(_MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(43, height - 53, "MEMORY BANK / FAMILY BOOK")
    canvas.drawRightString(width - 43, 39, str(doc.page))
    canvas.restoreState()


def _cover_background(canvas, doc) -> None:
    width, height = A5
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#EDE3D1"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.restoreState()


def _cover_decoration(canvas, doc) -> None:
    width, height = A5
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#BDAA8D"))
    canvas.roundRect(28, 28, width - 56, height - 56, 12, fill=0, stroke=1)
    canvas.restoreState()


def render_family_book(stories: list[dict], title: str = "我们的家庭纪念册") -> bytes:
    """返回 PDF 字节；仅接受已确认故事，原样保留正文。"""
    confirmed = [item for item in stories if item.get("status") == "confirmed"]
    if not confirmed:
        raise ValueError("还没有已确认故事，暂时不能导出 PDF")
    confirmed.sort(key=lambda item: (_year(item) is None, _year(item) or 0, str(item.get("id") or "")))
    _register_fonts()
    book_title = _safe_text(title)[:30] or "我们的家庭纪念册"
    years = [_year(item) for item in confirmed if _year(item) is not None]
    year_range = f"{years[0]} - {years[-1]}" if years else "年代待补充"
    groups: OrderedDict[int | None, list[dict]] = OrderedDict()
    for story in confirmed:
        groups.setdefault(_year(story), []).append(story)

    heading = ParagraphStyle("heading", fontName="MemorySerifBold", fontSize=23,
                             leading=34, textColor=_GREEN, alignment=TA_CENTER, wordWrap="CJK")
    subtitle = ParagraphStyle("subtitle", fontName="MemorySerif", fontSize=10,
                              leading=18, textColor=_MUTED, alignment=TA_CENTER, wordWrap="CJK")
    section = ParagraphStyle("section", fontName="MemorySerifBold", fontSize=19,
                             leading=30, textColor=_GREEN, spaceAfter=14, wordWrap="CJK")
    story_title = ParagraphStyle("story_title", fontName="MemorySerifBold", fontSize=15,
                                 leading=25, textColor=_GREEN, spaceAfter=4, wordWrap="CJK")
    metadata = ParagraphStyle("metadata", fontName="MemorySerif", fontSize=9,
                              leading=15, textColor=_RUST, spaceAfter=12, wordWrap="CJK")
    body = ParagraphStyle("body", fontName="MemorySerif", fontSize=10.5,
                          leading=20, textColor=colors.HexColor("#41483D"),
                          alignment=TA_LEFT, spaceAfter=19, wordWrap="CJK")
    note = ParagraphStyle("note", fontName="MemorySerif", fontSize=8,
                          leading=14, textColor=_MUTED, wordWrap="CJK")

    output = io.BytesIO()
    doc = BaseDocTemplate(output, pagesize=A5, leftMargin=48, rightMargin=48,
                          topMargin=78, bottomMargin=66, title=book_title,
                          author="记忆银行", pageCompression=1)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
    def draw_page(canvas, document) -> None:
        (_cover_background if document.page == 1 else _page_background)(canvas, document)

    def finish_page(canvas, document) -> None:
        (_cover_decoration if document.page == 1 else _page_decoration)(canvas, document)

    doc.addPageTemplates([PageTemplate(id="book", frames=[frame], onPage=draw_page,
                                       onPageEnd=finish_page)])

    flow = [Spacer(1, 40), _paragraph("FAMILY MEMORY BOOK", subtitle), Spacer(1, 30),
            _paragraph(book_title, heading), Spacer(1, 12),
            _paragraph("真实口述 · 家庭确认 · 按时光编排", subtitle), Spacer(1, 18)]
    cover_art = _ART_DIR / "cover-watercolor.png"
    if cover_art.exists():
        flow.append(Image(str(cover_art), width=245, height=200, kind="proportional"))
    flow.extend([Spacer(1, 18), _paragraph(f"{year_range} · 共 {len(confirmed)} 篇故事", subtitle),
                 PageBreak(), _paragraph("目录", section)])
    for year, items in groups.items():
        flow.append(_paragraph(f"{year}年" if year is not None else "年代待补充", metadata))
        for item in items:
            flow.append(_paragraph(f"《{_safe_text(item.get('title')) or '未命名故事'}》 · "
                                   f"{_safe_text(item.get('narratorName')) or '讲述者'}", body))
    flow.extend([Spacer(1, 14), _paragraph("只收录家庭成员已确认的故事；装饰插画不代表真实场景。", note)])

    vignette = _ART_DIR / "ginkgo-vignette.png"
    for year, items in groups.items():
        flow.extend([PageBreak(), _paragraph(f"{year}年" if year is not None else "年代待补充", section)])
        if vignette.exists():
            flow.append(Image(str(vignette), width=145, height=82, kind="proportional", hAlign="RIGHT"))
        flow.append(Spacer(1, 14))
        for item in items:
            flow.append(KeepTogether([
                _paragraph(_safe_text(item.get("title")) or "未命名故事", story_title),
                _paragraph(" · ".join(filter(None, [
                    _safe_text(item.get("narratorName")) or "讲述者",
                    _safe_text(item.get("lifeStage")),
                    _safe_text(item.get("mode")),
                ])), metadata),
            ]))
            flow.append(_paragraph(item.get("body") or "", body))
    doc.build(flow)
    return output.getvalue()
