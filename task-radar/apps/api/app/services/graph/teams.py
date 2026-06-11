"""Microsoft Teams Graph operations — channel messages + 1:1/group chats."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .client import GraphClient

# Cap for auto-discovery and chat scanning to keep Graph cost bounded.
_CHAT_SCAN_MAX = 50


def build_teams_source_link(team_id: str, channel_id: str, message_id: str) -> str:
    return f"https://teams.microsoft.com/l/message/{channel_id}/{message_id}?tenantId=&groupId={team_id}"


def build_chat_source_link(chat_id: str, message_id: str) -> str:
    return f"https://teams.microsoft.com/l/message/{chat_id}/{message_id}"


async def list_joined_teams(client: GraphClient) -> list[dict]:
    return await client.paged("/me/joinedTeams")


async def list_channels(client: GraphClient, team_id: str) -> list[dict]:
    return await client.paged(f"/teams/{team_id}/channels")


# ── Chat (1:1 and group) ──────────────────────────────────────────────

async def list_chats(client: GraphClient) -> list[dict]:
    """Return up to _CHAT_SCAN_MAX most-recently-active chats for the user."""
    params = {
        "$select": "id,chatType,topic,lastUpdatedDateTime",
        "$orderby": "lastUpdatedDateTime desc",
        "$top": str(_CHAT_SCAN_MAX),
    }
    try:
        return await client.paged("/me/chats", params=params)
    except Exception:
        # Some tenants reject $orderby on /me/chats; retry with a minimal shape.
        fallback = {
            "$select": "id,chatType,topic,lastUpdatedDateTime",
            "$top": str(_CHAT_SCAN_MAX),
        }
        return await client.paged("/me/chats", params=fallback)


async def get_chat_members(client: GraphClient, chat_id: str) -> list[dict]:
    """Return display names of participants (used to label 1:1 chat context)."""
    try:
        return await client.paged(f"/me/chats/{chat_id}/members")
    except Exception:  # noqa: BLE001
        return []


async def get_chat_messages_since(
    client: GraphClient, chat_id: str, since: datetime,
) -> list[dict]:
    # Graph supports $filter on /me/chats/{id}/messages but $orderby is not
    # supported together with $filter on this endpoint. Use $top + client-side filter.
    params = {
        "$top": "50",
        "$select": (
            "id,createdDateTime,lastModifiedDateTime,from,body,"
            "mentions,attachments,messageType,importance,webUrl"
        ),
    }
    try:
        raw = await client.paged(f"/me/chats/{chat_id}/messages", params=params)
    except Exception:
        # Some Graph front-ends reject $select for this endpoint.
        raw = await client.paged(
            f"/me/chats/{chat_id}/messages",
            params={"$top": "50"},
        )
    cutoff = since.replace(tzinfo=None)
    result = []
    for msg in raw:
        ts_str = msg.get("lastModifiedDateTime") or msg.get("createdDateTime")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.rstrip("Z"))
                if ts < cutoff:
                    continue
            except ValueError:
                pass
        result.append(msg)
    return result


async def get_channel_messages_since(
    client: GraphClient, team_id: str, channel_id: str, since: datetime
) -> list[dict]:
    # Graph does not support $filter on this endpoint — fetch recent messages and
    # filter client-side by lastModifiedDateTime / createdDateTime.
    params = {"$top": "50"}
    raw = await client.paged(f"/teams/{team_id}/channels/{channel_id}/messages", params=params)
    cutoff = since.replace(tzinfo=None)
    result = []
    for msg in raw:
        ts_str = msg.get("lastModifiedDateTime") or msg.get("createdDateTime")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.rstrip("Z"))
                if ts < cutoff:
                    continue
            except ValueError:
                pass
        result.append(msg)
    return result


async def get_channel_message_replies(
    client: GraphClient, team_id: str, channel_id: str, message_id: str
) -> list[dict]:
    return await client.paged(
        f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies"
    )


def detect_mentions(
    message: dict[str, Any],
    user_entra_id: str,
    *,
    user_upn: str | None = None,
    user_email: str | None = None,
    user_display_name: str | None = None,
) -> bool:
    """Robust mention detection.

    Matches the structured ``mentions`` array against the signed-in user
    using (in order): Entra object ID, userPrincipalName, email,
    case-insensitive display-name fallback.
    """
    upn_lc = (user_upn or "").lower()
    email_lc = (user_email or "").lower()
    name_lc = (user_display_name or "").lower()
    for m in message.get("mentions") or []:
        mentioned = (m.get("mentioned") or {}).get("user") or {}
        if user_entra_id and mentioned.get("id") == user_entra_id:
            return True
        if upn_lc and (mentioned.get("userPrincipalName") or "").lower() == upn_lc:
            return True
        if email_lc and (mentioned.get("email") or "").lower() == email_lc:
            return True
        if name_lc and (mentioned.get("displayName") or "").lower() == name_lc:
            return True
        # Some payloads put the mentionText at the top of the mention object.
        mt = (m.get("mentionText") or "").lower()
        if name_lc and mt and name_lc in mt:
            return True
    return False


def resolve_teams_file_links(message: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for att in message.get("attachments") or []:
        if att.get("contentType") in ("reference", "messageReference"):
            out.append(
                {
                    "name": att.get("name") or "file",
                    "source_url": att.get("contentUrl"),
                    "content_type": att.get("contentType"),
                }
            )
    return out
