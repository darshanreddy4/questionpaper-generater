"""PDF export (ReportLab). Fonts ship with matplotlib, so output is identical on any machine."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, HRFlowable, Image, KeepTogether, PageTemplate, Spacer, Table, TableStyle,
)
from reportlab.platypus import Paragraph as _RLParagraph
from reportlab.platypus.doctemplate import LayoutError

from app import languages
from app.diagrams import DiagramError, render_png
from app.exam.schema import Alternative, Answer, PaperContent, Question, SubQuestion, fmt_marks
from app.export.common import (
    duration_text, number_questions, option_letter, section_heading, section_marks_line,
)
from app.export import indic
from app.export.labels import ENGLISH, Labels, for_language
from app.export.mathtext import latex_to_text, render_math, split_math

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
FRAME_W = PAGE_W - 2 * MARGIN
INDENT = 8 * mm
INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#555555")
ACCENT = colors.HexColor("#1f3a68")
KEY = colors.HexColor("#14532d")

_fonts_ready = False


def Paragraph(text, style, *args, **kwargs):
    """ReportLab's Paragraph, with Indic text routed to a script font (bold when the style is bold)."""
    return _RLParagraph(indic.markup(text, "Bold" in style.fontName), style, *args, **kwargs)


def _register_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    ttf = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    for name, file in (("Serif", "STIXGeneral.ttf"), ("Serif-Bold", "STIXGeneralBol.ttf"),
                       ("Serif-Italic", "STIXGeneralItalic.ttf"), ("Serif-BoldItalic", "STIXGeneralBolIta.ttf"),
                       ("Sans", "DejaVuSans.ttf"), ("Sans-Bold", "DejaVuSans-Bold.ttf"),
                       ("Sans-Oblique", "DejaVuSans-Oblique.ttf")):
        pdfmetrics.registerFont(TTFont(name, os.path.join(ttf, file)))
    pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-Bold", italic="Serif-Italic",
                                  boldItalic="Serif-BoldItalic")
    pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique", boldItalic="Sans-Bold")
    _fonts_ready = True


def _styles(size: float, shaping: bool = False) -> Dict[str, ParagraphStyle]:
    lead = size * 1.42
    base = dict(fontName="Serif", fontSize=size, leading=lead, textColor=INK)
    styles = {
        "body": ParagraphStyle("body", alignment=TA_LEFT, **base),
        "q": ParagraphStyle("q", leftIndent=INDENT, firstLineIndent=-INDENT, spaceBefore=1, spaceAfter=2, **base),
        "cont": ParagraphStyle("cont", leftIndent=INDENT, spaceAfter=2, **base),
        "opt": ParagraphStyle("opt", leftIndent=4 * mm, firstLineIndent=-4 * mm, **base),
        "sub": ParagraphStyle("sub", leftIndent=INDENT + 6 * mm, firstLineIndent=-6 * mm, spaceAfter=2, **base),
        "marks": ParagraphStyle("marks", alignment=TA_RIGHT, **base),
        "or": ParagraphStyle("or", alignment=TA_CENTER, fontName="Serif-Bold", fontSize=size, leading=lead,
                             spaceBefore=3, spaceAfter=3),
        "title": ParagraphStyle("title", alignment=TA_CENTER, fontName="Sans-Bold", fontSize=15, leading=19, textColor=ACCENT),
        "subtitle": ParagraphStyle("subtitle", alignment=TA_CENTER, fontName="Serif-Bold", fontSize=13, leading=17),
        "meta": ParagraphStyle("meta", alignment=TA_CENTER, fontName="Serif", fontSize=10.5, leading=14, textColor=MUTED),
        "info": ParagraphStyle("info", fontName="Serif-Bold", fontSize=11, leading=15),
        "info_r": ParagraphStyle("info_r", fontName="Serif-Bold", fontSize=11, leading=15, alignment=TA_RIGHT),
        "h": ParagraphStyle("h", fontName="Sans-Bold", fontSize=10.5, leading=14, textColor=INK, spaceBefore=4),
        "instr": ParagraphStyle("instr", leftIndent=6 * mm, firstLineIndent=-6 * mm, fontName="Serif", fontSize=10.5,
                                leading=14.5),
        "sec": ParagraphStyle("sec", alignment=TA_CENTER, fontName="Sans-Bold", fontSize=11, leading=14, textColor=ACCENT),
        "secsub": ParagraphStyle("secsub", alignment=TA_CENTER, fontName="Serif-Italic", fontSize=10, leading=13,
                                 textColor=MUTED),
        "ans": ParagraphStyle("ans", leftIndent=INDENT, fontName="Serif", fontSize=size - 0.5, leading=lead - 0.6,
                              textColor=KEY),
        "anslabel": ParagraphStyle("anslabel", leftIndent=INDENT, fontName="Sans-Bold", fontSize=8.5, leading=11,
                                   textColor=KEY, spaceBefore=3),
        "caption": ParagraphStyle("caption", alignment=TA_CENTER, fontName="Serif-Italic", fontSize=9.5, leading=12,
                                  textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName="Serif", fontSize=size - 1, leading=lead - 1.5, textColor=INK),
        "cellb": ParagraphStyle("cellb", fontName="Serif-Bold", fontSize=size - 1, leading=lead - 1.5, textColor=INK),
        "ctx": ParagraphStyle("ctx", leftIndent=INDENT, fontName="Serif-Italic", fontSize=size - 1, leading=lead - 2,
                              textColor=MUTED),
    }
    if shaping:  # HarfBuzz shaping: conjuncts and vowel-sign placement for Indic scripts
        for st in styles.values():
            st.shaping = 1
    return styles


