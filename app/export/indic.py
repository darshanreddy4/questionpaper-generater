"""Indic-script support for the PDF and DOCX exporters.

PDF: Noto Sans fonts for the nine scripts ship with the app (SIL OFL, see app/fonts/OFL.txt). ReportLab shapes them
through HarfBuzz (uharfbuzz) when a paragraph style has shaping=1, which is what produces conjuncts and moves vowel
signs to the right place. Text in a script is wrapped in a <font> tag; everything else keeps the Latin fonts.

DOCX: Word shapes Indic text itself. What it needs is the complex-script font and size on every run that has such text.
"""
from __future__ import annotations

import os
import re
import threading
from typing import Dict, Optional, Tuple

from docx.oxml.ns import qn
from reportlab.pdfbase import pdfmetrics, ttfonts
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import paragraph as _rl_paragraph

from app import languages

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
_TITLES = {s: s.capitalize() for s in languages.SCRIPTS}
_lock = threading.Lock()
_ready: Dict[str, Tuple[str, str]] = {}

# Only a plain space joins two words into one run: the Bengali and Gujarati fonts have no ASCII punctuation or digits.
_BRIDGE = re.compile(r"^ $")


def shaping_available() -> bool:
    try:
        import uharfbuzz  # noqa: F401
        return True
    except ImportError:
        return False


def font_names(script: str) -> Tuple[str, str]:
    return f"Indic-{script}", f"Indic-{script}-Bold"


def ensure_font(script: str) -> Tuple[str, str]:
    """Register the Regular and Bold fonts for a script (once) and return their ReportLab names."""
    with _lock:
        if script in _ready:
            return _ready[script]
        regular, bold = font_names(script)
        stem = "NotoSans" + {"oriya": "Oriya"}.get(script, _TITLES[script])
        for name, suffix in ((regular, "Regular"), (bold, "Bold")):
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, f"{stem}-{suffix}.ttf")))
        pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold, italic=regular, boldItalic=bold)
        pdfmetrics.registerFontFamily(bold, normal=regular, bold=bold, italic=regular, boldItalic=bold)
        _ready[script] = (regular, bold)
        return _ready[script]


def markup(text: str, bold: bool = False) -> str:
    """Wrap each stretch of Indic text in a <font> tag naming that script's font. Text without Indic letters is
    returned untouched."""
    if not languages.has_indic(text):
        return text
    pieces = languages.runs(text)
    merged = []
    for i, (script, seg) in enumerate(pieces):
        if (script is None and 0 < i < len(pieces) - 1 and _BRIDGE.match(seg)
                and pieces[i - 1][0] and pieces[i - 1][0] == pieces[i + 1][0]):
            script = pieces[i - 1][0]
        if merged and merged[-1][0] == script and script is not None:
            merged[-1] = (script, merged[-1][1] + seg)
        else:
            merged.append((script, seg))
    out = []
    for script, seg in merged:
        if script is None:
            out.append(seg)
            continue
        regular, bold_name = ensure_font(script)
        out.append(f'<font name="{bold_name if bold else regular}">{seg}</font>')
    return "".join(out)


# ----------------------------------------------------------------------------- ReportLab shaping fix

_original_shape = ttfonts.shapeFragWord


def _shape_mixed_word(w, *args, **kwargs):
    """ReportLab shapes a whole word with the font of its first fragment. A word that mixes fonts (an Indic word
    followed by a comma, or Latin letters stuck to Indic ones) then comes out with wrong glyphs. Shape each run of
    one font separately instead."""
    if isinstance(w, ttfonts.ShapedFragWord) or not ttfonts.uharfbuzz:
        return w
    items = list(w[1:])
    names = {getattr(f, "fontName", None) for f, _ in items if not hasattr(f, "cbDefn")}
    if len(names) <= 1:
        return _original_shape(w, *args, **kwargs)
    groups = []
    for item in items:
        key = None if hasattr(item[0], "cbDefn") else item[0].fontName
        if groups and (key is None or groups[-1][0] in (None, key)):
            groups[-1][1].append(item)
            if groups[-1][0] is None:
                groups[-1][0] = key
        else:
            groups.append([key, [item]])
    out, width, changed = [], 0.0, False
    for _, group in groups:
        shaped = _original_shape([0] + group, *args, **kwargs)
        if isinstance(shaped, ttfonts.ShapedFragWord):
            changed = True
            width += shaped[0]
            out.extend(shaped[1:])
        else:
            width += sum(0 if hasattr(f, "cbDefn") else pdfmetrics.stringWidth(t, f.fontName, f.fontSize)
                         for f, t in group)
            out.extend(group)
    if not changed:
        return w
    return ttfonts.makeShapedFragWord(w)([width] + out)


