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

    def __init__(self, message: str, status: int | None = None,
                 quota_ids: tuple[str, ...] = (), retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.quota_ids = quota_ids      # e.g. GenerateRequestsPerDayPerProjectPerModel-FreeTier
        self.retry_after = retry_after  # seconds, from Google's RetryInfo, if given

    @property
    def is_config_error(self) -> bool:
        """4xx other than 429: wrong model name, bad key, no access. Retrying can't help."""
        return self.status is not None and 400 <= self.status < 500 and self.status != 429


    @property
    def is_daily_quota(self) -> bool:
        """429 for the per-day free-tier quota: it won't reset within one eval run. Google
        often names it only in the error details (quotaId ...PerDay...), not the message."""
        text = " ".join((str(self), *self.quota_ids)).lower()
        return self.status == 429 and ("per day" in text or "perday" in text)


def _quota_info(details) -> tuple[tuple[str, ...], float | None]:
    """Pull quota ids and the suggested retry delay out of a Google API error payload."""
    items = ((details or {}).get("error") or {}).get("details") or []
    quota_ids, retry_after = [], None
    for item in items if isinstance(items, list) else []:
        for v in item.get("violations") or []:
            if v.get("quotaId"):
                quota_ids.append(v["quotaId"])
        delay = item.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                retry_after = float(delay[:-1])
            except ValueError:
                pass
    return tuple(quota_ids), retry_after


MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REQUEST_TIMEOUT_MS = 30_000  # google-genai's HttpOptions.timeout is in MILLISECONDS


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
        from google.genai import types

        self.client = genai.Client(api_key=api_key,
                                   http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))

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
            # Keep which limit was hit (per minute vs per day) and Google's suggested wait.
            quota_ids, retry_after = _quota_info(getattr(e, "details", None))
            quota = f" [quota: {', '.join(quota_ids)}]" if quota_ids else ""
            raise LLMUnavailable(f"Gemini error {e.code}: {(e.message or '')[:200]}{quota}",
                                 status=e.code, quota_ids=quota_ids,
                                 retry_after=retry_after) from e
        except Exception as e:  # timeouts and network errors: transient, no HTTP status
            raise LLMUnavailable(f"Gemini request failed: {type(e).__name__}") from e

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
