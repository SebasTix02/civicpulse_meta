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


def test_retired_category_is_not_offered_or_matched(monkeypatch):
    retired = CATALOG.with_statuses({"pothole": "retired", "other": "retired"})
    assert retired.categories["other"]["status"] == "active", "other always stays active"
    assert "pothole" not in retired.active_ids()
    assert retired.keyword_match("Hay un bache enorme") is None
    router, _calls = router_with([(200, GOOD)], monkeypatch)
    out = build_spec("Hay un bache enorme", retired, router)
    assert out.spec["category_id"] == "other"
    assert CATALOG.categories["pothole"].get("status", "active") == "active", "the packaged catalog is not changed"


def test_pipeline_reads_statuses_once_a_minute():
    from civicpulse.pipeline.intake import IntakePipeline

    class Db:
        calls = 0

        def select(self, table, params):
            Db.calls += 1
            assert table == "categories"
            return [{"id": "pothole", "status": "retired"}]

    pipe = IntakePipeline(Db(), CATALOG, router=None)  # type: ignore[arg-type]
    assert "pothole" not in pipe.current_catalog().active_ids()
    pipe.current_catalog()
    assert Db.calls == 1

    class Down:
        def select(self, table, params):
            raise RuntimeError("db down")

    assert "pothole" in IntakePipeline(Down(), CATALOG, router=None).current_catalog().active_ids()  # type: ignore[arg-type]


def test_keyword_risk_signals():
    from civicpulse.signals import keyword_signals, merge_signals

    assert keyword_signals("Ayer se cayó una moto en el bache frente a la escuela, de noche no se ve") == [
        "accident", "near_school", "night_hazard"]
    assert keyword_signals("Me reventó la llanta, los carros se desvían y hay tráfico") == ["vehicle_damage", "blocks_traffic"]
    assert keyword_signals("Compré chocolate en el rincón de la esquina") == []
    assert keyword_signals("Pothole again, a cyclist fell and got hurt at night") == ["accident", "injury", "night_hazard", "recurring"]
    assert merge_signals(["recurring", "accident"], ["accident", "flooding"]) == ["accident", "flooding", "recurring"]


def test_spec_carries_model_and_keyword_signals(monkeypatch):
    answer = json.loads(GOOD) | {"risk_signals": ["blocks_traffic", "made_up", "blocks_traffic"]}
    router, _calls = router_with([(200, json.dumps(answer))], monkeypatch)
    out = build_spec("Hay un bache enorme frente al colegio", CATALOG, router)
    assert out.spec["risk_signals"] == ["blocks_traffic", "near_school"]
