"""Plain-text extraction from uploaded evidence, so documents become searchable."""
import io

MAX_CHARS = 200_000
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".log", ".json", ".xml", ".html", ".htm", ".eml")


def extract_text(filename, data):
    name = filename.lower()
    text = ""
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif name.endswith(".docx"):
        from docx import Document

        document = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in document.paragraphs)
    elif name.endswith(TEXT_SUFFIXES):
        text = data.decode("utf-8", errors="replace")
    return text.replace("\x00", "").strip()[:MAX_CHARS]
