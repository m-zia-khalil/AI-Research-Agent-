"""Reading the user's own documents for My Document Workspace.

Supports PDF (text-based), DOCX, TXT, MD and pasted text. Scanned PDFs (images
of pages) have no text layer; reading them needs OCR, which this app does not
include, so we detect them and explain instead of returning garbage.
"""

from __future__ import annotations

import io
import re

from utils import AppError, estimate_tokens, get_logger

log = get_logger(__name__)

MAX_WORDS = 60_000          # above this, ask the user to trim (token budget, see Phase 0 report)
MIN_CHARS_PER_PAGE = 60     # PDFs with less text per page than this are probably scans


def read_document(file_name: str, data: bytes) -> dict:
    """Read an uploaded file. Returns {text, words, pages, kind, note}.

    Raises AppError('scanned_pdf') for image-only PDFs and AppError('file') for
    anything unreadable.
    """
    name = file_name.lower()
    try:
        if name.endswith(".pdf"):
            result = _read_pdf(data)
        elif name.endswith(".docx"):
            result = _read_docx(data)
        elif name.endswith((".txt", ".md", ".markdown")):
            result = {"text": _decode(data), "pages": None, "kind": "text", "note": ""}
        else:
            raise AppError("file", f"unsupported file type: {file_name}")
    except AppError:
        raise
    except Exception as error:
        log.exception("Could not read %s", file_name)
        raise AppError("file", f"{type(error).__name__}: {error}")
    return _finish(result)


def read_pasted(text: str) -> dict:
    """Wrap pasted text in the same shape as an uploaded file."""
    return _finish({"text": text or "", "pages": None, "kind": "pasted", "note": ""})


def _finish(result: dict) -> dict:
    text = re.sub(r"[ \t]+", " ", result["text"]).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    words = len(text.split())
    if words < 30:
        raise AppError("file", "the document has fewer than 30 words of readable text")
    if words > MAX_WORDS:
        raise AppError("too_large", f"{words:,} words; the limit is {MAX_WORDS:,}")
    result.update(text=text, words=words)
    return result


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _read_pdf(data: bytes) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise AppError("file", "the PDF is password-protected")
    pages = [(page.extract_text() or "") for page in reader.pages]
    total = sum(len(p.strip()) for p in pages)
    if not pages or total / len(pages) < MIN_CHARS_PER_PAGE:
        raise AppError("scanned_pdf", f"{total} characters over {len(pages)} pages")
    empty = sum(1 for p in pages if len(p.strip()) < MIN_CHARS_PER_PAGE)
    note = f"{empty} of {len(pages)} pages had little or no text (images or scans) and were skipped." if empty else ""
    return {"text": "\n\n".join(pages), "pages": len(pages), "kind": "pdf", "note": note}


def _read_docx(data: bytes) -> dict:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
        lines.append(f"\n## {text}" if style.startswith("heading") or style == "title" else text)
    for table in document.tables:        # tables are added after the body text
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return {"text": "\n".join(lines), "pages": None, "kind": "docx", "note": ""}


def chunk_text(text: str, max_tokens: int = 2800) -> list[str]:
    """Split text into chunks of at most ~max_tokens, breaking between paragraphs."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current, size = [], [], 0
    for paragraph in paragraphs:
        cost = estimate_tokens(paragraph)
        if cost > max_tokens:                     # one huge paragraph: split by sentences
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                cost_s = estimate_tokens(sentence)
                if size + cost_s > max_tokens and current:
                    chunks.append("\n\n".join(current))
                    current, size = [], 0
                current.append(sentence[: int(max_tokens * 3.5)])
                size += cost_s
            continue
        if size + cost > max_tokens and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(paragraph)
        size += cost
    if current:
        chunks.append("\n\n".join(current))
    return chunks
