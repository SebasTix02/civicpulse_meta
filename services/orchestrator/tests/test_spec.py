import json

import httpx

from civicpulse.catalog import Catalog
from civicpulse.config import REPO_ROOT, ProviderConfig
from civicpulse.llm.router import LLMRouter
from civicpulse.pipeline.intake import build_spec

CATALOG = Catalog.load(REPO_ROOT / "packages/catalog/civic_catalog.json")


def router_with(answers, monkeypatch):
    """Router over two fake providers; `answers` is a list of (status, content) consumed in order."""
    monkeypatch.setenv("K1", "x")
    monkeypatch.setenv("K2", "x")
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.host)
        status, content = answers.pop(0)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    providers = [ProviderConfig("a", "https://a.test/v1", "m", "K1"), ProviderConfig("b", "https://b.test/v1", "m", "K2")]
    return LLMRouter(providers, client=httpx.Client(transport=httpx.MockTransport(handler))), calls


GOOD = json.dumps({"category_id": "pothole", "summary_en": "Deep pothole in front of the market", "severity": 3,
                   "sam_prompts": ["deep pothole", "BAD PROMPT!!"], "photo_verifiable": True, "confidence": 0.9,
                   "contains_personal_data": False})


def test_keywords_in_spanish():
    assert CATALOG.keyword_match("Hay un BACHE enorme frente al mercado") == "pothole"
    assert CATALOG.keyword_match("nada que ver") is None


def test_spec_merges_catalog_and_drops_bad_prompts(monkeypatch):
    router, _calls = router_with([(200, GOOD)], monkeypatch)
    out = build_spec("Hay un bache enorme", CATALOG, router)
    assert out.provider == "a"
    assert out.spec["category_id"] == "pothole"
    assert "deep pothole" in out.spec["target_prompts"] and "pothole" in out.spec["target_prompts"]
    assert all(p.islower() for p in out.spec["target_prompts"])


def test_falls_through_to_next_provider(monkeypatch):
    router, calls = router_with([(500, ""), (200, GOOD)], monkeypatch)
    assert build_spec("bache", CATALOG, router).provider == "b"
    assert calls == ["a.test", "b.test"]


def test_invalid_json_gets_one_retry(monkeypatch):
    router, calls = router_with([(200, "not json"), (200, GOOD)], monkeypatch)
    assert build_spec("bache", CATALOG, router).provider == "a"
    assert calls == ["a.test", "a.test"]


def test_all_providers_down_uses_keywords(monkeypatch):
    router, _ = router_with([(503, ""), (503, "")], monkeypatch)
    out = build_spec("hay un bache", CATALOG, router)
    assert out.provider == "keywords" and out.spec["category_id"] == "pothole"
    assert out.spec["contains_personal_data"] is True
