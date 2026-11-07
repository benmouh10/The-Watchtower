"""
GlobeLens AI — ChatbotController
================================
POST /chatbot
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.controllers.auth_controller import get_current_user
from src.core.database import get_db
from src.data.entities.models import User
from src.services.llm_service import LLMService
from src.services.entitlement_service import (
    METER_CHAT,
    UsageService,
    require_action_quota,
)

router = APIRouter()

class ChatbotRequest(BaseModel):
    message: str

class ChatbotResponse(BaseModel):
    reply: str
    source: str
    in_database: bool

@router.post("", response_model=ChatbotResponse, summary="Query AI News-Scout assistant")
async def chat_query(
    payload: ChatbotRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _quota_guard: None = Depends(require_action_quota(METER_CHAT)),
):
    query_str = payload.message.strip()
    if not query_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty."
        )

    llm = LLMService()
    reply, source, in_database = await llm.answer_chatbot_question(query_str)

    await UsageService(db).record(current_user.id, METER_CHAT)
    await db.commit()

    return ChatbotResponse(
        reply=reply,
        source=source,
        in_database=in_database
    )