_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)


def _inline(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _BOLD.sub(r"<b>\1</b>", text)
    return text.replace("\r", "").replace("\n", "<br/>")


class _Ctx:
    def __init__(self, size: float, tmpdir: str, lab: Labels = ENGLISH, shaping: bool = False):
        self.size = size
        self.tmpdir = tmpdir
        self.lab = lab
        self.shaping = shaping
        self.st = _styles(size, shaping)
        self._diagrams: Dict[str, Optional[Any]] = {}

    def math_path(self, png: bytes) -> str:
        path = os.path.join(self.tmpdir, hashlib.sha1(png).hexdigest() + ".png")
        if not os.path.exists(path):
            with open(path, "wb") as fh:
                fh.write(png)
        return path

    def rich(self, text: str) -> str:
        parts: List[str] = []
        for kind, content in split_math(text or ""):
            if kind == "text":
                parts.append(_inline(content))
                continue
            img = render_math(content, self.size)
            if img is None:
                parts.append("<i>" + _inline(latex_to_text(content)) + "</i>")
            else:
                brk = "<br/>" if kind == "display" else ""
                parts.append(f'{brk}<img src="{self.math_path(img.png)}" width="{img.width_pt:.2f}" '
                             f'height="{img.height_pt:.2f}" valign="{-img.drop_pt:.2f}"/>{brk}')
        return "".join(parts)

    def para(self, text: str, style: str = "body") -> Paragraph:
        return Paragraph(self.rich(text), self.st[style])

    def diagram(self, spec: Optional[dict], indent: float = INDENT) -> List[Flowable]:
        if not spec:
            return []
        key = json.dumps(spec, sort_keys=True)
        if key not in self._diagrams:
            try:
                self._diagrams[key] = render_png(spec, dpi=220)
            except DiagramError:
                self._diagrams[key] = None
        rendered = self._diagrams[key]
        if rendered is None:
            return []
        w, h = rendered.width_in * 72, rendered.height_in * 72
        limit = min(FRAME_W - indent - 10 * mm, 120 * mm)
        if w > limit:
            h, w = h * limit / w, limit
        img = Image(io.BytesIO(rendered.data), width=w, height=h)
        img.hAlign = "CENTER"
        items: List[Flowable] = [Spacer(1, 2), img]
        caption = spec.get("caption")
        if caption:
            items.append(self.para(str(caption), "caption"))
        items.append(Spacer(1, 3))
        return items


# ------------------------------------------------------------------ question pieces

def _options_block(ctx: _Ctx, options: List[str], correct: Optional[int], show_key: bool) -> List[Flowable]:
    if not options:
        return []
    cells = []
    for i, opt in enumerate(options):
        mark = ""
        text = ctx.rich(opt)
        if show_key and correct == i:
            mark = ' <font name="Sans-Bold" color="#14532d">&#10003;</font>'
            text = f"<b>{text}</b>"
        cells.append(Paragraph(f"({option_letter(i)})\u2002{text}{mark}", ctx.st["opt"]))
    short = all(len(re.sub(r"\$.*?\$", "xx", o)) <= 26 for o in options)
    avail = FRAME_W - INDENT - 14 * mm
    if short and len(cells) % 2 == 0:
        rows = [[cells[i], cells[i + 1]] for i in range(0, len(cells), 2)]
        t = Table(rows, colWidths=[avail / 2, avail / 2])
    else:
        t = Table([[c] for c in cells], colWidths=[avail])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2),
                           ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
    t.hAlign = "LEFT"
    wrapper = Table([[t]], colWidths=[FRAME_W - 14 * mm])
    wrapper.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), INDENT), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return [wrapper]


