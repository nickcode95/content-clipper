"""Thin wrapper around the Anthropic SDK: one structured-output call, validated by pydantic.

Every stage that talks to an LLM goes through `parse_structured`, so tests replace this single
function with a fake and never call the paid API.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
from anthropic.lib._parse._transform import transform_schema
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, ValidationError

# Server-side refusal fallback: if the model declines on a policy classifier, the API retries the
# same request on a fallback model it picks. Harmless for normal requests.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


def _simplify(schema: Any) -> Any:
    """Make every object property required; drop defaults and string formats.

    The API compiles the schema into a grammar and rejects big ones ("compiled grammar is too
    large"). Optional properties and formats such as `date` are what make it big. With every
    property required, the model writes the default itself ("not stated" = null/[]/false);
    pydantic still validates formats and constraints on the reply.
    """
    if isinstance(schema, dict):
        out = {k: _simplify(v) for k, v in schema.items() if k not in ("default", "format")}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
        return out
    if isinstance(schema, list):
        return [_simplify(v) for v in schema]
    return schema


def output_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    return transform_schema(_simplify(output_type.model_json_schema()))


def _extract_json(text: str) -> str:
    """The JSON object in a reply, tolerating code fences or stray prose around it."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _create(client: Any, model: str, **kw: Any) -> Any:
    try:
        return client.beta.messages.create(
            model=model, betas=[FALLBACK_BETA], fallbacks="default", **kw
        )
    except TypeError as e:
        # The SDK raises a bare TypeError when it finds no credentials at all.
        if "authentication method" not in str(e):
            raise
        raise LLMError(
            "No Anthropic credentials found: set ANTHROPIC_API_KEY in .env "
            "(or log in with `ant auth login`)"
        ) from e
    except anthropic.AuthenticationError as e:
        raise LLMError("Anthropic authentication failed: check ANTHROPIC_API_KEY in .env") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError(f"Anthropic permission denied for model {model}: {e.message}") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Model {model!r} not found: check config/models.yaml") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Anthropic rate limit hit (after SDK retries); try again shortly") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API (network)") from e


def _text(resp: Any, max_tokens: int) -> str:
    rid = resp._request_id
    if resp.stop_reason == "refusal":
        raise LLMError(f"Model refused the request (request id {rid})")
    if resp.stop_reason == "max_tokens":
        raise LLMError(f"Response hit max_tokens={max_tokens} before finishing (request id {rid})")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        raise LLMError(f"No text in response (request id {rid})")
    return text


def parse_structured[T: BaseModel](
    *,
    model: str,
    system: str,
    user: str | list[dict[str, Any]],
    output_type: type[T],
    max_tokens: int = 16000,
    effort: str | None = None,
    constrained: bool = True,
) -> T:
    """Ask `model` for JSON matching `output_type` and return the validated object.

    `user` is the prompt text, or a list of content blocks (text and images).

    constrained=True: the API enforces the schema as a grammar (structured outputs). The API
    refuses grammars above a size limit, so large schemas use constrained=False: the schema goes
    into the system prompt, the reply is validated here, and one fresh retry is made with the
    validation error if it does not match.
    """
    load_dotenv(find_dotenv(usecwd=True))  # ANTHROPIC_API_KEY from .env; never print it
    client = anthropic.Anthropic()
    schema = output_schema(output_type)
    output_config: dict[str, Any] = {"effort": effort} if effort else {}
    if constrained:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    else:
        system = (
            f"{system}\n\nReply with exactly one JSON object and nothing else (no prose, no code "
            f"fences). It must match this JSON schema:\n{json.dumps(schema)}"
        )
    extra = {"output_config": output_config} if output_config else {}

    attempts = 1 if constrained else 2
    content = [{"type": "text", "text": user}] if isinstance(user, str) else list(user)
    for attempt in range(1, attempts + 1):
        resp = _create(client, model, max_tokens=max_tokens, system=system,
                       messages=[{"role": "user", "content": content}], **extra)
        text = _text(resp, max_tokens)
        try:
            return output_type.model_validate_json(_extract_json(text))
        except ValidationError as e:
            if attempt == attempts:
                raise LLMError(
                    f"Response did not match the schema (request id {resp._request_id}): {e}"
                ) from e
            content = [*content, {"type": "text", "text": (
                "A previous answer to this request was rejected because its JSON did not match "
                f"the schema:\n{e}\nReturn a corrected JSON object."
            )}]
    raise AssertionError("unreachable")
