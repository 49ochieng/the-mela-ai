"""
Direct SharePoint ingestion script - bypasses API auth.
Syncs SharePoint documents and ingests them into Azure AI Search.
"""
import asyncio
import logging
import os
import sys

# Set env path before importing app modules
os.environ.setdefault("DOTENV_PATH", "../env/.env.dev")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
# Reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("msal").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def run_sharepoint_ingestion():
    """Sync SharePoint and ingest all documents."""
    from app.services.connectors.sharepoint import SharePointConnector
    from app.services.search.ingestion import ingestion_pipeline
    from app.core.config import settings
    
    workspace_id = settings.effective_tenant_id or "default"
    
    logger.info("Starting SharePoint ingestion...")
    logger.info("SharePoint sites: %s", settings.sharepoint_site_list)
    logger.info("Target index: %s", settings.AZURE_SEARCH_INDEX_NAME)
    
    connector = SharePointConnector(
        workspace_id=workspace_id,
        context_type="org",
    )
    
    total_docs = 0
    total_chunks = 0
    errors = []
    
    async for doc in connector.sync(full=True):
        total_docs += 1
        try:
            chunks = await ingestion_pipeline.ingest_document(doc)
            total_chunks += chunks
            logger.info(
                "[%d] Ingested '%s' -> %d chunks | ACL users=%d, groups=%d",
                total_docs,
                doc.title[:50] if doc.title else "Untitled",
                chunks,
                len(doc.acl_users),
                len(doc.acl_groups),
            )
        except Exception as e:
            errors.append((doc.title, str(e)))
            logger.error("Failed to ingest '%s': %s", doc.title, e)
    
    logger.info("=" * 60)
    logger.info("SharePoint Ingestion Complete")
    logger.info("Documents processed: %d", total_docs)
    logger.info("Total chunks indexed: %d", total_chunks)
    logger.info("Errors: %d", len(errors))
    if errors:
        for title, err in errors[:5]:
            logger.error("  - %s: %s", title, err)
    
    return total_docs, total_chunks


if __name__ == "__main__":
    docs, chunks = asyncio.run(run_sharepoint_ingestion())
    print(f"\nDone: {docs} documents, {chunks} chunks indexed")