def _data_table(ctx: _Ctx, headers: List[str], rows: List[List[str]]) -> List[Flowable]:
    if not rows:
        return []
    data = []
    if headers:
        data.append([Paragraph(ctx.rich(h), ctx.st["cellb"]) for h in headers])
    data += [[Paragraph(ctx.rich(c), ctx.st["cell"]) for c in r] for r in rows]
    ncol = max(len(r) for r in data)
    data = [r + [""] * (ncol - len(r)) for r in data]
    width = min(FRAME_W - INDENT - 20 * mm, 28 * mm * ncol)
    t = Table(data, colWidths=[width / ncol] * ncol, repeatRows=1 if headers else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if headers:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f6")))
    t.setStyle(TableStyle(style))
    t.hAlign = "CENTER"
    return [Spacer(1, 2), t, Spacer(1, 4)]


def _match_table(ctx: _Ctx, left: List[str], right: List[str]) -> List[Flowable]:
    data = [[Paragraph(ctx.lab.get("column_i"), ctx.st["cellb"]), Paragraph(ctx.lab.get("column_ii"), ctx.st["cellb"])]]
    for i, (a, b) in enumerate(zip(left, right)):
        data.append([Paragraph(f"{i + 1}.\u2002{ctx.rich(a)}", ctx.st["cell"]),
                     Paragraph(f"({option_letter(i)})\u2002{ctx.rich(b)}", ctx.st["cell"])])
    w = (FRAME_W - INDENT - 20 * mm) / 2
    t = Table(data, colWidths=[w, w])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f6")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    t.hAlign = "CENTER"
    return [Spacer(1, 2), t, Spacer(1, 4)]


def _answer_block(ctx: _Ctx, ans: Answer, options: List[str], qtype: str, total: float) -> List[Flowable]:
    items: List[Flowable] = [Paragraph(ctx.lab.get("answer"), ctx.st["anslabel"])]
    if ans.correct_option is not None and 0 <= ans.correct_option < len(options):
        items.append(ctx.para(f"{ctx.lab.get('correct_option')}: ({option_letter(ans.correct_option)}) {options[ans.correct_option]}", "ans"))
        if ans.text and not re.match(r"^\([a-dA-D]\)", ans.text):
            items.append(ctx.para(ans.text, "ans"))
    elif ans.text:
        items.append(ctx.para(ans.text, "ans"))
    scored = [s for s in ans.steps if s.text]
    if scored and not (len(scored) == 1 and scored[0].text.strip() == ans.text.strip()):
        rows = [[Paragraph(ctx.lab.get("marking_scheme"), ctx.st["cellb"]), Paragraph(ctx.lab.get("marks"), ctx.st["cellb"])]]
        for s in scored:
            rows.append([Paragraph(ctx.rich(s.text), ctx.st["cell"]), Paragraph(fmt_marks(s.marks), ctx.st["cell"])])
        rows.append([Paragraph(ctx.lab.get("total"), ctx.st["cellb"]), Paragraph(fmt_marks(total), ctx.st["cellb"])])
        t = Table(rows, colWidths=[FRAME_W - INDENT - 38 * mm, 14 * mm])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9bb39f")),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9f3ec")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        wrap = Table([[t]], colWidths=[FRAME_W - 14 * mm])
        wrap.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), INDENT), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        items.append(wrap)
    items += ctx.diagram(ans.diagram)
    return items


