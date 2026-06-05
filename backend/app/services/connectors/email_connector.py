"""
Mela AI - Outlook Email Connector (delegated token, user-scoped)
Indexes subject + sender + preview only.  Full body is never stored.
Privacy: ACL is set to the user's own ID.
"""

from __future__ import annotations

import hashlib
import logging
from typing import AsyncIterator, Dict, List, Optional

from app.services.connectors.base import ConnectorBase, ConnectorDocument, SOURCE_TYPE_EMAIL
from app.services.connectors.graph_client import GraphClient
from app.services.connectors.sharepoint import _parse_dt

logger = logging.getLogger(__name__)


def _doc_id(user_id: str, msg_id: str) -> str:
    return hashlib.sha256(f"email:{user_id}:{msg_id}".encode()).hexdigest()[:40]


class EmailConnector(ConnectorBase):
    source_type = SOURCE_TYPE_EMAIL

    def __init__(
        self,
        workspace_id: str,
        context_type: str = "org",
        delegated_token: str = "",
        user_id: str = "",
        folders: Optional[List[str]] = None,
    ) -> None:
        super().__init__(workspace_id, context_type)
        self._client = GraphClient(delegated_token=delegated_token or None)
        self._user_id = user_id
        self._folders = folders or ["inbox"]

    async def sync(self, full: bool = False) -> AsyncIterator[ConnectorDocument]:
        for folder_id in self._folders:
            try:
                messages = await self._client.list_messages(folder_id=folder_id, top=200)
            except Exception as e:
                self._logger.error("Failed to fetch folder %s: %s", folder_id, str(e))
                continue

            for msg in messages:
                doc = self._build_document(msg, folder_id)
                if doc:
                    yield doc

    def _build_document(self, msg: Dict, folder_id: str) -> Optional[ConnectorDocument]:
        msg_id = msg.get("id", "")
        subject = msg.get("subject") or "(no subject)"
        preview = msg.get("bodyPreview", "")
        sender_obj = msg.get("from", {}).get("emailAddress", {})
        sender = f"{sender_obj.get('name', '')} <{sender_obj.get('address', '')}>"
        received = msg.get("receivedDateTime")

        content = f"Subject: {subject}\nFrom: {sender}\nPreview: {preview}"

        return ConnectorDocument(
            id=_doc_id(self._user_id, msg_id),
            source_type=self.source_type,
            source_id=folder_id,
            workspace_id=self.workspace_id,
            context_type=self.context_type,
            title=subject,
            content=content,
            url="",
            file_type="email",
            last_modified=_parse_dt(received),
            acl_users=[self._user_id] if self._user_id else [],
            citation={
                "source": "Email",
                "subject": subject,
                "from": sender,
                "folder": folder_id,
                "date": received or "",
            },
        )

    async def health_check(self) -> bool:
        try:
            folders = await self._client.list_mail_folders()
            return isinstance(folders, list)
        except Exception as e:
            self._logger.warning("Email health check failed: %s", str(e))
            return False
