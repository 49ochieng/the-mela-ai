"""
Mela AI - Azure Document Intelligence Service
Provides document analysis capabilities using Azure AI Document Intelligence.
"""

import logging
from typing import Dict, Any, Optional, List
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, AnalyzeResult
from azure.core.credentials import AzureKeyCredential
from enum import Enum

from app.core.config import settings

logger = logging.getLogger(__name__)


class DocumentModel(str, Enum):
    """Supported document models."""
    PREBUILT_READ = "prebuilt-read"  # OCR, text extraction
    PREBUILT_LAYOUT = "prebuilt-layout"  # Tables, selection marks
    PREBUILT_DOCUMENT = "prebuilt-document"  # Key-value pairs
    PREBUILT_INVOICE = "prebuilt-invoice"  # Invoice fields
    PREBUILT_RECEIPT = "prebuilt-receipt"  # Receipt data
    PREBUILT_ID_DOCUMENT = "prebuilt-idDocument"  # ID cards, passports
    PREBUILT_BUSINESS_CARD = "prebuilt-businessCard"  # Business cards
    PREBUILT_TAX_US_W2 = "prebuilt-tax.us.w2"  # W-2 tax forms
    PREBUILT_CONTRACT = "prebuilt-contract"  # Contract analysis


class DocumentAnalysisResult:
    """Result from document analysis."""
    def __init__(
        self,
        content: str,
        pages: List[Dict[str, Any]],
        tables: List[Dict[str, Any]],
        key_value_pairs: List[Dict[str, Any]],
        documents: List[Dict[str, Any]],
        styles: List[Dict[str, Any]],
        model_id: str,
        api_version: str,
    ):
        self.content = content
        self.pages = pages
        self.tables = tables
        self.key_value_pairs = key_value_pairs
        self.documents = documents
        self.styles = styles
        self.model_id = model_id
        self.api_version = api_version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "pages": self.pages,
            "tables": self.tables,
            "key_value_pairs": self.key_value_pairs,
            "documents": self.documents,
            "styles": self.styles,
            "model_id": self.model_id,
            "api_version": self.api_version,
        }

    @property
    def text(self) -> str:
        """Get extracted text content."""
        return self.content

    @property
    def page_count(self) -> int:
        """Get number of pages analyzed."""
        return len(self.pages)


class TableResult:
    """Extracted table from document."""
    def __init__(
        self,
        row_count: int,
        column_count: int,
        cells: List[Dict[str, Any]],
        bounding_regions: List[Dict[str, Any]],
    ):
        self.row_count = row_count
        self.column_count = column_count
        self.cells = cells
        self.bounding_regions = bounding_regions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_count": self.row_count,
            "column_count": self.column_count,
            "cells": self.cells,
            "bounding_regions": self.bounding_regions,
        }

    def to_dataframe_dict(self) -> List[List[str]]:
        """Convert to 2D list for DataFrame creation."""
        result = [[""] * self.column_count for _ in range(self.row_count)]
        for cell in self.cells:
            row_idx = cell.get("rowIndex", 0)
            col_idx = cell.get("columnIndex", 0)
            content = cell.get("content", "")
            if row_idx < self.row_count and col_idx < self.column_count:
                result[row_idx][col_idx] = content
        return result