def _sub_blocks(ctx: _Ctx, subs: List[SubQuestion], show_answers: bool) -> List[Flowable]:
    items: List[Flowable] = []
    for s in subs:
        row = Table([[Paragraph(f"{ctx.rich(s.label)}\u2002{ctx.rich(s.text)}", ctx.st["sub"]),
                      Paragraph(f"[{fmt_marks(s.marks)}]", ctx.st["marks"])]], colWidths=[FRAME_W - 26 * mm, 12 * mm])
        row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        items.append(row)
        if s.options:
            items += _options_block(ctx, s.options, s.answer.correct_option, show_answers)
        if show_answers:
            items += _answer_block(ctx, s.answer, s.options, "sub", s.marks)
    return items


def _body(ctx: _Ctx, obj, number: Optional[int], show_answers: bool, is_alt: bool = False) -> List[Flowable]:
    """Flowables for a Question or its Alternative (no marks column)."""
    items: List[Flowable] = []
    lead = f"<b>{number}.</b>\u2002" if number is not None else ""
    items.append(Paragraph(lead + ctx.rich(obj.text), ctx.st["q"]))
    items += ctx.diagram(obj.diagram)
    if obj.table:
        items += _data_table(ctx, obj.table.headers, obj.table.rows)
    match = getattr(obj, "match", None)
    if match:
        items += _match_table(ctx, match.left, match.right)
    if obj.options:
        items += _options_block(ctx, obj.options, obj.answer.correct_option, show_answers)
    items += _sub_blocks(ctx, obj.sub_questions, show_answers)
    if show_answers and not obj.sub_questions:
        total = getattr(obj, "marks", None) or sum(s.marks for s in obj.answer.steps)
        items += _answer_block(ctx, obj.answer, obj.options, getattr(obj, "type", ""), total)
    return items