_rl_paragraph.shapeFragWord = _shape_mixed_word


# ----------------------------------------------------------------------------- DOCX

# Nirmala UI ships with Windows 8 and later and covers all nine scripts; Word on other systems substitutes its own.
DOCX_COMPLEX_FONT = "Nirmala UI"


_RPR_ORDER = ("w:rStyle", "w:rFonts", "w:b", "w:bCs", "w:i", "w:iCs", "w:caps", "w:smallCaps", "w:strike", "w:dstrike",
              "w:outline", "w:shadow", "w:emboss", "w:imprint", "w:noProof", "w:snapToGrid", "w:vanish", "w:webHidden",
              "w:color", "w:spacing", "w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect",
              "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em", "w:lang", "w:eastAsianLayout",
              "w:specVanish", "w:oMath")


def _child(rpr, tag: str):
    """The rPr child with this tag, created in schema order if missing (Word rejects some mis-ordered run properties)."""
    el = rpr.find(qn(tag))
    if el is not None:
        return el
    el = rpr.makeelement(qn(tag), {})
    later = {qn(t) for t in _RPR_ORDER[_RPR_ORDER.index(tag) + 1:]}
    for child in rpr:
        if child.tag in later:
            child.addprevious(el)
            return el
    rpr.append(el)
    return el


def prepare_docx_styles(doc, size_pt: float) -> None:
    """Document-wide defaults for complex scripts, so text typed later in Word also comes out right."""
    rpr = doc.styles["Normal"].element.get_or_add_rPr()
    _child(rpr, "w:rFonts").set(qn("w:cs"), DOCX_COMPLEX_FONT)
    _child(rpr, "w:szCs").set(qn("w:val"), str(int(round(size_pt * 2))))


def complex_script_pass(doc, paper_language: Optional[languages.Language] = None) -> None:
    """Word sizes and bolds complex-script text from szCs/bCs/iCs, not sz/b/i. Copy each run's own formatting across for
    every run that holds Indic text, and tag the run with its language."""
    roots = [doc.element.body]
    for section in doc.sections:
        roots.append(section.footer._element)
        roots.append(section.header._element)
    for root in roots:
        for r in root.iter(qn("w:r")):
            text = "".join(t.text or "" for t in r.iter(qn("w:t")))
            if not languages.has_indic(text):
                continue
            rpr = r.find(qn("w:rPr"))
            if rpr is None:
                rpr = r.makeelement(qn("w:rPr"), {})
                r.insert(0, rpr)
            sz = rpr.find(qn("w:sz"))
            if sz is not None:
                _child(rpr, "w:szCs").set(qn("w:val"), sz.get(qn("w:val")))
            for plain, cs in (("w:b", "w:bCs"), ("w:i", "w:iCs")):
                src = rpr.find(qn(plain))
                if src is not None:
                    dst = _child(rpr, cs)
                    val = src.get(qn("w:val"))
                    if val is not None:
                        dst.set(qn("w:val"), val)
            _child(rpr, "w:rFonts").set(qn("w:cs"), DOCX_COMPLEX_FONT)
            script = next((s for s, _ in languages.runs(text) if s), None)
            lang = paper_language if paper_language is not None and paper_language.script == script \
                else languages.language_for_script(script)
            if lang is not None:
                _child(rpr, "w:lang").set(qn("w:bidi"), lang.locale)
