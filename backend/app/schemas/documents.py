"""
Mela AI - Document Schemas
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class DocumentSource(str, Enum):
    UPLOAD = "upload"
    SHAREPOINT = "sharepoint"
    WEB = "web"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class DocumentUpload(BaseModel):
    """Document upload request."""
    title: Optional[str] = None
    add_to_knowledge_base: bool = True


class DocumentResponse(BaseModel):
    """Document response."""
    id: str
    title: str
    filename: str
    file_type: str
    file_size: int
    source: str
    source_url: Optional[str] = None
    chunk_count: int
    is_indexed: bool
    is_active: bool
    metadata: Optional[Dict[str, Any]] = None
    uploaded_by: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentChunkResponse(BaseModel):
    """Document chunk response."""
    id: str
    document_id: str
    chunk_index: int
    content: str
    token_count: int

    class Config:
        from_attributes = True


class SearchResult(BaseModel):
    """Search result from RAG."""
    document_id: str
    document_title: str
    chunk_id: str
    content: str
    score: float
    source_url: Optional[str] = None
    source_type: Optional[str] = None  # sharepoint, onedrive, upload, etc.
    metadata: Optional[Dict[str, Any]] = None


class SearchRequest(BaseModel):
    """Search request."""
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    filters: Optional[Dict[str, Any]] = None


class SearchResponse(BaseModel):
    """Search response."""
    query: str
    results: List[SearchResult]
    total_results: int


class IndexingStatus(BaseModel):
    """Document indexing status."""
    document_id: str
    status: DocumentStatus
    progress: float = 0.0
    message: Optional[str] = None
    error: Optional[str] = None


class SharePointSyncRequest(BaseModel):
    """SharePoint sync request."""
    site_id: Optional[str] = None
    drive_id: Optional[str] = None
    folder_path: Optional[str] = None
    file_types: List[str] = ["pdf", "docx", "pptx", "xlsx", "txt", "md"]


class SharePointSyncStatus(BaseModel):
    """SharePoint sync status."""
    is_running: bool
    last_sync: Optional[datetime] = None
    documents_synced: int = 0
    documents_failed: int = 0
    next_sync: Optional[datetime] = None
