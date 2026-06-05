"""
Mela AI - Document Endpoints
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from app.core.database import get_db
from app.core.mode import UserSession, get_user_session
from app.core.security import get_current_user, get_current_admin_user
from app.models import Document, DocumentChunk
from app.schemas.auth import UserInfo
from app.schemas.documents import (
    DocumentResponse, SearchRequest, SearchResponse, SearchResult,
    IndexingStatus, DocumentStatus,
)
from app.services.document_service import document_processor
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)
router = APIRouter()


async def process_document_background(
    document_id: str,
    file_data: bytes,
    file_type: str,
    filename: str,
    user_id: str,
):
    """Background task to process and index a document."""
    from app.core.database import async_session_maker

    async with async_session_maker() as db:
        try:
            # Get document
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            document = result.scalar_one_or_none()

            if not document:
                logger.error(f"Document {document_id} not found")
                return

            # Extract text
            text, metadata = document_processor.extract_text(file_data, file_type, filename)

            if not text:
                logger.warning(f"No text extracted from {filename}")
                document.is_indexed = False
                await db.commit()
                return

            # Index document
            chunk_ids = await rag_service.index_document(
                document_id=document_id,
                title=document.title,
                content=text,
                source=document.source,
                source_url=document.source_url,
                file_type=file_type,
                uploaded_by=user_id,
                metadata=metadata,
            )

            # Update document
            document.chunk_count = len(chunk_ids)
            document.is_indexed = True
            document.metadata = metadata

            # Create chunk records
            chunks = rag_service.chunk_text(text)
            for i, (chunk_id, chunk_text) in enumerate(zip(chunk_ids, chunks)):
                chunk = DocumentChunk(
                    id=chunk_id,
                    document_id=document_id,
                    chunk_index=i,
                    content=chunk_text,
                    token_count=len(chunk_text.split()),
                    search_index_id=chunk_id,
                )
                db.add(chunk)

            await db.commit()
            logger.info(f"Document {document_id} indexed with {len(chunk_ids)} chunks")

        except Exception as e:
            logger.error(f"Error processing document {document_id}: {e}")
            if document:
                document.is_indexed = False
                await db.commit()


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    add_to_knowledge_base: bool = Form(True),
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document for processing."""
    # Validate file type
    file_type = document_processor.detect_file_type(
        file.filename,
        file.content_type,
    )

    if file_type == "unknown":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {file.filename}",
        )

    # Read file
    file_data = await file.read()

    # M-4: per-user daily upload quota (no-op when DAILY_UPLOAD_QUOTA_MB=0)
    from app.services.upload_quota import check_and_consume_upload_quota
    from app.core.logging import log_security_event
    allowed, used_bytes, limit_bytes = await check_and_consume_upload_quota(
        current_user.id, len(file_data)
    )
    if not allowed:
        await log_security_event(
            db,
            user_id=current_user.id,
            action="upload_quota_exceeded",
            event_type="file_upload",
            resource_type="document",
            details={
                "filename": file.filename,
                "size": len(file_data),
                "used_bytes": used_bytes,
                "limit_bytes": limit_bytes,
            },
            success=False,
            error_message="daily_upload_quota_exceeded",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "daily_upload_quota_exceeded",
                "message": (
                    f"Daily upload limit of "
                    f"{limit_bytes // (1024 * 1024)} MB reached. "
                    "Try again after midnight UTC."
                ),
                "limit_mb": limit_bytes // (1024 * 1024),
                "used_mb": used_bytes // (1024 * 1024),
            },
        )

    # Security scan (mirrors chat upload path)
    from app.services.file_security import scan_file
    scan = scan_file(
        file_data,
        file.filename or "attachment",
        file.content_type or "application/octet-stream",
    )
    if scan.blocked:
        logger.warning(
            "[security] Blocked document upload user=%s file=%r: %s",
            current_user.id, file.filename, scan.warnings,
        )
        # Phase 2 (H-7): audit the rejection.
        await log_security_event(
            db,
            user_id=current_user.id,
            action="file_rejected",
            event_type="file_upload",
            resource_type="document",
            details={
                "filename": file.filename,
                "content_type": file.content_type,
                "size": len(file_data),
                "warnings": scan.warnings,
            },
            success=False,
            error_message=(scan.warnings[0] if scan.warnings else "file_rejected"),
        )
        await db.commit()
        # Release the quota we consumed before the security gate.
        from app.services.upload_quota import release_upload_quota
        await release_upload_quota(current_user.id, len(file_data))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "file_rejected",
                "message": scan.warnings[0] if scan.warnings else "File rejected by security scan",
                "warnings": scan.warnings,
            },
        )

    # Phase 6 (M-5): Antivirus scan BEFORE the blob is persisted.
    from app.services.av_scan_service import (
        scan_bytes as av_scan_bytes,
        should_fail_closed_on_unknown,
    )
    av = await av_scan_bytes(file_data, file.filename or "attachment")
    if av.is_malicious:
        logger.warning(
            "[av] Quarantined upload user=%s file=%r engine=%s sig=%s",
            current_user.id, file.filename, av.engine, av.signature,
        )
        await log_security_event(
            db,
            user_id=current_user.id,
            action="file_quarantined",
            event_type="file_upload",
            resource_type="document",
            details={
                "filename": file.filename,
                "content_type": file.content_type,
                "size": len(file_data),
                "av_engine": av.engine,
                "av_signature": av.signature,
            },
            success=False,
            error_message=f"malware_detected:{av.signature or 'unknown'}",
        )
        await db.commit()
        from app.services.upload_quota import release_upload_quota
        await release_upload_quota(current_user.id, len(file_data))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "file_quarantined",
                "message": "File rejected by antivirus scan.",
                "engine": av.engine,
                "signature": av.signature,
            },
        )
    if av.verdict in ("unknown", "error") and should_fail_closed_on_unknown():
        logger.warning(
            "[av] Rejecting upload (fail-closed) user=%s file=%r verdict=%s msg=%s",
            current_user.id, file.filename, av.verdict, av.message,
        )
        await log_security_event(
            db,
            user_id=current_user.id,
            action="file_rejected",
            event_type="file_upload",
            resource_type="document",
            details={
                "filename": file.filename,
                "size": len(file_data),
                "av_engine": av.engine,
                "av_verdict": av.verdict,
                "av_message": av.message,
            },
            success=False,
            error_message="av_scan_unavailable",
        )
        await db.commit()
        from app.services.upload_quota import release_upload_quota
        await release_upload_quota(current_user.id, len(file_data))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "av_scan_unavailable",
                "message": "Antivirus scanner unavailable; upload rejected.",
            },
        )

    # Upload to blob storage
    blob_url = await document_processor.upload_to_blob(
        file_data,
        file.filename,
        content_type=file.content_type,
    )

    # Create document record
    document = Document(
        id=str(uuid.uuid4()),
        title=title or file.filename,
        filename=file.filename,
        file_type=file_type,
        file_size=len(file_data),
        blob_url=blob_url,
        source="upload",
        content_hash=document_processor.get_content_hash(file_data),
        uploaded_by=current_user.id,
        is_indexed=False,
    )
    db.add(document)

    # Phase 2 (H-7): audit the successful upload before commit.
    await log_security_event(
        db,
        user_id=current_user.id,
        action="file_uploaded",
        event_type="file_upload",
        resource_type="document",
        resource_id=document.id,
        details={
            "filename": file.filename,
            "content_type": file.content_type,
            "size": len(file_data),
            "file_type": file_type,
            "add_to_knowledge_base": add_to_knowledge_base,
        },
        success=True,
    )

    await db.commit()

    # Process in background if adding to knowledge base
    if add_to_knowledge_base:
        background_tasks.add_task(
            process_document_background,
            document.id,
            file_data,
            file_type,
            file.filename,
            current_user.id,
        )

    return DocumentResponse.model_validate(document)


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    source: Optional[str] = None,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List documents owned by the current user (admins see all)."""
    query = select(Document).where(Document.is_active == True)

    # GDPR/SOC2 Sprint 2: hide soft-deleted documents when the flag is on.
    from app.core.soft_delete import filter_deleted
    query = filter_deleted(query, Document)

    # Fail-closed: non-admins can only see their own uploads.
    if "Admin" not in current_user.roles:
        query = query.where(Document.uploaded_by == current_user.id)

    if source:
        query = query.where(Document.source == source)

    query = query.order_by(Document.created_at.desc()).limit(limit).offset(offset)

    result = await db.execute(query)
    documents = result.scalars().all()

    return [DocumentResponse.model_validate(doc) for doc in documents]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document details (owner or admin only)."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.uploaded_by != current_user.id and "Admin" not in current_user.roles:
        # Return 404 instead of 403 to avoid leaking existence.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return DocumentResponse.model_validate(document)


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Check permission (owner or admin)
    if document.uploaded_by != current_user.id and "Admin" not in current_user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )

    # Delete from search index
    await rag_service.delete_document(document_id)

    # Soft delete
    document.is_active = False
    await db.commit()

    return {"message": "Document deleted"}


@router.post("/search", response_model=SearchResponse)
async def search_documents(
    request: SearchRequest,
    current_user: UserInfo = Depends(get_current_user),
    user_session: UserSession = Depends(get_user_session),
):
    """Search documents using semantic search with ACL-based permission filtering.

    In work mode: Uses enterprise query pipeline with proper ACL filtering.
    Users only see documents they have access to (based on SharePoint/OneDrive permissions).

    In personal mode: Only searches user's own uploaded files.
    """
    from app.core.config import settings

    if user_session.is_personal:
        # Personal mode: only search user's uploaded files (no enterprise data)
        filters = dict(request.filters or {})
        filters["source"] = "upload"
        filters["uploaded_by"] = str(current_user.id)
        results = await rag_service.search(
            query=request.query,
            top_k=request.top_k,
            filters=filters,
        )
        return SearchResponse(
            query=request.query,
            results=results,
            total_results=len(results),
        )

    # Work mode: Use enterprise query with ACL filtering
    if settings.AZURE_SEARCH_ENDPOINT and settings.AZURE_SEARCH_ADMIN_KEY:
        try:
            from app.services.search.query_pipeline import enterprise_query

            # Get workspace_id (tenant) for filtering
            workspace_id = current_user.tenant_id or settings.effective_tenant_id
            if not workspace_id:
                logger.warning("No tenant_id available for enterprise search")
                return SearchResponse(
                    query=request.query,
                    results=[],
                    total_results=0,
                )

            # Determine source types from filters
            source_types = None
            if request.filters and "source_type" in request.filters:
                st = request.filters["source_type"]
                source_types = [st] if isinstance(st, str) else st

            # Execute ACL-filtered enterprise search
            ent_results = await enterprise_query.search(
                query=request.query,
                workspace_id=workspace_id,
                context_type="org",
                user_id=current_user.id,  # Azure AD OID
                user_groups=current_user.groups,  # Azure AD group OIDs for ACL
                tenant_id=current_user.tenant_id or settings.effective_tenant_id,
                source_types=source_types,
                top_k=request.top_k,
                use_cache=True,
            )

            # Convert enterprise results to SearchResult schema
            results = [
                SearchResult(
                    document_id=r.chunk_id.rsplit("_", 1)[0] if "_" in r.chunk_id else r.chunk_id,
                    document_title=r.document_title,
                    chunk_id=r.chunk_id,
                    content=r.content,
                    score=r.score,
                    source_url=r.url,
                    source_type=r.source_type,
                    metadata=r.citation,
                )
                for r in ent_results
            ]

            return SearchResponse(
                query=request.query,
                results=results,
                total_results=len(results),
            )

        except Exception as e:
            logger.error("Enterprise search failed: %s", e, exc_info=True)
            # FAIL-CLOSED: do not silently downgrade to a non-ACL search path.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Enterprise search is temporarily unavailable. Please retry.",
            )

    # Enterprise search not configured: in work mode we MUST NOT return an
    # unfiltered RAG result set. Return an explicit empty response with a hint.
    logger.warning(
        "Work-mode search invoked without AZURE_SEARCH configured; returning empty results."
    )
    return SearchResponse(
        query=request.query,
        results=[],
        total_results=0,
    )


@router.get("/{document_id}/status", response_model=IndexingStatus)
async def get_document_status(
    document_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document indexing status (owner or admin only)."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.uploaded_by != current_user.id and "Admin" not in current_user.roles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.is_indexed:
        status_val = DocumentStatus.INDEXED
        progress = 1.0
        message = f"Indexed with {document.chunk_count} chunks"
    else:
        status_val = DocumentStatus.PROCESSING
        progress = 0.5
        message = "Processing document..."

    return IndexingStatus(
        document_id=document_id,
        status=status_val,
        progress=progress,
        message=message,
    )


@router.post("/reindex/{document_id}")
async def reindex_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserInfo = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-index a document (admin only)."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    # Download file and reprocess
    file_data = await document_processor.download_from_blob(document.blob_url)

    background_tasks.add_task(
        process_document_background,
        document.id,
        file_data,
        document.file_type,
        document.filename,
        current_user.id,
    )

    return {"message": "Re-indexing started"}
