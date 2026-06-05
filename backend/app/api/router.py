"""
Mela AI - API Router
"""

from fastapi import APIRouter

from app.api.endpoints import (
    auth,
    chat,
    documents,
    admin,
    files,
    speech,
    translation,
    images,
    document_intelligence,
    user_settings,
    connectors,
    projects,
    collaboration,
    model_settings,
    workflows,
    graph,
    notifications,
    budgets,
    memory,
    agent_memory,
    orchestration,
    orchestration_ingest,
    embed,
    user_data,
)

api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(files.router, prefix="/files", tags=["Files"])
api_router.include_router(speech.router, prefix="/speech", tags=["Speech"])
api_router.include_router(translation.router, prefix="/translation", tags=["Translation"])
api_router.include_router(images.router, prefix="/images", tags=["Image Generation"])
api_router.include_router(document_intelligence.router, prefix="/document-intelligence", tags=["Document Intelligence"])
api_router.include_router(user_settings.router, prefix="/user", tags=["User Settings"])
api_router.include_router(connectors.router, prefix="/connectors", tags=["Connectors"])
api_router.include_router(projects.router, prefix="/projects", tags=["Projects"])
api_router.include_router(collaboration.router, prefix="", tags=["Collaboration"])
api_router.include_router(model_settings.router, prefix="/settings", tags=["Settings"])
api_router.include_router(workflows.router, prefix="/workflows", tags=["Workflows"])
api_router.include_router(graph.router, prefix="/graph", tags=["Graph"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(budgets.router, prefix="/budgets", tags=["Budgets"])
api_router.include_router(memory.router, prefix="/memories", tags=["Memory"])
api_router.include_router(agent_memory.router, prefix="/agent-memory", tags=["Agent Memory"])
api_router.include_router(orchestration.router, prefix="/orchestration", tags=["Orchestration"])
# Worker callbacks. Auth is via X-Worker-Id + X-Worker-Api-Key, NOT user JWT.
# RateLimitMiddleware.is_silent_path() exempts /api/v1/ingest/ from human limits.
api_router.include_router(orchestration_ingest.router, prefix="", tags=["Orchestration Ingest"])
api_router.include_router(embed.router, prefix="/embed", tags=["Embed"])
# GDPR Sprint 2: DSAR export + RTBE erasure (404 when ENABLE_GDPR_ENDPOINTS=false)
api_router.include_router(user_data.router, prefix="/user-data", tags=["GDPR"])
