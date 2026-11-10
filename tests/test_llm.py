"""llm.parse_structured error handling, with a fake SDK client (never calls the API)."""

from types import SimpleNamespace as NS

import pytest
from pydantic import BaseModel

from content_clipper import llm


class Inner(BaseModel):
    y: str | None = None


class Out(BaseModel):
    x: int
    inner: Inner = Inner()
    tags: list[str] = []


class FakeClient:
    """Returns the given responses in order (the last one repeats)."""

    def __init__(self, *results, exc=None):
        self.calls = []
        self._results, self._exc = list(results), exc
        self.beta = NS(messages=NS(create=self._create))

    @property
    def kwargs(self):
        return self.calls[-1]

    def _create(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


@pytest.fixture
def use_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(llm.anthropic, "Anthropic", lambda: client)
        monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
        return client
    return install


def call():
    return llm.parse_structured(model="m", system="s", user="u", output_type=Out)


def resp(text=None, stop_reason="end_turn"):
    content = [NS(type="thinking", thinking="")]
    if text is not None:
        content.append(NS(type="text", text=text))
    return NS(stop_reason=stop_reason, content=content, _request_id="req_1")


def test_returns_validated_output_and_sends_fallback(use_client):
    client = use_client(FakeClient(resp('{"x": 1, "inner": {"y": null}, "tags": []}')))
    assert call() == Out(x=1)
    kw = client.kwargs
    assert kw["fallbacks"] == "default" and kw["betas"] == [llm.FALLBACK_BETA]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in kw  # rejected by current models


def test_schema_makes_every_property_required():
    schema = llm.output_schema(Out)
    assert set(schema["required"]) == {"x", "inner", "tags"}
    inner = schema["$defs"]["Inner"]
    assert inner["required"] == ["y"] and "default" not in str(schema)


@pytest.mark.parametrize("r, msg", [
    (resp(stop_reason="refusal"), "refused"),
    (resp(stop_reason="max_tokens"), "max_tokens"),
    (resp(None), "No text in response"),
    (resp('{"x": "not a number", "inner": {"y": null}, "tags": []}'), "did not match"),
])
def test_bad_responses_raise(use_client, r, msg):
    use_client(FakeClient(r))
    with pytest.raises(llm.LLMError, match=msg):
        call()


def test_missing_credentials_become_a_clear_error(use_client):
    use_client(FakeClient(None, exc=TypeError('"Could not resolve authentication method. Expected..."')))
    with pytest.raises(llm.LLMError, match="ANTHROPIC_API_KEY"):
        call()


def test_unrelated_type_errors_are_not_swallowed(use_client):
    use_client(FakeClient(None, exc=TypeError("some bug")))
    with pytest.raises(TypeError, match="some bug"):
        call()


GOOD = '{"x": 1, "inner": {"y": null}, "tags": []}'
BAD = '{"x": "one", "inner": {"y": null}, "tags": []}'


def call_unconstrained():
    return llm.parse_structured(model="m", system="s", user="u", output_type=Out,
                                constrained=False)


def test_unconstrained_puts_schema_in_prompt_and_accepts_fenced_json(use_client):
    client = use_client(FakeClient(resp(f"```json\n{GOOD}\n```")))
    assert call_unconstrained() == Out(x=1)
    kw = client.kwargs
    assert "output_config" not in kw
    assert '"required"' in kw["system"] and kw["system"].startswith("s")


def test_unconstrained_retries_once_with_the_validation_error(use_client):
    client = use_client(FakeClient(resp(BAD), resp(GOOD)))
    assert call_unconstrained() == Out(x=1)
    assert len(client.calls) == 2
    retry_prompt = client.calls[1]["messages"][0]["content"]
    assert retry_prompt.startswith("u") and "did not match the schema" in retry_prompt


def test_unconstrained_gives_up_after_the_retry(use_client):
    client = use_client(FakeClient(resp(BAD), resp(BAD)))
    with pytest.raises(llm.LLMError, match="did not match"):
        call_unconstrained()
    assert len(client.calls) == 2
