"""Outlook (mail) Graph operations."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .client import GraphClient


def build_outlook_source_link(message: dict[str, Any]) -> str | None:
    return message.get("webLink")


async def get_messages_since(client: GraphClient, since: datetime) -> list[dict]:
    iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "$filter": f"receivedDateTime ge {iso}",
        "$orderby": "receivedDateTime desc",
        "$top": "50",
        "$select": (
            "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,"
            "receivedDateTime,hasAttachments,bodyPreview,body,webLink"
        ),
    }
    return await client.paged("/me/messages", params=params)


_DELTA_SELECT = (
    "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,"
    "receivedDateTime,hasAttachments,bodyPreview,body,webLink"
)


async def get_inbox_messages_delta(
    client: GraphClient, delta_link: str | None,
) -> tuple[list[dict], str | None]:
    """Outlook delta query for the Inbox folder. If ``delta_link`` is
    provided, fetch only changes since that bookmark; otherwise prime a
    fresh delta cycle. Returns ``(messages, new_delta_link)``."""
    if delta_link:
        # delta_link is a fully-qualified URL; client.get accepts that.
        return await client.paged_with_delta(delta_link, params=None)
    params = {
        "$select": _DELTA_SELECT,
        "$top": "50",
    }
    return await client.paged_with_delta(
        "/me/mailFolders/Inbox/messages/delta", params=params,
    )


async def get_message(client: GraphClient, message_id: str) -> dict:
    return await client.get(f"/me/messages/{message_id}")


async def get_message_attachments(client: GraphClient, message_id: str) -> list[dict]:
    data = await client.get(f"/me/messages/{message_id}/attachments")
    return data.get("value", [])


async def download_email_attachment(client: GraphClient, message_id: str, attachment_id: str) -> bytes:
    data = await client.get(f"/me/messages/{message_id}/attachments/{attachment_id}")
    content_b64 = data.get("contentBytes")
    if not content_b64:
        return b""
    import base64
    return base64.b64decode(content_b64)
