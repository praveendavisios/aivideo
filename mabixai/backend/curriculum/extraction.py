"""Text extraction from curriculum documents.

Returns a list of pages: {"page": n, "text": str, "method": "text"|"ocr"}.
Headings are preserved as markdown markers ("# ", "## ") where the source
format exposes them (DOCX heading styles, PPTX slide titles).
"""
import io
import logging
import os

from ..config import settings

log = logging.getLogger("mabcat.extract")
MIN_TEXT_CHARS = 40  # a PDF page with less text than this is treated as scanned


class ExtractionError(Exception):
    pass


def _ocr_image(img):
    try:
        import pytesseract
    except ImportError as exc:
        raise ExtractionError("OCR needs pytesseract + tesseract-ocr installed") from exc
    from PIL import ImageOps
    img = ImageOps.grayscale(img)
    if img.width < 1200:  # upscale small scans — tesseract prefers ~300 dpi
        scale = 1200 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    try:
        return pytesseract.image_to_string(img, lang=settings.OCR_LANGS)
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractionError("tesseract-ocr binary not found on the server") from exc


def extract_pdf(path):
    from pypdf import PdfReader
    from PIL import Image
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ExtractionError("PDF is password protected") from exc
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        method = "text"
        if len(text.strip()) < MIN_TEXT_CHARS:
            ocr_parts = []
            for im in getattr(page, "images", []) or []:
                try:
                    ocr_parts.append(_ocr_image(Image.open(io.BytesIO(im.data))))
                except ExtractionError:
                    raise
                except Exception as exc:  # unreadable embedded image
                    log.warning("page %s image skipped: %s", i, exc)
            if ocr_parts:
                text, method = "\n".join(ocr_parts), "ocr"
        pages.append({"page": i, "text": text, "method": method})
    return pages


def extract_docx(path):
    import docx
    d = docx.Document(path)
    lines = []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            lines.append("")
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("heading") or style == "title":
            lvl = 1 if style == "title" else int("".join(c for c in style if c.isdigit()) or 1)
            lines.append("#" * min(lvl, 4) + " " + t)
        elif "list" in style:
            lines.append("- " + t)
        else:
            lines.append(t)
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return [{"page": 1, "text": "\n".join(lines), "method": "text"}]


def extract_pptx(path):
    from pptx import Presentation
    prs = Presentation(path)
    pages = []
    for i, slide in enumerate(prs.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else ""
        body = []
        for shape in slide.shapes:
            if shape == slide.shapes.title or not getattr(shape, "has_text_frame", False):
                continue
            for para in shape.text_frame.paragraphs:
                t = "".join(r.text for r in para.runs).strip()
                if t:
                    body.append(("- " if para.level else "") + t)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                body.append(notes)
        text = (f"## {title}\n" if title else "") + "\n".join(body)
        pages.append({"page": i, "text": text, "method": "text"})
    return pages


def extract_text_file(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return [{"page": 1, "text": raw.decode(enc), "method": "text"}]
        except UnicodeDecodeError:
            continue
    raise ExtractionError("Unreadable text encoding")


def extract_image(path):
    from PIL import Image
    with Image.open(path) as img:
        return [{"page": 1, "text": _ocr_image(img.convert("RGB")), "method": "ocr"}]


EXTRACTORS = {"pdf": extract_pdf, "docx": extract_docx, "pptx": extract_pptx, "txt": extract_text_file,
              "md": extract_text_file, "png": extract_image, "jpg": extract_image, "jpeg": extract_image,
              "webp": extract_image, "tif": extract_image, "tiff": extract_image}


def extract(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    fn = EXTRACTORS.get(ext)
    if not fn:
        raise ExtractionError(f"Unsupported document type .{ext}")
    try:
        pages = fn(path)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Could not read {os.path.basename(path)}: {exc}") from exc
    if not any(p["text"].strip() for p in pages):
        raise ExtractionError("No text could be extracted (empty document, or scanned without OCR support)")
    return pages


def validate_magic(path, ext):
    """Reject files whose content does not match the extension."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    sig = {"pdf": [b"%PDF"], "docx": [b"PK"], "pptx": [b"PK"], "png": [b"\x89PNG"], "jpg": [b"\xff\xd8"],
           "jpeg": [b"\xff\xd8"], "webp": [b"RIFF"], "tif": [b"II*\x00", b"MM\x00*"], "tiff": [b"II*\x00", b"MM\x00*"]}
    if ext in sig and not any(head.startswith(s) for s in sig[ext]):
        raise ExtractionError(f"File content does not look like a .{ext} file")
