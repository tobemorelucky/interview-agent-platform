"""Markdown text cleaning and chunking utilities."""

import re

from interview_api.core.config import settings


def clean_markdown(text: str) -> str:
    """Lightly clean markdown while preserving semantic structure.

    - Strips YAML front matter
    - Removes inline images, keeps alt text
    - Converts links to plain text
    - Preserves headings, lists, code blocks
    - Normalises excessive blank lines
    """
    # Strip YAML front matter (--- ... ---)
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)

    # Remove inline images: ![alt](url) -> alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)

    # Convert links: [text](url) -> text
    text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", text)

    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Normalise blank lines (max 2 consecutive)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Strip leading/trailing whitespace
    text = text.strip()

    return text


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    min_size: int | None = None,
) -> list[str]:
    """Split text into overlapping chunks by paragraph boundaries.

    Attempts to split on paragraph boundaries (double newline), then falls
    back to character-level splitting for very long paragraphs.
    """
    if chunk_size is None:
        chunk_size = settings.kb_chunk_size
    if chunk_overlap is None:
        chunk_overlap = settings.kb_chunk_overlap
    if min_size is None:
        min_size = settings.kb_chunk_min_size

    text = clean_markdown(text)
    paragraphs = text.split("\n\n")

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            if current:
                if len(current) >= min_size:
                    chunks.append(current)
                current = ""

            if len(para) > chunk_size:
                # Long paragraph: character-level sliding window
                for i in range(0, len(para), chunk_size - chunk_overlap):
                    piece = para[i : i + chunk_size]
                    if len(piece) >= min_size:
                        chunks.append(piece)
            else:
                current = para

    if current and len(current) >= min_size:
        chunks.append(current)

    return chunks
