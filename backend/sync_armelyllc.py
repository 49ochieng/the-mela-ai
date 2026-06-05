"""
Sync and verify ArmelyLLC SharePoint site specifically.
"""
import asyncio
import logging
import os

os.environ.setdefault("DOTENV_PATH", "../env/.env.dev")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def sync_armelyllc():
    """Sync ArmelyLLC site and verify indexing."""
    from app.services.connectors.sharepoint import SharePointConnector
    from app.services.search.ingestion import ingestion_pipeline
    from app.services.search.index_manager import index_manager
    from app.core.config import settings
    
    armelyllc_url = "https://armely.sharepoint.com/sites/ArmelyLLC"
    workspace_id = settings.effective_tenant_id or "default"
    
    print("=" * 80)
    print("ARMELYLLC SHAREPOINT SYNC & VERIFICATION")
    print("=" * 80)
    print(f"Site URL: {armelyllc_url}")
    print(f"Workspace: {workspace_id}")
    print(f"Index: {settings.AZURE_SEARCH_INDEX_NAME}")
    print()
    
    # Get initial index count
    try:
        stats_before = index_manager.get_index_stats(settings.AZURE_SEARCH_INDEX_NAME)
        count_before = stats_before.get("documentCount", 0)
        print(f"Index document count before sync: {count_before}")
    except Exception as e:
        print(f"Could not get index stats: {e}")
        count_before = 0
    
    print()
    print("Starting sync...")
    print("-" * 80)
    
    connector = SharePointConnector(
        workspace_id=workspace_id,
        context_type="org",
        site_urls=[armelyllc_url],
    )
    
    docs_found = []
    total_chunks = 0
    errors = []
    
    async for doc in connector.sync(full=True):
        docs_found.append(doc)
        print(f"  [{len(docs_found)}] {doc.title[:60]}")
        try:
            chunks = await ingestion_pipeline.ingest_document(doc)
            total_chunks += chunks
            if len(docs_found) % 10 == 0:
                print(f"      -> {chunks} chunks indexed (Total: {total_chunks} chunks)")
        except Exception as e:
            errors.append((doc.title, str(e)))
            logger.error("Failed to ingest '%s': %s", doc.title, e)
    
    print()
    print("=" * 80)
    print("SYNC COMPLETE")
    print("=" * 80)
    print(f"Documents found: {len(docs_found)}")
    print(f"Total chunks indexed: {total_chunks}")
    print(f"Errors: {len(errors)}")
    
    if errors:
        print("\nErrors encountered:")
        for title, err in errors[:5]:
            print(f"  - {title}: {err}")
    
    # Get final index count
    print()
    print("Verifying index...")
    try:
        stats_after = index_manager.get_index_stats(settings.AZURE_SEARCH_INDEX_NAME)
        count_after = stats_after.get("documentCount", 0)
        print(f"Index document count after sync: {count_after}")
        print(f"New chunks added: {count_after - count_before}")
    except Exception as e:
        print(f"Could not get index stats: {e}")
    
    # Sample a few documents from ArmelyLLC
    if docs_found:
        print()
        print("Sample documents from ArmelyLLC:")
        for i, doc in enumerate(docs_found[:5], 1):
            print(f"\n{i}. {doc.title}")
            print(f"   URL: {doc.url}")
            print(f"   Type: {doc.file_type}")
            print(f"   Size: {len(doc.content)} chars")
            print(f"   ACL Users: {len(doc.acl_users)}, Groups: {len(doc.acl_groups)}")
    
    return len(docs_found), total_chunks


if __name__ == "__main__":
    docs, chunks = asyncio.run(sync_armelyllc())
    print(f"\n{'=' * 80}")
    print(f"FINAL RESULT: {docs} documents, {chunks} chunks from ArmelyLLC")
    print(f"{'=' * 80}")
