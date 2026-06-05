"""
Mela AI - Email Service

Utilities for:
- Professional HTML email formatting with branded template
- Normalizing raw Graph API message objects to clean dicts
- Thread / task helpers used by tool_executor
"""

from __future__ import annotations

import html
import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── HTML email template ────────────────────────────────────────────────────────

_EMAIL_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings>
<o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<style>
  /* Reset */
  body, table, td, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  table, td {{ mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
  img {{ -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }}
  body {{
    margin: 0; padding: 0; width: 100% !important; min-width: 100%;
    background-color: #f3f4f6;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  }}
  /* Outer wrapper */
  .email-outer {{
    background-color: #f3f4f6;
    padding: 28px 16px;
  }}
  /* Card */
  .email-card {{
    max-width: 600px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04);
  }}
  /* Brand header bar */
  .email-header {{
    background: #2f5597;
    padding: 18px 36px;
    text-align: left;
  }}
  .email-header .brand {{
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.01em;
  }}
  /* Body */
  .email-body {{
    padding: 32px 36px 28px;
    font-size: 15px;
    line-height: 1.7;
    color: #1f2937;
  }}
  /* Headings */
  .email-body h1 {{
    font-size: 20px; font-weight: 700; color: #111827;
    margin: 0 0 16px; line-height: 1.3;
  }}
  .email-body h2 {{
    font-size: 17px; font-weight: 600; color: #1f2937;
    margin: 20px 0 10px; line-height: 1.35;
  }}
  .email-body h3 {{
    font-size: 15px; font-weight: 600; color: #374151;
    margin: 16px 0 8px; line-height: 1.4;
  }}
  /* Paragraphs */
  .email-body p {{
    margin: 0 0 14px;
    color: #1f2937;
  }}
  .email-body p:last-child {{ margin-bottom: 0; }}
  /* Lists */
  .email-body ul, .email-body ol {{
    margin: 0 0 14px;
    padding-left: 24px;
  }}
  .email-body li {{ margin-bottom: 6px; color: #1f2937; }}
  /* Emphasis */
  .email-body strong {{ font-weight: 700; color: #111827; }}
  .email-body em {{ font-style: italic; }}
  /* Links */
  .email-body a {{ color: #2f5597; text-decoration: none; }}
  .email-body a:hover {{ text-decoration: underline; }}
  /* Inline code */
  .email-body code {{
    background: #f3f4f6; color: #374151;
    padding: 1px 5px; border-radius: 4px;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 13px;
  }}
  /* Blockquote / callout */
  .email-body blockquote {{
    border-left: 3px solid #2f5597;
    margin: 14px 0; padding: 8px 16px;
    background: #eff4ff; border-radius: 0 6px 6px 0;
    color: #374151; font-style: italic;
  }}
  /* Divider */
  .email-body hr {{
    border: none; border-top: 1px solid #e5e7eb;
    margin: 20px 0;
  }}
  /* Signature */
  .email-signature {{
    margin-top: 28px;
    padding-top: 20px;
    border-top: 1px solid #e5e7eb;
  }}
  .email-signature .sig-name {{
    font-size: 14px; font-weight: 600; color: #1f2937;
    display: block; margin-bottom: 2px;
  }}
  .email-signature .sig-org {{
    font-size: 13px; color: #6b7280;
    display: block;
  }}
  /* Footer */
  .email-footer {{
    background: #f9fafb;
    border-top: 1px solid #e5e7eb;
    padding: 14px 36px;
    text-align: center;
    font-size: 12px;
    color: #9ca3af;
  }}
  /* Responsive */
  @media only screen and (max-width: 480px) {{
    .email-body {{ padding: 24px 20px 20px; }}
    .email-header {{ padding: 14px 20px; }}
    .email-footer {{ padding: 12px 20px; }}
  }}
</style>
</head>
<body>
<div class="email-outer">
  <div class="email-card">
    <div class="email-header">
      <span class="brand">Armely</span>
    </div>
    <div class="email-body">
      {body_html}
      {signature_html}
    </div>
    <div class="email-footer">
      Sent via Mela AI &middot; Armely &middot; <a href="https://armely.com" style="color:#9ca3af;">armely.com</a>
    </div>
  </div>
</div>
</body>
</html>"""

_SIGNATURE_TEMPLATE = """\
<div class="email-signature">
  <span class="sig-name">{sender_name}</span>
  <span class="sig-org">{org_name}</span>
</div>"""


def _markdown_to_html(text: str) -> str:
    """
    Markdown → HTML conversion for email bodies.
    Handles: # headings, **bold**, *italic*, `code`, [links](url),
    blockquotes, horizontal rules, line-breaks, paragraphs,
    unordered lists (- item), ordered lists (1. item).
    """
    # Escape HTML special chars first (protect < > & " in non-markdown text)
    text = html.escape(text, quote=False)

    # Re-unescape markdown link brackets that got escaped
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = html.escape(text, quote=False)  # re-escape after link processing

    # Headings (#, ##, ###) — must be at start of line
    def _heading(m: re.Match) -> str:
        level = min(len(m.group(1)), 3)
        content = m.group(2).strip()
        return f"<h{level}>{content}</h{level}>"

    text = re.sub(r"^(#{1,3})\s+(.+)$", _heading, text, flags=re.MULTILINE)

    # Bold and italic
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text, flags=re.DOTALL)
    text = re.sub(r"\*\*(.+?)\*\*",      r"<strong>\1</strong>",          text, flags=re.DOTALL)
    text = re.sub(r"\*([^*\n]+?)\*",     r"<em>\1</em>",                  text)
    text = re.sub(r"_([^_\n]+?)_",       r"<em>\1</em>",                  text)

    # Inline code (safe — run before link processing to avoid collisions)
    text = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", text)

    # Markdown links [text](url)
    text = re.sub(
        r'\[([^\]]+)\]\((https?://[^\)]+)\)',
        r'<a href="\2">\1</a>',
        text,
    )

    lines = text.split("\n")
    output: list[str] = []
    in_ul = False
    in_ol = False
    in_blockquote = False

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            output.append("</ul>")
            in_ul = False
        if in_ol:
            output.append("</ol>")
            in_ol = False

    for line in lines:
        stripped = line.rstrip()

        # Horizontal rule
        if re.match(r"^[-*_]{3,}$", stripped):
            _close_lists()
            if in_blockquote:
                output.append("</blockquote>")
                in_blockquote = False
            output.append("<hr>")
            continue

        # Blockquote
        if stripped.startswith("&gt; ") or stripped == "&gt;":
            _close_lists()
            if not in_blockquote:
                output.append("<blockquote>")
                in_blockquote = True
            content = re.sub(r"^&gt;\s?", "", stripped)
            output.append(f"<p>{content}</p>" if content else "<br>")
            continue
        if in_blockquote and stripped:
            # Non-blank non-quote line ends the blockquote
            output.append("</blockquote>")
            in_blockquote = False

        # Headings were already converted to tags — pass through
        if re.match(r"^<h[1-6]>", stripped):
            _close_lists()
            if in_blockquote:
                output.append("</blockquote>")
                in_blockquote = False
            output.append(stripped)
            continue

        # Unordered list
        if re.match(r"^[-*]\s+", stripped):
            if in_blockquote:
                output.append("</blockquote>")
                in_blockquote = False
            if not in_ul:
                if in_ol:
                    output.append("</ol>")
                    in_ol = False
                output.append("<ul>")
                in_ul = True
            item = re.sub(r"^[-*]\s+", "", stripped)
            output.append(f"<li>{item}</li>")
            continue

        # Ordered list
        ol_match = re.match(r"^\d+\.\s+(.+)", stripped)
        if ol_match:
            if in_blockquote:
                output.append("</blockquote>")
                in_blockquote = False
            if not in_ol:
                if in_ul:
                    output.append("</ul>")
                    in_ul = False
                output.append("<ol>")
                in_ol = True
            output.append(f"<li>{ol_match.group(1)}</li>")
            continue

        # Close open lists on any non-list line
        _close_lists()

        if not stripped:
            if in_blockquote:
                output.append("</blockquote>")
                in_blockquote = False
            output.append("<br>")
        else:
            output.append(f"<p>{stripped}</p>")

    _close_lists()
    if in_blockquote:
        output.append("</blockquote>")

    # Clean up consecutive <br> tags (max two blank lines → one)
    result = re.sub(r"(<br>\s*){3,}", "<br><br>", "\n".join(output))
    return result


def format_html_email(
    body: str,
    sender_name: str = "",
    org_name: str = "Armely",
    already_html: bool = False,
) -> str:
    """
    Wrap an email body in a professional HTML template.

    Args:
        body: Plain text (markdown supported) or raw HTML if already_html=True.
        sender_name: Display name for the email signature.
        org_name: Organisation name shown in the signature.
        already_html: Skip markdown conversion if body is already HTML.

    Returns:
        Complete HTML email document string.
    """
    body_html = body if already_html else _markdown_to_html(body)

    # Strip any leftover raw <br> tags that appear as visible text
    # (can happen if the model produces literal &lt;br&gt; in its content)
    body_html = body_html.replace("&lt;br&gt;", "").replace("&lt;br /&gt;", "")

    signature_html = ""
    if sender_name:
        signature_html = _SIGNATURE_TEMPLATE.format(
            sender_name=html.escape(sender_name),
            org_name=html.escape(org_name),
        )

    # Safe injection: body_html may contain Python format placeholders like {name}
    # which would cause str.format() to raise KeyError.
    # Temporarily replace {{ / }} (CSS braces in template) with control chars,
    # substitute our two placeholders, then restore the braces.
    _L, _R = "\x01", "\x02"
    tmpl = _EMAIL_TEMPLATE.replace("{{", _L).replace("}}", _R)
    result = tmpl.replace("{body_html}", body_html).replace("{signature_html}", signature_html)
    return result.replace(_L, "{").replace(_R, "}")


# ── Plain-text email formatting ────────────────────────────────────────────────

def _markdown_to_plaintext(text: str) -> str:
    """
    Convert markdown / rich text to clean plain text suitable for email.
    Strips all HTML tags, converts markdown syntax to readable text symbols.
    """
    # Remove any HTML tags that may have crept in
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    text = (
        text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
    )

    # Headings: # Title → TITLE / ===, ## Title → Title / ---
    def _plain_heading(m: re.Match) -> str:
        level = len(m.group(1))
        title = m.group(2).strip()
        if level == 1:
            bar = "=" * max(len(title), 4)
            return f"\n{title.upper()}\n{bar}"
        elif level == 2:
            bar = "-" * max(len(title), 4)
            return f"\n{title}\n{bar}"
        return f"\n  {title}"

    text = re.sub(r"^(#{1,3})\s+(.+)$", _plain_heading, text, flags=re.MULTILINE)

    # Bold / italic: strip markers, keep text
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*([^*\n]+?)\*", r"\1", text)
    text = re.sub(r"_([^_\n]+?)_", r"\1", text)

    # Inline code: `code` → code (drop backticks)
    text = re.sub(r"`([^`\n]+?)`", r"\1", text)

    # Links: [display](url) → display (url)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r"\1 (\2)", text)

    # Blockquotes: > text → | text
    text = re.sub(r"^[ \t]*>\s*(.+)$", r"  | \1", text, flags=re.MULTILINE)

    # Horizontal rules → unicode line
    text = re.sub(r"^[-*_]{3,}$", "─" * 52, text, flags=re.MULTILINE)

    # Unordered lists: - item or * item → • item
    text = re.sub(r"^[ \t]*[-*]\s+", "  • ", text, flags=re.MULTILINE)

    # Collapse more than 2 consecutive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def format_plain_text_email(
    body: str,
    sender_name: str = "",
    org_name: str = "Armely",
) -> str:
    """
    Format an email body as professional plain text.

    Compatible with every email client — no HTML tags, no rendering issues.
    Converts markdown syntax (headings, bold, lists, links) to readable text.
    Appends a professional sign-off and branding footer.
    """
    clean_body = _markdown_to_plaintext(body)

    parts: list[str] = [clean_body, ""]

    if sender_name:
        parts += ["", "Best regards,", sender_name, org_name]

    parts += [
        "",
        "─" * 52,
        "Sent via Mela AI  ·  armely.com",
    ]

    return "\n".join(parts)


# ── Message normalisation ──────────────────────────────────────────────────────

def normalize_message(raw: Dict[str, Any], include_body: bool = False) -> Dict[str, Any]:
    """
    Convert a raw Graph API message object into a clean, consistent dict
    suitable for returning to the LLM or the frontend.
    """
    sender = raw.get("from", {}).get("emailAddress", {})
    body_content = ""
    if include_body:
        body = raw.get("body", {})
        raw_content = body.get("content", "")
        if body.get("contentType", "").lower() == "html":
            # Strip HTML tags for LLM consumption
            body_content = re.sub(r"<[^>]+>", " ", raw_content)
            body_content = re.sub(r"\s+", " ", body_content).strip()
        else:
            body_content = raw_content

    to_list = [
        r["emailAddress"].get("address", "")
        for r in raw.get("toRecipients", [])
        if r.get("emailAddress")
    ]
    cc_list = [
        r["emailAddress"].get("address", "")
        for r in raw.get("ccRecipients", [])
        if r.get("emailAddress")
    ]

    result: Dict[str, Any] = {
        "id": raw.get("id", ""),
        "conversation_id": raw.get("conversationId", ""),
        "subject": raw.get("subject", "(no subject)"),
        "from_name": sender.get("name", ""),
        "from_address": sender.get("address", ""),
        "to": to_list,
        "cc": cc_list,
        "preview": (raw.get("bodyPreview") or "")[:300],
        "received_at": raw.get("receivedDateTime", ""),
        "is_read": raw.get("isRead", True),
        "is_important": raw.get("importance", "normal") == "high",
        "is_flagged": raw.get("flag", {}).get("flagStatus", "notFlagged") == "flagged",
        "has_attachments": raw.get("hasAttachments", False),
    }
    if include_body and body_content:
        result["body"] = body_content
    return result


def normalize_message_list(
    raw_messages: List[Dict[str, Any]],
    include_body: bool = False,
) -> List[Dict[str, Any]]:
    """Normalise a list of raw Graph message objects."""
    return [normalize_message(m, include_body=include_body) for m in raw_messages]
