"""LLM client. The model name comes from GEMINI_MODEL, never from code: Google retires
model versions often. Groq fallback arrives in M6 behind the same two methods."""

import os
import re
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel


class LLMUnavailable(Exception):
    """No API key/model configured, or the provider failed (quota, outage, bad model name).

    status is the provider's HTTP status when there is one: 429/5xx are worth retrying,
    other 4xx (bad model name, bad key) are configuration errors that never fix themselves.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def is_config_error(self) -> bool:
        """4xx other than 429: wrong model name, bad key, no access. Retrying can't help."""
        return self.status is not None and 400 <= self.status < 500 and self.status != 429


MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalise_model(raw: str | None) -> str:
    """Turn GEMINI_MODEL into a bare model id, or fail with a message saying what's wrong.

    Tolerates surrounding whitespace/quotes and a 'models/' prefix; rejects pasted code
    such as model='models/gemini-x-flash'.
    """
    value = (raw or "").strip().strip("'\"").strip()
    if value.startswith("models/"):
        value = value[len("models/"):]
    if not MODEL_ID.match(value):
        raise LLMUnavailable("GEMINI_MODEL must be a bare model id like gemini-...-flash "
                             "(no 'model=', quotes or spaces)")
    return value


class LLM(Protocol):
    def generate_json(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel: ...
    def generate_text(self, system: str, user: str) -> str: ...


class GeminiLLM:
    def __init__(self) -> None:
        api_key, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_MODEL")
        if not api_key or not model:
            raise LLMUnavailable("GEMINI_API_KEY / GEMINI_MODEL not configured")
        self.model = normalise_model(model)
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
            raise LLMUnavailable(f"Gemini error {e.code}", status=e.code) from e

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
