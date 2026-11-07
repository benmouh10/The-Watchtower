"""
GlobeLens AI — TavilyService
============================
Integrates the Tavily Search API to execute real-time queries for web-grounded fact checking.
"""
import httpx
import structlog
from typing import List, Dict, Any, Optional
from src.core.config import settings

logger = structlog.get_logger()

class TavilyService:
    def __init__(self) -> None:
        self.api_key = settings.TAVILY_API_KEY
        self.base_url = "https://api.tavily.com/search"

    async def search_web(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Executes a search query against the Tavily Search API.
        Returns a list of dictionaries, each containing 'title', 'url', 'content', and 'score'.
        """
        if not self.api_key:
            logger.warn("Tavily search skipped: TAVILY_API_KEY is not set.")
            return []

        # Clean/truncate the query
        cleaned_query = query.strip()
        if len(cleaned_query) > 200:
            first_line = cleaned_query.split('\n')[0].strip()
            if len(first_line) > 30:
                cleaned_query = first_line[:200]
            else:
                cleaned_query = cleaned_query[:200]

        logger.info("Executing Tavily web search", query=cleaned_query, max_results=max_results)

        payload = {
            "api_key": self.api_key,
            "query": cleaned_query,
            "search_depth": "basic",
            "max_results": max_results
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.base_url, json=payload)
                
                if response.status_code != 200:
                    logger.error(
                        "Tavily API error", 
                        status_code=response.status_code, 
                        response_body=response.text
                    )
                    return []

                data = response.json()
                results = data.get("results", [])
                
                logger.info("Tavily search successful", results_count=len(results))
                return results

        except httpx.RequestError as exc:
            logger.error("HTTP request exception during Tavily search", error=str(exc))
            return []
        except Exception as exc:
            logger.error("Unexpected error in Tavily service", error=str(exc))
            return []
