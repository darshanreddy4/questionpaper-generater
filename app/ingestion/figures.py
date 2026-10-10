from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple

log = logging.getLogger("examforge.figures")


@dataclass
class FigureImage:
    page: int
    png: bytes
    area: float


def _merge(rects):
    """Union overlapping rectangles so one figure made of many vector paths is cropped once."""
    rects = [r for r in rects]
    changed = True
    while changed:
        changed = False
        out = []
        while rects:
            r = rects.pop()
            for i, o in enumerate(out):
                if r.intersects(o) or (r + (-8, -8, 8, 8)).intersects(o):
                    out[i] = o | r
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def pdf_figures(path: str, max_figures: int, dpi: int = 150) -> List[FigureImage]:
    """Crop embedded images and clusters of vector drawings from a PDF (diagrams are often vector art)."""
    import fitz  # PyMuPDF

    found: List[FigureImage] = []
    doc = fitz.open(path)
    try:
        for pno in range(len(doc)):
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            rects = []
            for info in page.get_image_info():
                rects.append(fitz.Rect(info["bbox"]))
            try:
                paths = page.get_drawings()
                if len(paths) >= 4:
                    rects.extend(page.cluster_drawings(drawings=paths))
            except Exception as exc:  # malformed content streams
                log.info("drawing clustering failed on page %s: %s", pno + 1, exc)
            for r in _merge(rects):
                r = r & page.rect
                if r.is_empty or r.width < 50 or r.height < 40:
                    continue
                if r.width * r.height > 0.85 * page_area:  # a full-page scan, handled by OCR instead
                    continue
                clip = r + (-6, -6, 6, 6)
                pix = page.get_pixmap(clip=clip & page.rect, dpi=dpi)
                found.append(FigureImage(pno + 1, pix.tobytes("png"), r.width * r.height))
    finally:
        doc.close()
    found.sort(key=lambda f: -f.area)
    kept = found[:max_figures]
    kept.sort(key=lambda f: f.page)
    return kept


def pdf_page_images(path: str, max_pages: int, dpi: int = 130) -> List[Tuple[int, bytes]]:
    import fitz

    out: List[Tuple[int, bytes]] = []
    doc = fitz.open(path)
    try:
        for pno in range(min(len(doc), max_pages)):
            out.append((pno + 1, doc[pno].get_pixmap(dpi=dpi).tobytes("png")))
    finally:
        doc.close()
    return out


def docx_images(path: str, max_figures: int) -> List[FigureImage]:
    import io
    import zipfile

    from PIL import Image

    found: List[FigureImage] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.startswith("word/media/") or not name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp")):
                continue
            info = zf.getinfo(name)
            if info.file_size > 15 * 1024 * 1024:
                continue
            data = zf.read(name)
            try:
                with Image.open(io.BytesIO(data)) as im:
                    if im.width < 80 or im.height < 60:
                        continue
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, "PNG")
                    found.append(FigureImage(0, buf.getvalue(), float(im.width * im.height)))
            except Exception:
                continue
    found.sort(key=lambda f: -f.area)
    return found[:max_figures]
