"""Thin Claude wrapper: one call in, schema-validated JSON out.

Uses structured outputs (output_config.format) so every response parses, and the
server-side refusal fallback so a rare safety decline gets re-run on a fallback model
instead of stalling the factory.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import anthropic

from . import log

logger = log.get("producer")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    pass


def image_block(path: str | Path, media_type: str = "image/jpeg") -> dict:
    data = base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def strict_schema(schema: dict) -> dict:
    """Structured outputs want additionalProperties:false + every property required on each object."""
    schema = json.loads(json.dumps(schema))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node.setdefault("additionalProperties", False)
                node.setdefault("required", list(node["properties"].keys()))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


class Claude:
    def __init__(self, model: str = "claude-opus-5-5", client: anthropic.Anthropic | None = None):
        self.model = model
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=4)
        return self._client

    def json(
        self,
        system: str,
        content: list[dict] | str,
        schema: dict,
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> dict:
        if isinstance(content, str):
            content = [text_block(content)]
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": strict_schema(schema)},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.BadRequestError as e:
            raise LLMError(f"Claude rejected the request: {e.message}") from e
        except anthropic.AuthenticationError as e:
            raise LLMError("ANTHROPIC_API_KEY is missing or invalid") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Claude rate limit hit; will retry next tick") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Claude API") from e

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise LLMRefusal(f"Claude declined (category={category})")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude response hit max_tokens before finishing")

        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError("Claude returned no text block")
        u = response.usage
        logger.debug("claude %s in=%s out=%s", response.model, u.input_tokens, u.output_tokens)
        return json.loads(text)
