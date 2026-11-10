"""Gemini calls with JSON schema output. A key from the environment (Gemini API), or
Vertex AI under the runtime's own identity when GOOGLE_GENAI_USE_VERTEXAI is set."""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, Callable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

DEFAULT_MODEL = "gemini-3.8-flash"

# A delivery or evidence check normally answers in 5 to 20 s. One model call that stalls
# should cost one fresh try, not the whole time limit: on 5 Nov 2026 a check that answers
# in 7 s waited out the 90 s limit and failed. Two tries of 40 s stay inside that limit.
CHECK_ATTEMPT_SECONDS = float(os.environ.get("CHECK_ATTEMPT_SECONDS", "40"))
CHECK_ATTEMPTS = 2
_TRANSIENT_CODES = (429, 500, 502, 503, 504)


def _transient(e: Exception) -> bool:
    """A busy or failing model service (rate limit, 5xx): worth one more try."""
    return getattr(e, "code", None) in _TRANSIENT_CODES


def model_id() -> str:
    return os.environ.get("CLAUSEPAY_MODEL", DEFAULT_MODEL)


def _use_vertex() -> bool:
    return os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes")


def ai_configured() -> bool:
    if _use_vertex():
        return bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        if _use_vertex():
            # On Cloud Run: Vertex AI under the service's own identity, no key.
            _client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                                   location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"))
            return _client
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("Set GEMINI_API_KEY to use the AI features.")
        _client = genai.Client(api_key=key)
    return _client


async def with_fresh_tries(make_call: Callable[[], Awaitable[R]], *, attempt_seconds: float | None,
                           attempts: int) -> R:
    """Run one model call; if it stalls past `attempt_seconds`, or the service is busy
    or failing, start a fresh one, up to `attempts` calls in all."""
    attempts = max(1, attempts)
    for n in range(attempts):
        call = make_call()
        try:
            return await (asyncio.wait_for(call, timeout=attempt_seconds) if attempt_seconds else call)
        except asyncio.TimeoutError:
            if n == attempts - 1:
                raise
        except Exception as e:
            if n == attempts - 1 or not _transient(e):
                raise
    raise AssertionError("unreachable")


async def generate_json(prompt: str, schema: type[T], *, temperature: float = 0.1,
                        attachments: list[tuple[bytes, str]] | None = None,
                        attempt_seconds: float | None = None, attempts: int = 1) -> T:
    """`attachments`: (bytes, mime type) pairs sent with the prompt, e.g. an image the model reads.
    `attempt_seconds` and `attempts`: give each call that long, then start a fresh one, up to
    `attempts` calls; a rate limit or server error also gets the fresh call. Without them
    the call runs once, as long as the caller allows."""
    from google.genai import types
    contents: object = prompt
    if attachments:
        contents = [types.Part.from_bytes(data=b, mime_type=mime) for b, mime in attachments] + [prompt]
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )
    resp = await with_fresh_tries(
        lambda: _get_client().aio.models.generate_content(model=model_id(), contents=contents, config=config),
        attempt_seconds=attempt_seconds, attempts=attempts)
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    return schema.model_validate_json(resp.text or "{}")
