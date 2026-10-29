import json

import httpx
import jsonschema

from civicpulse.config import REPO_ROOT, ProviderConfig
from civicpulse.llm.router import LLMRouter
from civicpulse.pipeline.brief import BriefStage

SCHEMA = json.loads((REPO_ROOT / "packages/contracts/schemas/brief.schema.json").read_text())
CLUSTER, ACTOR = "9f8e7d6c-0000-4000-8000-000000000001", "c0000000-0000-4000-8000-000000000001"
KEY = f"{CLUSTER}:{ACTOR}"


def context(lang="es"):
    return {
        "cluster": {"ref": "c_9f8e7d6c", "id": CLUSTER, "status": "validated", "domain_id": "roads",
                    "category_id": "pothole", "category_name": {"es": "Bache", "en": "Pothole"}},
        "stakeholder": {"id": ACTOR, "name": "RRT - Road Repair Test Co.", "kind": "government",
                        "profile": "operational", "lang": lang},
        "actions": ["pothole_patching", "temporary_signage"],
        "metrics": {"m_report_count": 4, "m_distinct_devices": 3, "m_photo_confirmed": 2, "m_max_severity": 4,
                    "m_people_count": None, "m_days_open": 2, "m_weighted_support": 1.5, "m_priority_score": 3.4},
        "reports": [{"ref": "r_1a2b3c4d", "id": "1a2b3c4d-0000-4000-8000-000000000000", "severity": 4,
                     "intake_status": "REPORT_CONFIRMED_BY_PHOTO", "photo_confirmed": True}],
        "nearby": [{"ref": "osm_4412", "layer": "schools", "name": "Escuela", "distance_m": 80}],
    }


class FakeDb:
    def __init__(self, ctx):
        self.ctx, self.dispatch = ctx, {"cluster_id": CLUSTER, "stakeholder_id": ACTOR, "brief": None}
        self.inserted, self.rpcs = [], []

    def select_one(self, table, **eq):
        assert table == "dispatches" and eq == {"cluster_id": CLUSTER, "stakeholder_id": ACTOR}
        return self.dispatch

    def rpc(self, fn, args):
        self.rpcs.append((fn, args))
        return self.ctx

    def update_where(self, table, patch, params):
        assert params["brief"] == "is.null"
        if self.dispatch["brief"] is not None:
            return False
        self.dispatch |= patch
        return True

    def insert(self, table, row):
        self.inserted.append((table, row))


def router(answers, monkeypatch):
    monkeypatch.setenv("K1", "x")
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        status, content = answers.pop(0) if answers else (500, "")
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return LLMRouter([ProviderConfig("a", "https://a.test/v1", "m", "K1")],
                     client=httpx.Client(transport=httpx.MockTransport(handler))), seen


def answer(body, actions=None):
    return 200, json.dumps({"title": "Bache con 4 reportes", "body_markdown": body,
                            "actions": actions if actions is not None else [
                                {"action_id": "pothole_patching", "rationale": "Hay fotos confirmadas.",
                                 "citations": ["m_photo_confirmed"]}]})


GOOD = ("Cuatro reportes de un bache desde tres dispositivos [m_report_count] [m_distinct_devices]. "
        "Dos fotos lo confirman [m_photo_confirmed] [r_1a2b3c4d]. Hay una escuela a 80 m [osm_4412].")


def test_brief_cites_only_known_ids_and_keeps_the_contract(monkeypatch):
    db = FakeDb(context())
    r, seen = router([answer(GOOD)], monkeypatch)
    assert BriefStage(db, r).run(KEY) == "written"
    brief = db.dispatch["brief"]
    jsonschema.validate(brief, SCHEMA)
    assert brief["validator"] == {"ok": True, "unknown_ids": [], "invalid_actions": [], "retries": 0}
    assert {c["id"] for c in brief["citations"]} == {"m_report_count", "m_distinct_devices", "m_photo_confirmed",
                                                     "r_1a2b3c4d", "osm_4412"}
    assert next(c for c in brief["citations"] if c["id"] == "r_1a2b3c4d")["ref"].startswith("1a2b3c4d-")
    assert brief["model"] == {"provider": "a", "model": "m"} and brief["lang"] == "es"
    prompt = seen[0]["messages"]
    assert "Spanish" in prompt[0]["content"] and "pothole_patching" in prompt[0]["content"]
    assert CLUSTER not in prompt[1]["content"]  # the model sees citation ids, not internal uuids
    assert db.inserted[-1][1]["stage"] == "brief" and db.inserted[-1][1]["status"] == "ok"


def test_invented_id_gets_one_retry(monkeypatch):
    db = FakeDb(context())
    r, seen = router([answer(GOOD + " Lo reportó la junta parroquial [r_deadbeef]."), answer(GOOD)], monkeypatch)
    BriefStage(db, r).run(KEY)
    brief = db.dispatch["brief"]
    assert len(seen) == 2 and "r_deadbeef" in seen[1]["messages"][1]["content"]
    assert brief["validator"]["ok"] and brief["validator"]["retries"] == 1 and brief["model"]["provider"] == "a"


def test_still_invalid_falls_back_to_the_template(monkeypatch):
    db = FakeDb(context("en"))
    bad = answer(GOOD, actions=[{"action_id": "close_the_street", "rationale": "x", "citations": ["m_report_count"]}])
    r, _ = router([bad, bad], monkeypatch)
    BriefStage(db, r).run(KEY)
    brief = db.dispatch["brief"]
    jsonschema.validate(brief, SCHEMA)
    assert brief["model"] == {"provider": None, "model": None} and brief["actions"] == []
    assert brief["validator"]["ok"] and brief["lang"] == "en"
    assert brief["title"] == "4 reports of pothole" and "[m_photo_confirmed]" in brief["body_markdown"]


def test_model_down_writes_the_template_without_severity_when_unknown(monkeypatch):
    ctx = context()
    ctx["metrics"]["m_max_severity"] = None
    db = FakeDb(ctx)
    r, _ = router([], monkeypatch)
    BriefStage(db, r).run(KEY)
    brief = db.dispatch["brief"]
    assert brief["validator"]["ok"] and "Severidad" not in brief["body_markdown"]
    assert db.inserted[-1][1]["provider"] == "template"


def test_a_written_brief_is_never_rewritten(monkeypatch):
    db = FakeDb(context())
    db.dispatch["brief"] = {"title": "ya"}
    r, seen = router([answer(GOOD)], monkeypatch)
    assert BriefStage(db, r).run(KEY) == "already_written" and not seen and not db.rpcs
