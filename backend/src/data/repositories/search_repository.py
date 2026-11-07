"""
GlobeLens AI — SearchRepository
===============================
Elasticsearch wrapper for event indexing, full-text search, and prefix autocomplete.
"""
from typing import Any, Dict, List

from src.core.config import settings
from src.core.elasticsearch import es_client


class SearchRepository:
    """
    Elasticsearch data access layer for the SearchService.
    Handles event document indexing, full-text search queries, and prefix autocomplete.
    """

    INDEX = settings.ELASTICSEARCH_INDEX_EVENTS

    def __init__(self) -> None:
        self._client = es_client

    async def index(self, event_id: str, document: Dict[str, Any]) -> None:
        """Index or update an Event document in Elasticsearch."""
        await self._client.index(
            index=self.INDEX,
            id=event_id,
            document=document,
        )

    async def search(self, query: str, size: int = 20) -> List[Dict[str, Any]]:
        """
        Execute a multi-match full-text search across title and summary.
        Returns a list of matching event documents.
        """
        response = await self._client.search(
            index=self.INDEX,
            body={
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^3", "summary^2"],
                        "fuzziness": "AUTO",
                    }
                },
                "size": size,
            },
        )
        return [hit["_source"] for hit in response["hits"]["hits"]]

    async def autocomplete(self, prefix: str, size: int = 10) -> List[Dict[str, Any]]:
        """
        Autocomplete query against title.autocomplete (edge_ngram field).
        Returns matching event documents for quick headline suggestions.
        """
        response = await self._client.search(
            index=self.INDEX,
            body={
                "query": {
                    "match": {
                        "title.autocomplete": {
                            "query": prefix,
                            "operator": "and"
                        }
                    }
                },
                "size": size,
            },
        )
        return [hit["_source"] for hit in response["hits"]["hits"]]

    async def close(self) -> None:
        """Close connection pool. (Handled globally in lifespan, but kept for compatibility)"""
        # Global client is managed by the application lifespan context
        pass
