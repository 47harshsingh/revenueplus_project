"""LLM client. The model name comes from GEMINI_MODEL, never from code: Google retires
model versions often. Groq fallback arrives in M6 behind the same two methods."""

import os
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel


class LLMUnavailable(Exception):
    """No API key/model configured, or the provider failed (quota, outage, bad model name)."""


class LLM(Protocol):
    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel: ...
    def generate_text(self, system: str, user: str) -> str: ...


class GeminiLLM:
    def __init__(self) -> None:
        api_key, self.model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_MODEL")
        if not api_key or not self.model:
            raise LLMUnavailable("GEMINI_API_KEY / GEMINI_MODEL not configured")
        from google import genai  # imported lazily: tests with a fake LLM never need it

        self.client = genai.Client(api_key=api_key)

    def _call(self, system: str, user: str, **config):
        from google.genai import errors, types

        try:
            return self.client.models.generate_content(
                model=self.model,
                contents=user,
                config=types.GenerateContentConfig(system_instruction=system, temperature=0,
                                                   **config),
            )
        except errors.APIError as e:
            raise LLMUnavailable(f"Gemini error {e.code}") from e

    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        resp = self._call(system, user, response_mime_type="application/json",
                          response_schema=schema)
        try:
            return schema.model_validate_json(resp.text)
        except ValueError as e:
            raise LLMUnavailable("model returned malformed JSON") from e

    def generate_text(self, system: str, user: str) -> str:
        return (self._call(system, user).text or "").strip()


def get_llm() -> Callable[[], LLM]:
    """FastAPI dependency returning a factory, not a client: the client (which fails without a
    key) is built only after the request is validated, so bad input is 422 even when the model
    is unavailable. Tests override this with a fake."""
    return GeminiLLM