class DocumentIntelligenceService:
    """Service for Azure Document Intelligence operations."""

    def __init__(self):
        self.endpoint = settings.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
        self.api_key = settings.AZURE_DOCUMENT_INTELLIGENCE_KEY

        if self.is_configured:
            self.client = DocumentIntelligenceClient(
                endpoint=self.endpoint,
                credential=AzureKeyCredential(self.api_key),
            )
        else:
            self.client = None

    @property
    def is_configured(self) -> bool:
        """Check if the Document Intelligence service is properly configured."""
        return bool(self.endpoint and self.api_key)

    async def analyze_document(
        self,
        document_data: bytes,
        model_id: str = DocumentModel.PREBUILT_DOCUMENT,
        pages: Optional[str] = None,
        locale: Optional[str] = None,
    ) -> DocumentAnalysisResult:
        """
        Analyze a document using specified model.

        Args:
            document_data: Document bytes (PDF, JPEG, PNG, TIFF, BMP)
            model_id: Model to use for analysis
            pages: Page selection (e.g., "1-3,5")
            locale: Locale hint for OCR (e.g., "en-US")

        Returns:
            DocumentAnalysisResult with extracted information
        """
        if not self.is_configured:
            raise ValueError("Document Intelligence service is not configured")

        try:
            # Start the analysis
            poller = await self.client.begin_analyze_document(
                model_id=model_id.value if isinstance(model_id, DocumentModel) else model_id,
                analyze_request=document_data,
                content_type="application/octet-stream",
                pages=pages,
                locale=locale,
            )

            # Wait for completion
            result: AnalyzeResult = await poller.result()

            # Extract and format results
            return self._format_result(result, model_id)

        except Exception as e:
            logger.error(f"Document analysis error: {e}")
            raise

    async def analyze_document_from_url(
        self,
        url: str,
        model_id: str = DocumentModel.PREBUILT_DOCUMENT,
        pages: Optional[str] = None,
        locale: Optional[str] = None,
    ) -> DocumentAnalysisResult:
        """
        Analyze a document from URL.

        Args:
            url: Public URL of the document
            model_id: Model to use for analysis
            pages: Page selection
            locale: Locale hint for OCR

        Returns:
            DocumentAnalysisResult with extracted information
        """
        if not self.is_configured:
            raise ValueError("Document Intelligence service is not configured")

        try:
            poller = await self.client.begin_analyze_document(
                model_id=model_id.value if isinstance(model_id, DocumentModel) else model_id,
                analyze_request=AnalyzeDocumentRequest(url_source=url),
                pages=pages,
                locale=locale,
            )

            result: AnalyzeResult = await poller.result()
            return self._format_result(result, model_id)

        except Exception as e:
            logger.error(f"Document analysis from URL error: {e}")
            raise

    def _format_result(self, result: AnalyzeResult, model_id: str) -> DocumentAnalysisResult:
        """Format the analysis result into a structured response."""
        # Extract pages
        pages = []
        if result.pages:
            for page in result.pages:
                pages.append({
                    "page_number": page.page_number,
                    "width": page.width,
                    "height": page.height,
                    "unit": page.unit,
                    "angle": page.angle,
                    "lines_count": len(page.lines) if page.lines else 0,
                    "words_count": len(page.words) if page.words else 0,
                })

        # Extract tables
        tables = []
        if result.tables:
            for table in result.tables:
                cells = []
                if table.cells:
                    for cell in table.cells:
                        cells.append({
                            "rowIndex": cell.row_index,
                            "columnIndex": cell.column_index,
                            "rowSpan": cell.row_span or 1,
                            "columnSpan": cell.column_span or 1,
                            "content": cell.content,
                            "kind": cell.kind,
                        })

                tables.append({
                    "row_count": table.row_count,
                    "column_count": table.column_count,
                    "cells": cells,
                })

        # Extract key-value pairs
        key_value_pairs = []
        if result.key_value_pairs:
            for kv in result.key_value_pairs:
                key_content = kv.key.content if kv.key else ""
                value_content = kv.value.content if kv.value else ""
                key_value_pairs.append({
                    "key": key_content,
                    "value": value_content,
                    "confidence": kv.confidence,
                })

        # Extract documents (for invoice, receipt, etc.)
        documents = []
        if result.documents:
            for doc in result.documents:
                fields = {}
                if doc.fields:
                    for field_name, field_value in doc.fields.items():
                        fields[field_name] = {
                            "value": self._get_field_value(field_value),
                            "confidence": field_value.confidence if field_value else None,
                        }

                documents.append({
                    "doc_type": doc.doc_type,
                    "confidence": doc.confidence,
                    "fields": fields,
                })

        # Extract styles
        styles = []
        if result.styles:
            for style in result.styles:
                styles.append({
                    "is_handwritten": style.is_handwritten,
                    "confidence": style.confidence,
                })

        return DocumentAnalysisResult(
            content=result.content or "",
            pages=pages,
            tables=tables,
            key_value_pairs=key_value_pairs,
            documents=documents,
            styles=styles,
            model_id=model_id.value if isinstance(model_id, DocumentModel) else model_id,
            api_version=result.api_version or "",
        )

    def _get_field_value(self, field) -> Any:
        """Extract value from document field."""
        if field is None:
            return None

        value_type = field.type

        if value_type == "string":
            return field.value_string
        elif value_type == "number":
            return field.value_number
        elif value_type == "date":
            return str(field.value_date) if field.value_date else None
        elif value_type == "time":
            return str(field.value_time) if field.value_time else None
        elif value_type == "phoneNumber":
            return field.value_phone_number
        elif value_type == "currency":
            if field.value_currency:
                return {
                    "amount": field.value_currency.amount,
                    "symbol": field.value_currency.symbol,
                    "code": field.value_currency.code,
                }
            return None
        elif value_type == "address":
            if field.value_address:
                return {
                    "street": field.value_address.street_address,
                    "city": field.value_address.city,
                    "state": field.value_address.state,
                    "postal_code": field.value_address.postal_code,
                    "country": field.value_address.country_region,
                }
            return None
        elif value_type == "array":
            if field.value_array:
                return [self._get_field_value(item) for item in field.value_array]
            return []
        elif value_type == "object":
            if field.value_object:
                return {k: self._get_field_value(v) for k, v in field.value_object.items()}
            return {}
        else:
            return field.content

    async def extract_text(self, document_data: bytes) -> str:
        """
        Extract text from a document using the read model.

        Args:
            document_data: Document bytes

        Returns:
            Extracted text content
        """
        result = await self.analyze_document(
            document_data=document_data,
            model_id=DocumentModel.PREBUILT_READ,
        )
        return result.content

    async def extract_tables(self, document_data: bytes) -> List[TableResult]:
        """
        Extract tables from a document.

        Args:
            document_data: Document bytes

        Returns:
            List of extracted tables
        """
        result = await self.analyze_document(
            document_data=document_data,
            model_id=DocumentModel.PREBUILT_LAYOUT,
        )

        tables = []
        for table_data in result.tables:
            tables.append(TableResult(
                row_count=table_data.get("row_count", 0),
                column_count=table_data.get("column_count", 0),
                cells=table_data.get("cells", []),
                bounding_regions=table_data.get("bounding_regions", []),
            ))

        return tables

    async def analyze_invoice(self, document_data: bytes) -> Dict[str, Any]:
        """
        Extract invoice data from a document.

        Args:
            document_data: Invoice document bytes

        Returns:
            Extracted invoice fields
        """
        result = await self.analyze_document(
            document_data=document_data,
            model_id=DocumentModel.PREBUILT_INVOICE,
        )

        if result.documents and len(result.documents) > 0:
            return result.documents[0].get("fields", {})

        return {}

    async def analyze_receipt(self, document_data: bytes) -> Dict[str, Any]:
        """
        Extract receipt data from a document.

        Args:
            document_data: Receipt document bytes

        Returns:
            Extracted receipt fields
        """
        result = await self.analyze_document(
            document_data=document_data,
            model_id=DocumentModel.PREBUILT_RECEIPT,
        )

        if result.documents and len(result.documents) > 0:
            return result.documents[0].get("fields", {})

        return {}

    async def analyze_id_document(self, document_data: bytes) -> Dict[str, Any]:
        """
        Extract ID document data (passport, driver's license, etc.).

        Args:
            document_data: ID document bytes

        Returns:
            Extracted ID document fields
        """
        result = await self.analyze_document(
            document_data=document_data,
            model_id=DocumentModel.PREBUILT_ID_DOCUMENT,
        )

        if result.documents and len(result.documents) > 0:
            return result.documents[0].get("fields", {})

        return {}

    async def close(self):
        """Close the client connection."""
        if self.client:
            await self.client.close()


# Singleton instance - initialized lazily to avoid import failures
try:
    document_intelligence_service = DocumentIntelligenceService()
except Exception as e:
    logger.warning(f"Failed to initialize DocumentIntelligenceService: {e}")
    document_intelligence_service = None
