"""
Turning uploaded files into plain text.

This module knows nothing about FastAPI or the LLM. It only takes bytes in
and gives text out, which makes it easy to test on its own.
"""

import io
import os
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# Max characters of document text we send to the model. Small local models
# have a limited context window (how much text they can "see" at once).
# Roughly 4 characters ≈ 1 token, so 12 000 chars ≈ 3 000 tokens.
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "12000"))

SUPPORTED_EXTENSIONS = (".pdf", ".txt")


class ExtractionError(Exception):
    """Raised when we cannot get usable text out of a file.

    `status_code` is the HTTP status the API should answer with.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def extract_text(filename: str, data: bytes) -> str:
    """Return the text inside an uploaded file, or raise ExtractionError."""
    if not data:
        raise ExtractionError("The uploaded file is empty.")

    name = (filename or "").lower()
    if name.endswith(".pdf"):
        text = _extract_pdf(data)
    elif name.endswith(".txt"):
        text = _extract_txt(data)
    else:
        raise ExtractionError(
            "Unsupported file type. Please upload a .pdf or .txt file.",
            status_code=415,  # 415 = "Unsupported Media Type"
        )

    text = _clean(text)
    if not text:
        if name.endswith(".pdf"):
            raise ExtractionError(
                "No text could be extracted from this PDF. It may be a scanned "
                "document (images of pages). Try a PDF with selectable text, or "
                "run it through OCR first.",
                status_code=422,
            )
        raise ExtractionError("The file contains no text.", status_code=422)
    return text


def _extract_pdf(data: bytes) -> str:
    # Every real PDF file starts with the bytes "%PDF".
    if not data.lstrip().startswith(b"%PDF"):
        raise ExtractionError("This file has a .pdf name but is not a valid PDF.")

    try:
        reader = PdfReader(io.BytesIO(data))
        # Some PDFs are "encrypted" with an empty password; try to open them.
        if reader.is_encrypted and not reader.decrypt(""):
            raise ExtractionError("This PDF is password protected.")
        # extract_text() can return None for pages without text.
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractionError:
        raise
    except PdfReadError as exc:
        raise ExtractionError(f"Could not read the PDF (it may be damaged): {exc}") from exc
    except Exception as exc:  # pypdf can also raise other errors on odd files
        raise ExtractionError(f"Could not read the PDF: {exc}") from exc

    return "\n\n".join(pages)


def _extract_txt(data: bytes) -> str:
    # Try UTF-8 first ("utf-8-sig" also removes an invisible BOM marker).
    # Fall back to Latin-1, which can decode any byte sequence.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _clean(text: str) -> str:
    """Tidy up whitespace so we don't waste the model's context on it."""
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)  # many spaces/tabs -> one space
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)  # 3+ newlines -> blank line
    return text.strip()


def truncate_text(text: str, max_chars: int = MAX_INPUT_CHARS) -> tuple[str, bool]:
    """Shorten text to at most `max_chars`.

    Returns (text, was_truncated). We try to cut at a paragraph or sentence
    boundary so the model doesn't get a half-finished sentence.
    """
    if len(text) <= max_chars:
        return text, False

    cut = text[:max_chars]
    # Look for a nice place to cut in the last 20% of the allowed text.
    min_pos = int(max_chars * 0.8)
    for separator in ("\n\n", ". ", "\n"):
        pos = cut.rfind(separator)
        if pos >= min_pos:
            cut = cut[: pos + 1]
            break
    return cut.strip(), True
