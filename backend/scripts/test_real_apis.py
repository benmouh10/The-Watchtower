import asyncio
import sys
import os

# Add /app to python path
sys.path.insert(0, "/app")

from src.services.embedding_service import EmbeddingService
from src.services.llm_service import LLMService

async def test_azure_embedding():
    print("Testing Azure AI Foundry Embedding Service...")
    try:
        service = EmbeddingService()
        vector = await service.generate_vector("Testing the Azure AI Foundry embedding service connection for 1536 dimensions validation.")
        print(f"✅ Success! Generated vector dimensions: {len(vector)}")
        print(f"Vector preview (first 5 values): {vector[:5]}")
        assert len(vector) == 1536, f"Expected 1536 dimensions, got {len(vector)}"
    except Exception as e:
        print(f"❌ Failed Azure embedding: {e}")
        import traceback
        traceback.print_exc()

async def test_nvidia_summary():
    print("\nTesting Nvidia NIM LLM Service...")
    try:
        service = LLMService()
        articles = [
            "Title: Global Tech Summit Announced\nContent: The annual Global Tech Summit will be held in Tokyo this year, focusing on new developments in artificial intelligence, carbon-neutral computing, and secure quantum networks. Organizers expect over 10,000 attendees from 50 countries.",
            "Title: Tokyo to Host AI Conference\nContent: Leaders in tech are gathering in Tokyo for the Global Tech Summit. Key themes include sustainable computing architectures and quantum security standards. The event has drawn massive interest globally."
        ]
        intelligence = await service.analyze_event_cluster(articles)
        print("✅ Success! Event intelligence summary generated.")
        print(f"Topic: {intelligence.topic}")
        print(f"Bias Lean: {intelligence.bias_lean}")
        print(f"Country Focus: {intelligence.location_country}")
        print(f"Coordinates: Lat {intelligence.latitude}, Lon {intelligence.longitude}")
        print(f"Importance Score: {intelligence.importance_score}")
        print(f"Summary (first paragraph):\n{intelligence.summary.splitlines()[0]}")
    except Exception as e:
        print(f"❌ Failed Nvidia LLM synthesis: {e}")
        import traceback
        traceback.print_exc()

async def main():
    print("==================================================")
    print("Starting integration verification of new APIs")
    print("==================================================")
    await test_azure_embedding()
    await test_nvidia_summary()
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
