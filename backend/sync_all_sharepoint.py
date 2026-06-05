"""
Run SharePoint full sync for all configured sites including the new ArmelyLLC site.
"""
import asyncio
import os
import sys
import logging

# Set the dotenv path
os.environ["DOTENV_PATH"] = "../env/.env.dev"

# Add app to path
sys.path.insert(0, ".")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Reduce noise from httpcore
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

async def main():
    from app.core.config import settings
    from app.services.connectors.sharepoint import SharePointConnector
    from app.services.ingestion_worker import ingestion_worker
    from app.services.search.index_manager import index_manager
    
    print("=" * 80)
    print("SHAREPOINT FULL SYNC - INCLUDING ARMELYLLC")
    print("=" * 80)
    print()
    print(f"Configured SharePoint sites:")
    site_list = settings.sharepoint_site_list  # Use the property method which parses the CSV
    for i, site in enumerate(site_list, 1):
        print(f"  {i}. {site}")
    print()
    
    workspace_id = settings.AZURE_TENANT_ID or "default-workspace"
    print(f"Workspace ID: {workspace_id}")
    print()
    
    total_docs = 0
    total_new = 0
    
    for site_url in site_list:  # Use the parsed list
        site_name = site_url.split("/")[-1] or site_url.split("/")[-2]
        print(f"\n{'=' * 60}")
        print(f"Processing: {site_name}")
        print(f"URL: {site_url}")
        print(f"{'=' * 60}")
        
        try:
            # SharePointConnector accepts site_urls (list), not site_url
            connector = SharePointConnector(
                workspace_id=workspace_id,
                context_type="org",
                site_urls=[site_url],  # Pass as list
            )
            
            docs = []
            async for doc in connector.sync(full=True):
                docs.append(doc)
                if len(docs) % 50 == 0:
                    print(f"  Collected {len(docs)} documents so far...")
            
            print(f"\nTotal documents from {site_name}: {len(docs)}")
            
            if docs:
                # Index documents
                print(f"Indexing {len(docs)} documents...")
                batch_size = 100
                for i in range(0, len(docs), batch_size):
                    batch = docs[i:i+batch_size]
                    try:
                        result = await index_manager.index_documents_async(
                            settings.AZURE_SEARCH_INDEX_NAME,
                            batch
                        )
                        print(f"  Batch {i//batch_size + 1}/{(len(docs) + batch_size - 1)//batch_size}: {len(batch)} docs indexed")
                    except Exception as e:
                        print(f"  Error indexing batch: {e}")
                
                total_new += len(docs)
                total_docs += len(docs)
                print(f"✅ Successfully indexed {len(docs)} documents from {site_name}")
            else:
                print(f"⚠️ No documents found in {site_name}")
                
        except Exception as e:
            print(f"❌ Error processing {site_name}: {e}")
            import traceback
            traceback.print_exc()
    
    print()
    print("=" * 80)
    print("SYNC COMPLETE")
    print("=" * 80)
    print(f"Total sites processed: {len(site_list)}")
    print(f"Total documents indexed: {total_docs}")
    print()
    
    # Check index stats
    try:
        stats = index_manager.get_index_stats(settings.AZURE_SEARCH_INDEX_NAME)
        print(f"Index document count: {stats.get('documentCount', 'unknown')}")
        print(f"Index storage size: {stats.get('storageSize', 'unknown')} bytes")
    except Exception as e:
        print(f"Could not get index stats: {e}")

if __name__ == "__main__":
    asyncio.run(main())
