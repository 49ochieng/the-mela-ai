"""
Mela AI - Services
"""

import logging

logger = logging.getLogger(__name__)

# Import classes and instances with safe error handling
from app.services.openai_service import openai_service, AzureOpenAIService
from app.services.rag_service import rag_service, RAGService
from app.services.chat_service import chat_service, ChatService
from app.services.graph_service import graph_service, GraphAPIService
from app.services.speech_service import speech_service, SpeechService
from app.services.document_service import document_processor, DocumentProcessor, get_document_processor
from app.services.translator_service import translator_service, TranslatorService
from app.services.dalle_service import dalle_service, ImageGenerationService as DalleService
from app.services.document_intelligence_service import (
    document_intelligence_service,
    DocumentIntelligenceService,
)

__all__ = [
    "openai_service",
    "AzureOpenAIService",
    "rag_service",
    "RAGService",
    "chat_service",
    "ChatService",
    "graph_service",
    "GraphAPIService",
    "speech_service",
    "SpeechService",
    "document_processor",
    "DocumentProcessor",
    "get_document_processor",
    "translator_service",
    "TranslatorService",
    "dalle_service",
    "DalleService",
    "document_intelligence_service",
    "DocumentIntelligenceService",
]