def _with_marks(content: List[Flowable], marks: float, flat: bool, ctx: _Ctx) -> List[Flowable]:
    label = f"[{fmt_marks(marks)}]"
    if flat:
        return content + [Paragraph(label, ctx.st["marks"])]
    t = Table([[content, Paragraph(label, ctx.st["marks"])]], colWidths=[FRAME_W - 14 * mm, 14 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return [t]


def _question_flowables(ctx: _Ctx, q: Question, number: int, mode: str, flat: bool) -> List[Flowable]:
    show_answers = mode in ("combined",)
    if mode == "answer":
        stem = re.sub(r"\s+", " ", q.text).strip()
        stem = stem if len(stem) <= 150 else stem[:147].rsplit(" ", 1)[0] + "…"
        head = [Paragraph(f"<b>{number}.</b>\u2002{ctx.rich(stem)}", ctx.st["q"])]
        meta = f"{q.topic} · {q.difficulty} · {q.bloom}" if q.topic else f"{q.difficulty} · {q.bloom}"
        head.append(Paragraph(_inline(meta), ctx.st["ctx"]))
        body: List[Flowable] = head
        if q.sub_questions:
            for s in q.sub_questions:
                body.append(Paragraph(f"{ctx.rich(s.label)} [{fmt_marks(s.marks)}]", ctx.st["cont"]))
                body += _answer_block(ctx, s.answer, s.options, "sub", s.marks)
        else:
            body += _answer_block(ctx, q.answer, q.options, q.type, q.marks)
        if q.alternative:
            body.append(Paragraph(ctx.lab.get("or"), ctx.st["or"]))
            alt = q.alternative
            if alt.sub_questions:
                for s in alt.sub_questions:
                    body.append(Paragraph(f"{ctx.rich(s.label)} [{fmt_marks(s.marks)}]", ctx.st["cont"]))
                    body += _answer_block(ctx, s.answer, s.options, "sub", s.marks)
            else:
                body += _answer_block(ctx, alt.answer, alt.options, q.type, q.marks)
        return _with_marks(body, q.marks, flat, ctx) + [Spacer(1, 5)]

    body = _body(ctx, q, number, show_answers)
    if q.alternative:
        body.append(Paragraph(ctx.lab.get("or"), ctx.st["or"]))
        body += _body(ctx, q.alternative, None, show_answers, is_alt=True)
    return _with_marks(body, q.marks, flat, ctx) + [Spacer(1, 5)]


# ------------------------------------------------------------------ document

class _NumberedCanvas(rl_canvas.Canvas):
    footer_left = ""
    footer_right = ""
    footer_lab: Labels = ENGLISH
    footer_shaping = False

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._saved: List[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total: int) -> None:
        self.setFont("Sans", 8)
        self.setFillColor(MUTED)
        self.setStrokeColor(colors.HexColor("#bbbbbb"))
        self.setLineWidth(0.4)
        self.line(MARGIN, 13 * mm, PAGE_W - MARGIN, 13 * mm)
        page = self.footer_lab.get("page", p=self._pageNumber, t=total)
        right = f"{self.footer_right} · {page}" if self.footer_right else page
        if not self.footer_shaping:
            self.drawString(MARGIN, 8.5 * mm, self.footer_left[:95])
            self.drawRightString(PAGE_W - MARGIN, 8.5 * mm, right)
            return
        base = dict(fontName="Sans", fontSize=8, leading=10, textColor=MUTED, shaping=1)
        for text, x, align in ((self.footer_left[:95], MARGIN, TA_LEFT), (right, PAGE_W / 2, TA_RIGHT)):
            para = Paragraph(_inline(text), ParagraphStyle("foot", alignment=align, **base))
            w, h = para.wrap(PAGE_W / 2 - MARGIN, 10 * mm)
            para.drawOn(self, x, 8 * mm - 2)


def _header(ctx: _Ctx, content: PaperContent, mode: str) -> List[Flowable]:
    m = content.meta
    st = ctx.st
    lab = ctx.lab
    items: List[Flowable] = []
    if m.institution:
        items.append(Paragraph(_inline(m.institution), st["title"]))
        items.append(Spacer(1, 3))
    name = m.exam_name or m.title
    items.append(Paragraph(_inline(name), st["subtitle"]))
    sub = " · ".join(x for x in (m.subject, m.class_level and f"{lab.get('class_level')}: {m.class_level}", m.board) if x)
    if sub:
        items.append(Paragraph(_inline(sub), st["meta"]))
    if m.set_label:
        items.append(Paragraph(f"<b>{lab.get('set')} {_inline(m.set_label)}</b>", st["meta"]))
    if mode == "answer":
        items.append(Spacer(1, 3))
        items.append(Paragraph(_inline(lab.get("answer_key")), ParagraphStyle(
            "ak", parent=st["sec"], textColor=KEY, fontSize=12, leading=16)))
    elif mode == "combined":
        items.append(Paragraph(_inline(lab.get("teachers_copy")), ParagraphStyle(
            "ak", parent=st["sec"], textColor=KEY, fontSize=10.5, leading=14)))
    items.append(Spacer(1, 4))
    items.append(HRFlowable(width="100%", thickness=1, color=INK, spaceAfter=3))
    left = f"{lab.get('time_allowed')}: {duration_text(m.duration_minutes, lab)}"
    right = f"{lab.get('max_marks')}: {fmt_marks(m.total_marks)}"
    t = Table([[Paragraph(left, st["info"]), Paragraph(right, st["info_r"])]], colWidths=[FRAME_W / 2, FRAME_W / 2])
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    items.append(t)
    if mode == "question":
        extra = Table([[Paragraph(f"{lab.get('name')}: ______________________________", st["body"]),
                        Paragraph(f"{lab.get('roll_no')}: ________________", st["body"]),
                        Paragraph(_inline(f"{lab.get('date')}: {m.date}") if m.date else f"{lab.get('date')}: ____________",
                                  st["body"])]],
                      colWidths=[FRAME_W * 0.46, FRAME_W * 0.30, FRAME_W * 0.24])
        extra.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 5)]))
        items.append(extra)
    items.append(HRFlowable(width="100%", thickness=1, color=INK, spaceBefore=3, spaceAfter=5))
    if mode == "question" and m.instructions:
        items.append(Paragraph(lab.get("general_instructions"), st["h"]))
        for i, line in enumerate(m.instructions, 1):
            items.append(Paragraph(f"{i}.\u2002{ctx.rich(line)}", st["instr"]))
        items.append(Spacer(1, 4))
    return items


