"""
Mela AI - Generated File Download Endpoint

Allows authenticated users to re-download files they previously generated
via the code interpreter. Files are stored as base64 in GeneratedFileLog.
"""

import base64
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.models import GeneratedFileLog
from app.schemas.auth import UserInfo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{file_log_id}")
async def download_generated_file(
    file_log_id: str,
    current_user: UserInfo = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Download a previously generated file by its log ID.
    Only the user who generated the file can download it.
    """
    result = await db.execute(
        select(GeneratedFileLog).where(GeneratedFileLog.id == file_log_id)
    )
    log = result.scalar_one_or_none()

    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    # Ownership check — only the generating user may download
    if log.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    if not log.file_data:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="File data is no longer available. Re-run the request to regenerate.",
        )

    try:
        file_bytes = base64.b64decode(log.file_data)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File data is corrupted",
        )

    return Response(
        content=file_bytes,
        media_type=log.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{log.filename}"',
            "Content-Length": str(len(file_bytes)),
            "Cache-Control": "private, max-age=3600",
        },
    )
