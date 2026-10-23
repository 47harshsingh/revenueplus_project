from collections.abc import Callable

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.chat.llm import LLM, get_llm
from api.chat.service import ChatResult, answer_question

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@router.post("/chat", response_model=ChatResult)
def chat(req: ChatRequest, make_llm: Callable[[], LLM] = Depends(get_llm)):
    # A refusal is a normal answer (200, status="refused"), not an error.
    return answer_question(req.question.strip(), make_llm())
