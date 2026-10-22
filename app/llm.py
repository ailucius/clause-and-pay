"""Gemini calls with JSON schema output. A key from the environment (Gemini API), or
Vertex AI under the runtime's own identity when GOOGLE_GENAI_USE_VERTEXAI is set."""
from __future__ import annotations

import os
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "gemini-3.8-flash"


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


async def generate_json(prompt: str, schema: type[T], *, temperature: float = 0.1,
                        attachments: list[tuple[bytes, str]] | None = None) -> T:
    """`attachments`: (bytes, mime type) pairs sent with the prompt, e.g. an image the model reads."""
    from google.genai import types
    contents: object = prompt
    if attachments:
        contents = [types.Part.from_bytes(data=b, mime_type=mime) for b, mime in attachments] + [prompt]
    resp = await _get_client().aio.models.generate_content(
        model=model_id(),
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    return schema.model_validate_json(resp.text or "{}")
