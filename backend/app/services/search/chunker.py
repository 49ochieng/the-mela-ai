"""
Mela AI - Text Chunker
Token-aware chunking with sentence-boundary respect and heading preservation.
Uses tiktoken (cl100k_base) for accurate token counting.
Injects document metadata (title, source, path) into each chunk header
so embeddings capture document context even for isolated chunks.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None
    logger.warning("tiktoken not available; falling back to character-based chunking")

_SENTENCE_SEPS = [". ", ".\n", "! ", "!\n", "? ", "?\n", "\n\n"]

# Matches Markdown-style headings (# through ####)
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)


def _token_len(text: str) -> int:
    if _ENC:
        return len(_ENC.encode(text))
    return len(text) // 4  # rough approximation


def _split_sentences(text: str) -> List[str]:
    """Split text at sentence boundaries."""
    sentences: List[str] = []
    current = ""
    i = 0
    while i < len(text):
        matched = False
        for sep in _SENTENCE_SEPS:
            if text[i:i + len(sep)] == sep:
                current += sep
                if current.strip():
                    sentences.append(current)
                current = ""
                i += len(sep)
                matched = True
                break
        if not matched:
            current += text[i]
            i += 1
    if current.strip():
        sentences.append(current)
    return sentences


def _extract_last_heading(text: str) -> str:
    """Return the last Markdown heading found in a block of text."""
    matches = list(_HEADING_RE.finditer(text))
    return matches[-1].group(2).strip() if matches else ""


def _build_chunk_header(
    title: str,
    source_type: str = "",
    path: str = "",
    section: str = "",
) -> str:
    """Build a metadata header that gets prepended to each chunk.

    This ensures embeddings capture the document context even when the
    chunk text alone would be ambiguous.
    """
    parts: List[str] = []
    if title:
        parts.append(f"Document: {title}")
    if source_type:
        parts.append(f"Source: {source_type}")
    if path:
        # Trim Graph path prefix for readability
        clean = path.replace("/drive/root:", "").strip("/")
        if clean:
            parts.append(f"Path: {clean}")
    if section:
        parts.append(f"Section: {section}")
    if not parts:
        return ""
    return " | ".join(parts) + "\n\n"


class TextChunker:
    """
    Splits text into overlapping chunks bounded by token count.
    Chunks respect sentence boundaries where possible.
    Preserves section headings across chunk boundaries.
    """

    def __init__(self, chunk_size: int = 1000, overlap: int = 150) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(
        self,
        text: str,
        chunk_size: Optional[int] = None,
        overlap: Optional[int] = None,
    ) -> List[str]:
        size = chunk_size or self.chunk_size
        lap = overlap or self.overlap
        sentences = _split_sentences(text)

        chunks: List[str] = []
        current: List[str] = []
        current_tokens = 0

        for sentence in sentences:
            s_tokens = _token_len(sentence)

            if current_tokens + s_tokens > size and current:
                chunks.append("".join(current))
                # Keep overlap: pop sentences from the front until tokens <= overlap
                while current and current_tokens > lap:
                    removed = current.pop(0)
                    current_tokens -= _token_len(removed)

            current.append(sentence)
            current_tokens += s_tokens

        if current:
            chunks.append("".join(current))

        return [c.strip() for c in chunks if c.strip()]

    def chunk_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        chunk_size: Optional[int] = None,
        overlap: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict]:
        """Chunk a document and inject metadata header into each chunk.

        Args:
            metadata: Optional dict with keys like ``source_type``, ``path``,
                      ``file_type`` that get injected into each chunk header
                      so embeddings capture document context.
        """
        meta = metadata or {}
        header = _build_chunk_header(
            title=title,
            source_type=meta.get("source_type", ""),
            path=meta.get("path", ""),
        )
        header_tokens = _token_len(header) if header else 0

        # Reduce effective chunk size by header overhead
        effective_size = (chunk_size or self.chunk_size) - header_tokens
        if effective_size < 200:
            # Header too large; skip it to avoid degenerate tiny chunks
            header = ""
            effective_size = chunk_size or self.chunk_size

        chunks = self.chunk(content, effective_size, overlap)
        results = []
        running_heading = ""
        text_so_far = ""

        for i, text in enumerate(chunks):
            # Track the most recent heading across chunks
            heading = _extract_last_heading(text)
            if heading:
                running_heading = heading

            # Build per-chunk header with current section
            chunk_header = _build_chunk_header(
                title=title,
                source_type=meta.get("source_type", ""),
                path=meta.get("path", ""),
                section=running_heading,
            ) if header else ""

            chunk_id = hashlib.sha256(f"{doc_id}:{i}".encode()).hexdigest()[:32]
            results.append({
                "chunk_id": chunk_id,
                "chunk_index": i,
                "doc_id": doc_id,
                "title": title,
                "content": chunk_header + text,
                "section": running_heading,
            })
        return results


# Singleton
chunker = TextChunker()
