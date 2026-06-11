"""Convert raw Graph messages into a common normalized form for AI extraction."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...enums import SourceType
from ...utils.text import clean_message_body, content_hash, excerpt
from ..graph.outlook import build_outlook_source_link
from ..graph.teams import build_teams_source_link, build_chat_source_link, detect_mentions, resolve_teams_file_links


def normalize_email(msg: dict[str, Any]) -> dict[str, Any]:
    body = msg.get("body") or {}
    is_html = (body.get("contentType") or "html").lower() == "html"
    cleaned = clean_message_body(body.get("content") or msg.get("bodyPreview") or "", is_html=is_html)
    body_text = excerpt(cleaned)
    sender = (msg.get("from") or {}).get("emailAddress") or {}
    to = [(r.get("emailAddress") or {}).get("address") for r in (msg.get("toRecipients") or [])]
    cc = [(r.get("emailAddress") or {}).get("address") for r in (msg.get("ccRecipients") or [])]
    received_iso = msg.get("receivedDateTime")
    return {
        "source_type": SourceType.EMAIL.value,
        "graph_message_id": msg["id"],
        "internet_message_id": msg.get("internetMessageId"),
        "conversation_id": msg.get("conversationId"),
        "sender_name": sender.get("name"),
        "sender_email": sender.get("address"),
        "recipients_json": {"to": [a for a in to if a], "cc": [a for a in cc if a]},
        "subject_or_channel": msg.get("subject"),
        "body_excerpt": body_text,
        "body_hash": content_hash(sender.get("address") or "", msg.get("subject") or "", body_text),
        "source_link": build_outlook_source_link(msg),
        "received_at": _parse_iso(received_iso),
        "has_attachments": bool(msg.get("hasAttachments")),
        "raw_metadata_json": {"webLink": msg.get("webLink")},
        "ai_payload": {
            "source": "email",
            "from_name": sender.get("name"),
            "from_email": sender.get("address"),
            "to": [a for a in to if a],
            "cc": [a for a in cc if a],
            "subject_or_channel": msg.get("subject"),
            "received_at": received_iso,
            "body_text": body_text,
            "mentions": [],
            "thread_context": "",
            "attachments": [],
        },
    }


def normalize_teams(
    msg: dict[str, Any], *, team_id: str, channel_id: str, channel_name: str | None,
    user_entra_id: str, thread_context: str = "",
    team_name: str | None = None,
    user_upn: str | None = None,
    user_email: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    body_html = (msg.get("body") or {}).get("content") or ""
    cleaned = clean_message_body(body_html, is_html=True)
    body_text = excerpt(cleaned)
    sender = ((msg.get("from") or {}).get("user")) or {}
    is_mention = detect_mentions(
        msg, user_entra_id,
        user_upn=user_upn, user_email=user_email,
        user_display_name=user_display_name,
    )
    files = resolve_teams_file_links(msg)
    received_iso = msg.get("createdDateTime")
    gid = msg["id"]
    return {
        "source_type": SourceType.TEAMS.value,
        "graph_message_id": f"{team_id}:{channel_id}:{gid}",
        "internet_message_id": None,
        "conversation_id": gid,
        "reply_to_id": msg.get("replyToId"),
        "sender_name": sender.get("displayName"),
        "sender_email": None,
        "recipients_json": {"channel": channel_name, "team": team_name},
        "subject_or_channel": channel_name,
        "body_excerpt": body_text,
        "body_hash": content_hash(sender.get("displayName") or "", channel_name or "", body_text),
        "source_link": msg.get("webUrl") or build_teams_source_link(team_id, channel_id, gid),
        "received_at": _parse_iso(received_iso),
        "has_attachments": bool(files),
        "raw_metadata_json": {
            "team_id": team_id, "team_name": team_name,
            "channel_id": channel_id, "channel_name": channel_name,
            "message_id": gid, "reply_to_id": msg.get("replyToId"),
            "is_mention": is_mention,
            "web_url": msg.get("webUrl"),
            "importance": msg.get("importance"),
            "message_type": msg.get("messageType"),
        },
        "ai_payload": {
            "source": "teams",
            "team_name": team_name,
            "channel_name": channel_name,
            "from_name": sender.get("displayName"),
            "from_email": None,
            "to": [],
            "cc": [],
            "subject_or_channel": f"#{channel_name}" if channel_name else None,
            "received_at": received_iso,
            "body_text": body_text,
            "mentions": ["@user"] if is_mention else [],
            "is_mentioned": is_mention,
            "thread_context": thread_context,
            "attachments": files,
        },
        "is_mention": is_mention,
        "files": files,
    }


def normalize_teams_chat(
    msg: dict[str, Any], *,
    chat_id: str,
    chat_type: str,
    chat_label: str,
    user_entra_id: str,
    user_upn: str | None = None,
    user_email: str | None = None,
    user_display_name: str | None = None,
) -> dict[str, Any]:
    """Normalize a message from a 1:1 or group chat (/me/chats/{id}/messages)."""
    body_html = (msg.get("body") or {}).get("content") or ""
    cleaned = clean_message_body(body_html, is_html=True)
    body_text = excerpt(cleaned)
    sender = ((msg.get("from") or {}).get("user")) or {}
    is_mention = detect_mentions(
        msg, user_entra_id,
        user_upn=user_upn, user_email=user_email,
        user_display_name=user_display_name,
    )
    # For 1:1 chats: sender IS the other party, so every message is implicitly
    # "to" the user — treat as mention for noise-filtering purposes.
    if chat_type == "oneOnOne":
        is_mention = True
    files = resolve_teams_file_links(msg)
    received_iso = msg.get("createdDateTime")
    gid = msg["id"]
    return {
        "source_type": SourceType.TEAMS.value,
        "graph_message_id": f"chat:{chat_id}:{gid}",
        "internet_message_id": None,
        "conversation_id": chat_id,
        "reply_to_id": msg.get("replyToId"),
        "sender_name": sender.get("displayName"),
        "sender_email": None,
        "recipients_json": {"chat": chat_label, "chat_type": chat_type},
        "subject_or_channel": chat_label,
        "body_excerpt": body_text,
        "body_hash": content_hash(sender.get("displayName") or "", chat_label, body_text),
        "source_link": msg.get("webUrl") or build_chat_source_link(chat_id, gid),
        "received_at": _parse_iso(received_iso),
        "has_attachments": bool(files),
        "raw_metadata_json": {
            "chat_id": chat_id,
            "chat_type": chat_type,
            "chat_label": chat_label,
            "message_id": gid,
            "reply_to_id": msg.get("replyToId"),
            "is_mention": is_mention,
            "web_url": msg.get("webUrl"),
            "importance": msg.get("importance"),
            "message_type": msg.get("messageType"),
        },
        "ai_payload": {
            "source": "teams_chat",
            "chat_type": chat_type,
            "chat_label": chat_label,
            "from_name": sender.get("displayName"),
            "from_email": None,
            "to": [],
            "cc": [],
            "subject_or_channel": chat_label,
            "received_at": received_iso,
            "body_text": body_text,
            "mentions": ["@user"] if is_mention else [],
            "is_mentioned": is_mention,
            "thread_context": "",
            "attachments": files,
        },
        "is_mention": is_mention,
        "files": files,
    }



def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None



