"""Resume file parser — extracts text from PDF, DOCX, and TXT files."""

from io import BytesIO


class ResumeParser:
    """Parse resume files into plain text.

    Supported types: pdf, docx, txt.
    No OCR — image-based PDFs will return minimal/empty text.
    Legacy .doc is not supported.
    """

    def parse(self, data: bytes, file_type: str) -> str:
        file_type_lower = file_type.lower()
        if file_type_lower == "txt":
            return self._parse_txt(data)
        elif file_type_lower == "pdf":
            return self._parse_pdf(data)
        elif file_type_lower == "docx":
            return self._parse_docx(data)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")

    def _parse_txt(self, data: bytes) -> str:
        return data.decode("utf-8", errors="replace")

    def _parse_pdf(self, data: bytes) -> str:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)

    def _parse_docx(self, data: bytes) -> str:
        from docx import Document

        doc = Document(BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
