import json

import httpx

from civicpulse.config import ProviderConfig, _provider_chain
from civicpulse.llm.router import LLMRouter, extract_json
from civicpulse.models import SpecLLMOutput

GOOD = json.dumps({"category_id": "pothole", "summary_en": "Pothole", "severity": 3, "sam_prompts": [],
                   "photo_verifiable": True, "confidence": 0.9, "contains_personal_data": False})


def test_baseten_first_with_hackathon_model(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    first = _provider_chain()[0]
    assert first.name == "baseten_muse_spark_open"
    assert first.model == "meta-models/muse-spark-open-1026"
    assert first.reasoning_effort == "low" and first.reasoning_min_tokens == 4096


def test_reasoning_provider_gets_budget_and_effort(monkeypatch):
    monkeypatch.setenv("K", "x")
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        msg = {"content": GOOD, "reasoning_content": "The user describes a hole in the road..."}
        return httpx.Response(200, json={"choices": [{"message": msg}]})

    p = ProviderConfig("open", "https://o.test/v1", "m", "K", reasoning_min_tokens=4096, reasoning_effort="low")
    res = LLMRouter([p], client=httpx.Client(transport=httpx.MockTransport(handler))).structured(
        "sys", "text", SpecLLMOutput, max_tokens=400)
    assert res.data.category_id == "pothole"
    assert bodies[0]["max_tokens"] == 4496 and bodies[0]["reasoning_effort"] == "low"


def test_null_content_counts_as_invalid_and_falls_through(monkeypatch):
    monkeypatch.setenv("K", "x")
    answers = [None, None, GOOD]

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": answers.pop(0)}}]})

    providers = [ProviderConfig("a", "https://a.test/v1", "m", "K"), ProviderConfig("b", "https://b.test/v1", "m", "K")]
    res = LLMRouter(providers, client=httpx.Client(transport=httpx.MockTransport(handler))).structured(
        "sys", "text", SpecLLMOutput)
    assert res.provider == "b"


def test_inline_think_block_is_ignored():
    assert extract_json('<think>maybe {"x": 1}</think>{"y": 2}') == {"y": 2}


def test_personal_baseten_key_takes_over_when_hackathon_key_fails(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_ORDER", raising=False)
    monkeypatch.setenv("BASETEN_API_KEY", "hackathon")
    monkeypatch.setenv("BASETEN_PERSONAL_API_KEY", "personal")
    chain = _provider_chain()
    assert [p.name for p in chain[:3]] == ["baseten_muse_spark_open", "baseten_personal_muse_spark_open",
                                           "meta_muse_spark"]
    assert chain[1].model == "meta-models/muse-spark-open-1026"
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.headers["authorization"])
        if request.headers["authorization"].endswith("hackathon"):
            return httpx.Response(402, json={"error": "credits exhausted"})
        return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})

    res = LLMRouter(chain[:2], client=httpx.Client(transport=httpx.MockTransport(handler))).structured(
        "sys", "text", SpecLLMOutput)
    assert res.provider == "baseten_personal_muse_spark_open"
    assert seen == ["Bearer hackathon", "Bearer personal"]