def _answer_key_grid(ctx: _Ctx, content: PaperContent, numbers: Dict[str, int]) -> List[Flowable]:
    pairs = []
    for sec in content.sections:
        for q in sec.questions:
            if q.type in ("mcq", "assertion_reason") and q.answer.correct_option is not None:
                pairs.append(f"{numbers[q.id]}. ({option_letter(q.answer.correct_option)})")
    if not pairs:
        return []
    cols = 8
    rows = [pairs[i:i + cols] for i in range(0, len(pairs), cols)]
    rows = [r + [""] * (cols - len(r)) for r in rows]
    t = Table([[Paragraph(c, ctx.st["cell"]) for c in r] for r in rows], colWidths=[FRAME_W / cols] * cols)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9bb39f")),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4faf6"))]))
    return [Paragraph(ctx.lab.get("objective_key"), ctx.st["h"]), Spacer(1, 2), t, Spacer(1, 8)]


def _story(ctx: _Ctx, content: PaperContent, mode: str, flat: bool) -> List[Flowable]:
    numbers = number_questions(content)
    story = _header(ctx, content, mode)
    if mode == "answer":
        story += _answer_key_grid(ctx, content, numbers)
    for sec in content.sections:
        if not sec.questions:
            continue
        head = Table([[Paragraph(f"{section_heading(sec, ctx.lab)}" + (f" — {_inline(sec.title)}" if sec.title else ""), ctx.st["sec"])],
                      [Paragraph(_inline(section_marks_line(sec, ctx.lab)), ctx.st["secsub"])]], colWidths=[FRAME_W])
        head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef1f6")),
                                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        block: List[Flowable] = [Spacer(1, 4), head]
        if sec.instruction and mode != "answer":
            block.append(Paragraph(f"<i>{_inline(sec.instruction)}</i>", ctx.st["cont"]))
        block.append(Spacer(1, 4))
        first = True
        for q in sec.questions:
            flow = _question_flowables(ctx, q, numbers[q.id], mode, flat)
            if first:
                story.append(KeepTogether(block + flow[:1]))
                story += flow[1:]
                first = False
            else:
                story += flow
    return story


def build_pdf(content: PaperContent, mode: str = "question", font_size: float = 11.0) -> bytes:
    """mode: question | answer | combined"""
    _register_fonts()
    lab = for_language(content.meta.language)
    shaping = indic.shaping_available() and languages.has_indic(content.model_dump_json())
    with tempfile.TemporaryDirectory(prefix="examforge-") as tmp:
        last_error: Optional[Exception] = None
        for flat in (False, True):
            ctx = _Ctx(font_size, tmp, lab, shaping)
            buf = io.BytesIO()
            doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * mm,
                                  bottomMargin=18 * mm, title=content.meta.title or content.meta.exam_name,
                                  author=content.meta.institution or "ExamForge", subject=content.meta.subject)
            frame = Frame(MARGIN, 18 * mm, FRAME_W, PAGE_H - 32 * mm, id="main", leftPadding=0, rightPadding=0,
                          topPadding=0, bottomPadding=0)
            doc.addPageTemplates([PageTemplate(id="p", frames=[frame])])
            label = lab.get({"question": "question_paper", "answer": "answer_key_short", "combined": "teachers_copy_short"}[mode])
            left = f"{content.meta.subject} · {content.meta.exam_name or content.meta.title} · {label}"

            class Canvas(_NumberedCanvas):
                footer_left = left
                footer_right = f"{lab.get('set_short')} {content.meta.set_label}" if content.meta.set_label else ""
                footer_lab = lab
                footer_shaping = shaping

            try:
                doc.build(_story(ctx, content, mode, flat), canvasmaker=Canvas)
                return buf.getvalue()
            except LayoutError as exc:  # a single question taller than a page: retry without the marks column
                last_error = exc
        raise RuntimeError(f"Could not lay out the PDF: {last_error}")
