"""HTML→text + cleaning utilities."""
from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

_SIGNATURE_HINTS = (
    "--", "—", "best regards", "kind regards", "regards,", "thanks,",
    "thank you,", "sincerely,", "sent from my", "cheers,",
)
_DISCLAIMER_HINTS = (
    "this email and any attachments", "confidential and may be privileged",
    "if you are not the intended recipient", "this message may contain confidential",
)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "img"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return _collapse_whitespace(text)


def _collapse_whitespace(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_signature(text: str) -> str:
    lines = text.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        low = line.strip().lower()
        if any(low.startswith(h) for h in _SIGNATURE_HINTS):
            cut = i
            break
    return "\n".join(lines[:cut]).strip()


def strip_disclaimer(text: str) -> str:
    low = text.lower()
    for hint in _DISCLAIMER_HINTS:
        idx = low.find(hint)
        if idx > 0:
            return text[:idx].rstrip()
    return text


def clean_message_body(html_or_text: str, *, is_html: bool = True) -> str:
    text = html_to_text(html_or_text) if is_html else _collapse_whitespace(html_or_text)
    text = strip_signature(text)
    text = strip_disclaimer(text)
    return text


def excerpt(text: str, limit: int = 4000) -> str:
    return text[:limit]


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()
