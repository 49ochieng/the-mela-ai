"""
Mela AI - Connector Base Types

Defines the shared data contract (ConnectorDocument) that every connector
produces, and the abstract interface (ConnectorBase) that every connector
must implement.  The ingestion pipeline consumes ConnectorDocument objects
without caring which connector produced them.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical source-type labels (used for filtering in Azure AI Search)
# ---------------------------------------------------------------------------
SOURCE_TYPE_SHAREPOINT = "sharepoint"
SOURCE_TYPE_ONEDRIVE = "onedrive"
SOURCE_TYPE_EMAIL = "email"
SOURCE_TYPE_PLANNER = "planner"
SOURCE_TYPE_ORG_WEBSITE = "org_website"
SOURCE_TYPE_PUBLIC_WEB = "public_web"
SOURCE_TYPE_AGENT_MEMORY = "agent_memory"

VALID_SOURCE_TYPES = {
    SOURCE_TYPE_SHAREPOINT,
    SOURCE_TYPE_ONEDRIVE,
    SOURCE_TYPE_EMAIL,
    SOURCE_TYPE_PLANNER,
    SOURCE_TYPE_ORG_WEBSITE,
    SOURCE_TYPE_PUBLIC_WEB,
    SOURCE_TYPE_AGENT_MEMORY,
}


@dataclass
class ConnectorDocument:
    """
    Canonical representation of a document produced by any connector.

    Fields mirror the Azure AI Search schema defined in IndexManager so
    that the ingestion pipeline can build search documents directly from
    this dataclass without any additional mapping logic.
    """

    # --- Identity ---------------------------------------------------------
    # Globally unique document id (connector sets this; usually a stable hash
    # of source_type + source_id so re-ingestion is idempotent).
    id: str

    # The source system that produced this document.
    source_type: str  # one of VALID_SOURCE_TYPES

    # Opaque id within the source system (e.g. SharePoint item id, message id).
    source_id: str

    # --- Namespace isolation -----------------------------------------------
    # Maps to Conversation/Project workspace_id for profile isolation.
    workspace_id: str = ""

    # "org" for work profile, "personal" for personal profile.
    context_type: str = "org"

    # --- Content ----------------------------------------------------------
    title: str = ""
    content: str = ""

    # Human-readable URL to open the document in its native app.
    url: str = ""

    # File-system or SharePoint path (optional).
    path: str = ""

    # MIME type or extension, e.g. "pdf", "docx", "html".
    file_type: str = ""

    # --- Timestamps -------------------------------------------------------
    last_modified: Optional[datetime] = None
    created_at: Optional[datetime] = None

    # --- Access control ---------------------------------------------------
    # User object-ids allowed to see this document (ACL enforcement).
    acl_users: List[str] = field(default_factory=list)

    # Group object-ids allowed to see this document.
    acl_groups: List[str] = field(default_factory=list)

    # Microsoft Purview / AIP sensitivity label (e.g. "Confidential").
    sensitivity_label: str = ""

    # UTC timestamp of the last ACL refresh for this document.
    # Populated by the ACL refresh job; None means never refreshed.
    acl_last_refreshed: Optional[datetime] = None

    # --- Agent Memory metadata (optional; only set by agent_memory ingestion) ─
    # Mirrors AgentMemoryItem.scope: 'personal' | 'workspace' | 'tenant'.
    memory_scope: str = ""
    # Mirrors AgentMemoryItem.tag: 'knowledge' | 'template' | 'brand' | 'policy' | 'demo'.
    tag: str = ""
    # FK back to AgentMemoryItem.id so deletes/reindex/disables can target one item.
    agent_memory_item_id: str = ""

    # --- Pre-built citation -----------------------------------------------
    # JSON-serialisable dict that the frontend can render as a source card.
    # Set by the connector; the ingestion pipeline stores it verbatim.
    citation: Optional[Dict[str, Any]] = None

    # --- Extra metadata ---------------------------------------------------
    # Arbitrary key-value pairs the connector may attach; not indexed.
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_type not in VALID_SOURCE_TYPES:
            raise ValueError(
                f"Invalid source_type '{self.source_type}'. "
                f"Must be one of: {sorted(VALID_SOURCE_TYPES)}"
            )
        if not self.id:
            raise ValueError("ConnectorDocument.id must not be empty.")
        if not self.source_id:
            raise ValueError("ConnectorDocument.source_id must not be empty.")

        # Normalise timestamps to UTC-aware datetimes.
        if self.last_modified and self.last_modified.tzinfo is None:
            self.last_modified = self.last_modified.replace(tzinfo=timezone.utc)
        if self.created_at and self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=timezone.utc)

    # Convenience: build a default citation dict if none was provided.
    def default_citation(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
            "file_type": self.file_type,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
        }


class ConnectorBase(abc.ABC):
    """
    Abstract base class for all Mela AI data connectors.

    Subclasses must implement `sync()` which yields ConnectorDocument
    objects.  The ingestion pipeline calls `sync()` and pipes the results
    into IngestionPipeline.ingest_document().
    """

    # Short slug identifying the connector, e.g. "sharepoint".
    source_type: str = ""

    def __init__(self, workspace_id: str, context_type: str = "org") -> None:
        self.workspace_id = workspace_id
        self.context_type = context_type
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )

    @abc.abstractmethod
    async def sync(self, full: bool = False):
        """
        Yield ConnectorDocument objects from the source system.

        Args:
            full: If True run a full re-sync; otherwise run an incremental
                  delta sync using the connector's stored delta token.
        """
        ...  # pragma: no cover

    async def health_check(self) -> bool:
        """
        Return True if the connector can reach its source system.

        Override in subclasses to add a real health probe.
        """
        return True
