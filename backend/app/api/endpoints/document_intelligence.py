"""
Mela AI - Document Intelligence Endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

from app.core.security import get_current_user
from app.schemas.auth import UserInfo
from app.services.document_intelligence_service import (
    document_intelligence_service,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentModelEnum(str, Enum):
    """Supported document analysis models."""
    READ = "prebuilt-read"
    LAYOUT = "prebuilt-layout"
    DOCUMENT = "prebuilt-document"
    INVOICE = "prebuilt-invoice"
    RECEIPT = "prebuilt-receipt"
    ID_DOCUMENT = "prebuilt-idDocument"
    BUSINESS_CARD = "prebuilt-businessCard"
    TAX_W2 = "prebuilt-tax.us.w2"
    CONTRACT = "prebuilt-contract"


class AnalyzeUrlRequest(BaseModel):
    """Request model for analyzing document from URL."""
    url: str = Field(..., description="Public URL of the document")
    model: DocumentModelEnum = Field(
        default=DocumentModelEnum.DOCUMENT,
        description="Model to use for analysis",
    )
    pages: Optional[str] = Field(
        None,
        description="Page selection (e.g., '1-3,5')",
    )
    locale: Optional[str] = Field(
        None,
        description="Locale hint for OCR (e.g., 'en-US')",
    )


@router.post("/analyze")
async def analyze_document(
    file: UploadFile = File(..., description="Document file to analyze"),
    model: DocumentModelEnum = Query(
        default=DocumentModelEnum.DOCUMENT,
        description="Model to use for analysis",
    ),
    pages: Optional[str] = Query(
        None,
        description="Page selection (e.g., '1-3,5')",
    ),
    locale: Optional[str] = Query(
        None,
        description="Locale hint for OCR",
    ),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Analyze a document using Azure Document Intelligence.

    Supports PDF, JPEG, PNG, TIFF, and BMP formats.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        # Read file content
        content = await file.read()

        # Analyze document
        result = await document_intelligence_service.analyze_document(
            document_data=content,
            model_id=model.value,
            pages=pages,
            locale=locale,
        )

        return result.to_dict()

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Document analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document analysis failed: {str(e)}",
        )


@router.post("/analyze/url")
async def analyze_document_from_url(
    request: AnalyzeUrlRequest,
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Analyze a document from a public URL.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        result = await document_intelligence_service.analyze_document_from_url(
            url=request.url,
            model_id=request.model.value,
            pages=request.pages,
            locale=request.locale,
        )

        return result.to_dict()

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Document analysis from URL failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Document analysis failed: {str(e)}",
        )


@router.post("/extract-text")
async def extract_text(
    file: UploadFile = File(..., description="Document to extract text from"),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Extract text content from a document using OCR.

    Uses the Read model for optimal text extraction.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        content = await file.read()
        text = await document_intelligence_service.extract_text(content)

        return {
            "filename": file.filename,
            "text": text,
            "character_count": len(text),
        }

    except Exception as e:
        logger.error(f"Text extraction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Text extraction failed: {str(e)}",
        )


@router.post("/extract-tables")
async def extract_tables(
    file: UploadFile = File(..., description="Document to extract tables from"),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Extract tables from a document.

    Returns structured table data that can be converted to DataFrames.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        content = await file.read()
        tables = await document_intelligence_service.extract_tables(content)

        return {
            "filename": file.filename,
            "table_count": len(tables),
            "tables": [
                {
                    "row_count": t.row_count,
                    "column_count": t.column_count,
                    "data": t.to_dataframe_dict(),
                }
                for t in tables
            ],
        }

    except Exception as e:
        logger.error(f"Table extraction failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Table extraction failed: {str(e)}",
        )


@router.post("/analyze/invoice")
async def analyze_invoice(
    file: UploadFile = File(..., description="Invoice document to analyze"),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Extract structured data from an invoice.

    Returns vendor, customer, items, totals, and other invoice fields.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        content = await file.read()
        invoice_data = await document_intelligence_service.analyze_invoice(content)

        return {
            "filename": file.filename,
            "invoice": invoice_data,
        }

    except Exception as e:
        logger.error(f"Invoice analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invoice analysis failed: {str(e)}",
        )


@router.post("/analyze/receipt")
async def analyze_receipt(
    file: UploadFile = File(..., description="Receipt to analyze"),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Extract structured data from a receipt.

    Returns merchant, items, totals, and transaction details.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        content = await file.read()
        receipt_data = await document_intelligence_service.analyze_receipt(content)

        return {
            "filename": file.filename,
            "receipt": receipt_data,
        }

    except Exception as e:
        logger.error(f"Receipt analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Receipt analysis failed: {str(e)}",
        )


@router.post("/analyze/id")
async def analyze_id_document(
    file: UploadFile = File(..., description="ID document to analyze"),
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Extract data from ID documents (passports, driver's licenses, etc.).

    Returns name, document number, expiration date, and other ID fields.
    """
    try:
        if not document_intelligence_service.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Document Intelligence service is not configured",
            )

        content = await file.read()
        id_data = await document_intelligence_service.analyze_id_document(content)

        return {
            "filename": file.filename,
            "id_document": id_data,
        }

    except Exception as e:
        logger.error(f"ID document analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ID document analysis failed: {str(e)}",
        )


@router.get("/models")
async def get_supported_models(
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Get list of supported document analysis models.
    """
    return {
        "models": [
            {
                "id": "prebuilt-read",
                "name": "Read",
                "description": "OCR and text extraction from documents and images",
            },
            {
                "id": "prebuilt-layout",
                "name": "Layout",
                "description": "Extract text, tables, and selection marks",
            },
            {
                "id": "prebuilt-document",
                "name": "Document",
                "description": "Extract key-value pairs and entities",
            },
            {
                "id": "prebuilt-invoice",
                "name": "Invoice",
                "description": "Extract invoice data (vendor, items, totals)",
            },
            {
                "id": "prebuilt-receipt",
                "name": "Receipt",
                "description": "Extract receipt data (merchant, items, totals)",
            },
            {
                "id": "prebuilt-idDocument",
                "name": "ID Document",
                "description": "Extract data from passports and licenses",
            },
            {
                "id": "prebuilt-businessCard",
                "name": "Business Card",
                "description": "Extract contact information from business cards",
            },
            {
                "id": "prebuilt-contract",
                "name": "Contract",
                "description": "Analyze contract documents",
            },
        ],
    }


@router.get("/status")
async def get_service_status(
    current_user: UserInfo = Depends(get_current_user),
):
    """
    Check if the Document Intelligence service is available.
    """
    return {
        "available": document_intelligence_service.is_configured,
        "supported_formats": ["pdf", "jpeg", "png", "tiff", "bmp"],
    }
