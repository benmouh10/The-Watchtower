import sys
sys.path.insert(0, "/app")

import asyncio
from src.services.llm_service import LLMService

async def main():
    service = LLMService()
    print("Running process_pending_events directly...")
    processed, errors = await service.process_pending_events()
    print(f"Completed! Processed: {processed}, Errors: {errors}")

if __name__ == "__main__":
    asyncio.run(main())
