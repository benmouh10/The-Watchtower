"""
GlobeLens AI — SearchService
===========================
Orchestrates index mappings creation, individual document indexing, and bulk synchronizations.
"""
import uuid
import structlog
from typing import Dict, Any, Tuple
from sqlalchemy import select
from elasticsearch.helpers import async_bulk

from src.core.config import settings
from src.core.database import AsyncSessionFactory
from src.data.entities.models import Event
from src.data.repositories.search_repository import SearchRepository

logger = structlog.get_logger()


class SearchService:
    """
    Business logic layer for Elasticsearch integrations.
    Handles schema management, database sync tasks, and indexing triggers.
    """

    def __init__(self) -> None:
        self.search_repository = SearchRepository()
        self.index_name = settings.ELASTICSEARCH_INDEX_EVENTS

    async def create_events_index(self) -> None:
        """
        Creates the 'globelens_events' index with strict analyzer/mappings settings if not exists.
        """
        client = self.search_repository._client
        logger.info("Checking if Elasticsearch index exists", index=self.index_name)
        
        try:
            exists = await client.indices.exists(index=self.index_name)
            if exists:
                logger.info("Elasticsearch index already exists", index=self.index_name)
                return
            
            # Setup index settings with edge_ngram autocomplete and mappings
            index_body = {
                "settings": {
                    "analysis": {
                        "filter": {
                            "edge_ngram_filter": {
                                "type": "edge_ngram",
                                "min_gram": 1,
                                "max_gram": 20
                            }
                        },
                        "analyzer": {
                            "autocomplete_analyzer": {
                                "type": "custom",
                                "tokenizer": "standard",
                                "filter": [
                                    "lowercase",
                                    "edge_ngram_filter"
                                ]
                            }
                        }
                    }
                },
                "mappings": {
                    "properties": {
                        "id": {"type": "keyword"},
                        "title": {
                            "type": "text",
                            "analyzer": "standard",
                            "fields": {
                                "autocomplete": {
                                    "type": "text",
                                    "analyzer": "autocomplete_analyzer",
                                    "search_analyzer": "standard"
                                }
                            }
                        },
                        "summary": {
                            "type": "text",
                            "analyzer": "standard"
                        },
                        "topic": {"type": "keyword"},
                        "location_country": {"type": "keyword"},
                        "latitude": {"type": "float"},
                        "longitude": {"type": "float"},
                        "importance_score": {"type": "float"}
                    }
                }
            }
            
            logger.info("Creating Elasticsearch index with custom mappings", index=self.index_name)
            await client.indices.create(index=self.index_name, body=index_body)
            logger.info("Successfully created Elasticsearch index", index=self.index_name)
        except Exception as exc:
            logger.error("Failed to check or create Elasticsearch index", index=self.index_name, error=str(exc))
            raise exc

    async def index_processed_event(self, event_id: uuid.UUID, event_data: dict) -> None:
        """
        Formats and pushes a single updated event document into Elasticsearch.
        """
        logger.info("Indexing single processed event", event_id=str(event_id))
        
        # Support mapping from 'country' in DB model to 'location_country' in ES mapping
        location_country = event_data.get("location_country") or event_data.get("country")
        
        doc = {
            "id": str(event_id),
            "title": event_data.get("title"),
            "summary": event_data.get("summary"),
            "topic": event_data.get("topic"),
            "location_country": location_country,
            "latitude": event_data.get("latitude"),
            "longitude": event_data.get("longitude"),
            "importance_score": float(event_data.get("importance_score", 0.0))
        }
        
        try:
            await self.search_repository.index(str(event_id), doc)
            logger.info("Successfully indexed event in Elasticsearch", event_id=str(event_id))
        except Exception as exc:
            logger.error("Failed to index event in Elasticsearch", event_id=str(event_id), error=str(exc))
            raise exc

    async def sync_all_processed_events(self) -> Tuple[int, int]:
        """
        Queries the database for all events in PROCESSED status and bulk-indexes
        them into Elasticsearch. Returns a tuple (indexed_count, failed_count).
        """
        logger.info("Starting bulk database to Elasticsearch synchronization")
        indexed_count = 0
        failed_count = 0
        
        async with AsyncSessionFactory() as session:
            try:
                result = await session.execute(
                    select(Event).where(Event.status == "PROCESSED")
                )
                events = result.scalars().all()
                logger.info("Fetched processed events from database", count=len(events))
                
                if not events:
                    logger.info("No processed events found to sync")
                    return 0, 0
                
                actions = []
                for event in events:
                    actions.append({
                        "_index": self.index_name,
                        "_id": str(event.id),
                        "_source": {
                            "id": str(event.id),
                            "title": event.title,
                            "summary": event.summary,
                            "topic": event.topic,
                            "location_country": event.country,
                            "latitude": event.latitude,
                            "longitude": event.longitude,
                            "importance_score": float(event.importance_score or 0.0)
                        }
                    })
                
                # Perform bulk index operation using the helpers
                client = self.search_repository._client
                success, errors = await async_bulk(client, actions, raise_on_error=False)
                
                indexed_count = success
                failed_count = len(errors) if isinstance(errors, list) else int(errors)
                logger.info(
                    "Bulk synchronization completed",
                    success_count=indexed_count,
                    failed_count=failed_count
                )
            except Exception as exc:
                logger.error("Fatal error during bulk synchronization", error=str(exc))
                raise exc
                
        return indexed_count, failed_count
