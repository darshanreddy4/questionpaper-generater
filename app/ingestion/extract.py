"""Turns an uploaded file into text plus figures.

Text extraction for the common formats is delegated to the ``book_to_skill`` package already in this
workspace (PDF/EPUB/DOCX/RTF/HTML/MOBI/plain text, with its Unicode sanitisation that strips hidden
prompt-injection characters). This module adds formats it does not cover (legacy DOC, PPTX, ODT, CSV,
images) and the vision paths for scanned pages and diagrams.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional
from xml.etree import ElementTree as ET

from book_to_skill.config import SUPPORTED_EXTENSIONS
from book_to_skill.exceptions import ExtractionError
from book_to_skill.sanitize import sanitize_extracted_text
from book_to_skill.utils import extract_single_file

from app import languages
from app.config import Settings
from app.ingestion import figures as fig_mod
from app.ingestion import vision
from app.llm.base import LLMError, LLMProvider
from app.llm.usage import UsageMeter

log = logging.getLogger("examforge.extract")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
EXTRA_EXTENSIONS = {".doc", ".pptx", ".odt", ".csv"} | IMAGE_EXTENSIONS
ALLOWED_EXTENSIONS = set(SUPPORTED_EXTENSIONS) | EXTRA_EXTENSIONS


@dataclass
class FigureDraft:
    page: int
    description: str
    kind: str
    image: Optional[bytes] = None
    diagram: Optional[dict] = None


@dataclass
class Extracted:
    text: str
    method: str
    figures: List[FigureDraft] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)


Progress = Callable[[str], None]


_ZW_AFTER_INDIC = re.compile("(?<=[\u0900-\u0D7F])([\u200c\u200d])")


def _sanitize(text: str):
    """The shared sanitiser drops zero-width characters as a prompt-injection precaution. After an Indic letter
    they are spelling (Malayalam chillu, Telugu and Kannada conjunct breaks), so carry them through it."""
    protected = _ZW_AFTER_INDIC.sub(lambda m: "\ue000" if m.group(1) == "\u200d" else "\ue001", text)
    cleaned, removed = sanitize_extracted_text(protected)
    return cleaned.replace("\ue000", "\u200d").replace("\ue001", "\u200c"), removed


def safe_display_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "document")[:200]


def _xml_text(data: bytes, text_tags: set, break_tags: set) -> str:
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ExtractionError("Document contains an XML DOCTYPE/ENTITY declaration and was rejected.")
    root = ET.fromstring(data)
    out: List[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in text_tags and el.text:
            out.append(el.text)
        elif tag in break_tags:
            out.append("\n")
    return "".join(out)


def _extract_pptx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        slides = sorted((n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        parts = []
        for i, name in enumerate(slides, 1):
            parts.append(f"## Slide {i}\n" + _xml_text(zf.read(name), {"t"}, {"p"}))
    return "\n\n".join(parts)


def _extract_odt(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        data = zf.read("content.xml")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ExtractionError("Document contains an XML DOCTYPE/ENTITY declaration and was rejected.")
    root = ET.fromstring(data)
    out: List[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("p", "h"):
            out.append("".join(el.itertext()))
    return "\n".join(out)


def _extract_legacy_doc(path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        if shutil.which("textutil"):  # macOS
            cmd = ["textutil", "-convert", "txt", "-output", str(Path(tmp) / "out.txt"), str(path)]
        elif shutil.which("soffice"):
            cmd = ["soffice", "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(path)]
        elif shutil.which("antiword"):
            res = subprocess.run(["antiword", str(path)], capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout
            raise ExtractionError("antiword could not read this .doc file.")
        else:
            raise ExtractionError(
                "Legacy .doc files need textutil (macOS), LibreOffice or antiword. Save the file as .docx and retry.")
        subprocess.run(cmd, capture_output=True, timeout=180)
        out = next(Path(tmp).glob("*.txt"), None)
        if not out:
            raise ExtractionError("Could not convert this .doc file; save it as .docx and retry.")
        return out.read_text(encoding="utf-8", errors="replace")


def _extract_figures(images: List[fig_mod.FigureImage], provider: LLMProvider, progress: Progress,
                     workers: int, meter: Optional[UsageMeter] = None) -> List[FigureDraft]:
    results: List[FigureDraft] = []
    if not images:
        return results
    progress(f"Analysing {len(images)} figure(s)")

    def work(img: fig_mod.FigureImage) -> Optional[FigureDraft]:
        info = vision.analyse_figure(provider, img.png, meter)
        if not info:
            return None
        return FigureDraft(img.page, info["description"], info["kind"], img.png, info["diagram"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for draft in pool.map(work, images):
            if draft:
                results.append(draft)
    return results


def _ocr_pages(pages, provider: LLMProvider, progress: Progress, workers: int, meter: Optional[UsageMeter] = None):
    texts: Dict[int, str] = {}
    figs: List[FigureDraft] = []

    def work(item):
        pno, png = item
        return pno, png, vision.transcribe_page(provider, png, meter)

    progress(f"Reading {len(pages)} scanned page(s) with the vision model")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for pno, png, res in pool.map(work, pages):
            texts[pno] = res["text"]
            for f in res["figures"]:
                figs.append(FigureDraft(pno, f["description"], f["kind"], png if f["diagram"] is None else None,
                                        f["diagram"]))
    return "\n\n".join(texts[k] for k in sorted(texts)), figs


def extract_source(path: Path, provider: LLMProvider, settings: Settings, progress: Progress = lambda _m: None,
                   meter: Optional[UsageMeter] = None, vision: bool = False) -> Extracted:
    """*vision* is the user's explicit opt-in to AI reading of pictures, diagrams and scans (it costs tokens)."""
    ext = path.suffix.lower()
    workers = settings.llm_max_concurrency
    figures: List[FigureDraft] = []

    if ext in IMAGE_EXTENSIONS:
        if not vision:
            raise ExtractionError("Pictures can only be read with AI. Upload it again with 'Read pictures, diagrams and "
                                  "scans with AI' ticked.")
        if not provider.supports_vision or provider.is_offline:
            raise ExtractionError("Reading images needs an AI model. Add your own API key under AI & billing, or ask "
                                  "your administrator to enable one.")
        png = path.read_bytes()
        text, figures = _ocr_pages([(1, png)], provider, progress, 1, meter)
        text, _ = _sanitize(text)
        if not text.strip() and not figures:
            raise ExtractionError("No readable content found in the image.")
        return Extracted(text, "vision-ocr", figures, {"pages": 1})

    stats: Dict = {}
    if ext == ".pptx":
        text, method = _extract_pptx(path), "pptx-xml"
    elif ext == ".odt":
        text, method = _extract_odt(path), "odt-xml"
    elif ext == ".csv":
        text, method = path.read_text(encoding="utf-8", errors="replace"), "plain-text"
    elif ext == ".doc":
        text, method = _extract_legacy_doc(path), "legacy-doc"
    else:
        try:
            result = extract_single_file(path, "text", "no")
            text, method = result["text"], result["extraction_method"]
            stats = {k: result.get(k) for k in ("pages", "words", "chapters_detected", "chapters_method", "file_size_mb")}
        except ExtractionError as exc:
            scanned = ext == ".pdf" and ("scanned" in str(exc) or "Could not extract text from PDF" in str(exc))
            if not scanned:
                raise
            if not vision:
                raise ExtractionError(
                    "This PDF has no text layer (it looks scanned). Upload it again with 'Read pictures, diagrams and "
                    "scans with AI' ticked, or run OCR on it first.") from exc
            if provider.is_offline or not provider.supports_vision:
                raise ExtractionError(
                    "This PDF has no text layer (it looks scanned) and no AI model is available to read it. Add your "
                    "own API key under AI & billing, or run OCR on the file first.") from exc
            pages = fig_mod.pdf_page_images(str(path), settings.vision_ocr_max_pages)
            text, figures = _ocr_pages(pages, provider, progress, workers, meter)
            text, _ = _sanitize(text)
            if not text.strip():
                raise ExtractionError("The vision model could not read any text from this PDF.") from exc
            return Extracted(text, "vision-ocr", figures, {"pages": len(pages)})

    text, removed = _sanitize(text)
    if removed:
        stats["invisible_characters_removed"] = removed
    if not text.strip():
        raise ExtractionError("No readable text found in this file.")

    if vision and provider.supports_vision and not provider.is_offline and settings.vision_max_images > 0:
        images: List[fig_mod.FigureImage] = []
        try:
            if ext == ".pdf":
                progress("Locating diagrams")
                images = fig_mod.pdf_figures(str(path), settings.vision_max_images)
            elif ext == ".docx":
                images = fig_mod.docx_images(str(path), settings.vision_max_images)
        except Exception as exc:  # figure discovery must never fail the whole upload
            log.warning("figure discovery failed for %s: %s", path.name, exc)
        try:
            figures = _extract_figures(images, provider, progress, workers, meter)
        except LLMError as exc:
            log.warning("figure analysis aborted: %s", exc)
    stats["figures"] = len(figures)
    script = languages.dominant_script(text)
    if script:
        guess = languages.language_for_script(script)
        stats["script"], stats["language_hint"] = languages.SCRIPTS[script], guess.name if guess else ""
    return Extracted(text, method, figures, stats)
