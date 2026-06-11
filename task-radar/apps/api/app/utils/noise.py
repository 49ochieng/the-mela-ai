"""Pre-AI noise filtering."""
from __future__ import annotations

import re

_NOREPLY_PATTERNS = re.compile(
    r"(noreply|no-reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|notifications?@|"
    r"calendar-?notification|calendly|asana|jira@|github@|gitlab@|slack@|zoom@|teams@|"
    r"linkedin|substack|medium\.com|mailchimp|sendgrid|hubspot|marketo)",
    re.IGNORECASE,
)
_NEWSLETTER_HINTS = (
    "unsubscribe", "view in browser", "manage your preferences",
    "you are receiving this", "update your subscription",
)
_AUTOREPLY_HINTS = ("out of office", "automatic reply", "auto-reply", "automatic response")
_EMOJI_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
# Verbs/nouns that suggest action even in short messages
_ACTION_HINTS = re.compile(
    r"\b(please|pls|kindly|need|require|due|deadline|asap|today|tomorrow|"
    r"review|approve|sign|send|share|update|fix|deploy|merge|ship|deliver|"
    r"submit|file|complete|finish|prepare|draft|schedule|book|call|meeting|"
    r"follow up|action|todo|to-do|task|owner|assign|by eod|by cob|"
    r"can you|could you|would you|will you|need you to|able to)\b",
    re.IGNORECASE,
)


def is_noise_email(sender_email: str | None, subject: str | None, body: str | None) -> bool:
    if sender_email and _NOREPLY_PATTERNS.search(sender_email):
        return True
    text = f"{subject or ''}\n{body or ''}".lower()
    if any(h in text for h in _AUTOREPLY_HINTS):
        return True
    hits = sum(1 for h in _NEWSLETTER_HINTS if h in text)
    return hits >= 2


def is_noise_teams(body: str | None, mentions_user: bool) -> bool:
    """Pre-AI Teams filter. With mentions_only OFF the goal is: drop
    obvious garbage (empty, emoji-only, very short with no action verb),
    let the GPT extractor decide everything else."""
    body = (body or "").strip()
    if not body:
        return True
    if _EMOJI_ONLY.match(body):
        return True
    # Very short and no action verb and not a mention → treat as chatter.
    if len(body) < 12 and not mentions_user and not _ACTION_HINTS.search(body):
        return True
    return False
