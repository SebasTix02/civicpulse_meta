"""Stage `brief` (plan §3.3 step 8): the text a stakeholder reads about a validated cluster.

1. brief_context() (migration 13) builds the facts in SQL, each with the id that cites it.
   Citizen text never reaches it, so it never reaches a brief.
2. One Muse call writes title, body and up to 3 actions in the stakeholder's language and
   profile (operational, technical, citizen, press).
3. The validator checks that every cited id exists in the context and that every action is in
   the domain's action catalog. A failure gets one retry with the problems shown to the model.
4. If the model is down or still invents ids, a fixed template writes the brief from the
   metrics alone (model null): the panel always gets a brief, and never an unchecked claim.

The brief is stored in dispatches.brief only while it is null, so duplicate webhooks are safe.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime

from pydantic import ValidationError

from civicpulse.db import Db
from civicpulse.llm.prompts import brief_system
from civicpulse.llm.router import AllProvidersFailed, LLMRouter
from civicpulse.models import BriefLLMOutput

CITATION = re.compile(r"\[([^\[\]]+)\]")
CITATION_ID = re.compile(r"^(r_[0-9a-f]{8}|c_[0-9a-f]{8}|osm_[0-9]+|naufest_[A-Za-z0-9_]+|m_[a-z_]+)$")


def allowed_ids(ctx: dict) -> dict[str, tuple[str, str]]:
    """Citation id -> (kind, ref) for everything the context contains."""
    ids = {ctx["cluster"]["ref"]: ("cluster", ctx["cluster"]["id"])}
    ids |= {r["ref"]: ("report", r["id"]) for r in ctx.get("reports", [])}
    ids |= {f["ref"]: ("geo_feature", f["ref"]) for f in ctx.get("nearby", [])}
    ids |= {k: ("metric", k) for k, v in (ctx.get("metrics") or {}).items() if v is not None}
    return ids


def cited(out: BriefLLMOutput) -> list[str]:
    found = [i.strip() for m in CITATION.findall(out.title + " " + out.body_markdown) for i in re.split(r"[,;\s]+", m)]
    found += [c for a in out.actions for c in a.citations]
    return list(dict.fromkeys(i for i in found if i))


def validate(out: BriefLLMOutput, ctx: dict) -> dict:
    known = allowed_ids(ctx)
    unknown = [i for i in cited(out) if i not in known or not CITATION_ID.match(i)]
    invalid = [a.action_id for a in out.actions if a.action_id not in ctx.get("actions", [])]
    uncited = not CITATION.search(out.body_markdown)
    report = {"ok": not unknown and not invalid and not uncited, "unknown_ids": unknown, "invalid_actions": invalid}
    if uncited:
        report["unknown_ids"] = [*unknown, "(body has no citations)"]
    return report


TEMPLATE = {
    "es": {
        "title": "{n} reportes de {cat}",
        "count": "{n} reportes de {cat} desde {d} dispositivos distintos [m_report_count] [m_distinct_devices] [{c}].",
        "photo": "{p} tienen la foto verificada por la IA [m_photo_confirmed].",
        "severity": "Severidad máxima {s} de 5 [m_max_severity].",
        "days": "Abierto hace {days} días [m_days_open].",
    },
    "en": {
        "title": "{n} reports of {cat}",
        "count": "{n} reports of {cat} from {d} different devices [m_report_count] [m_distinct_devices] [{c}].",
        "photo": "{p} have the photo verified by the AI [m_photo_confirmed].",
        "severity": "Highest severity {s} of 5 [m_max_severity].",
        "days": "Open for {days} days [m_days_open].",
    },
}


def template_brief(ctx: dict, lang: str) -> BriefLLMOutput:
    """Facts from the metrics only, each sentence cited; used when the model cannot be trusted."""
    m, cl = ctx["metrics"], ctx["cluster"]
    t = TEMPLATE.get(lang, TEMPLATE["es"])
    fill = {"n": m["m_report_count"], "d": m["m_distinct_devices"], "p": m["m_photo_confirmed"],
            "s": m.get("m_max_severity"), "days": m["m_days_open"], "c": cl["ref"],
            "cat": ((cl.get("category_name") or {}).get(lang) or cl["category_id"]).lower()}
    parts = [t["count"], t["photo"]] + ([t["severity"]] if fill["s"] is not None else []) + [t["days"]]
    return BriefLLMOutput(title=t["title"].format(**fill)[:140], body_markdown=" ".join(parts).format(**fill), actions=[])


class BriefStage:
    def __init__(self, db: Db, router: LLMRouter, max_tokens: int = 1200):
        self.db, self.router, self.max_tokens = db, router, max_tokens

    def run(self, key: str) -> str:
        cluster_id, stakeholder_id = key.split(":", 1)
        dispatch = self.db.select_one("dispatches", cluster_id=cluster_id, stakeholder_id=stakeholder_id)
        if dispatch is None:
            return "no_dispatch"
        if dispatch.get("brief") is not None:
            return "already_written"
        started = time.monotonic()
        try:
            brief, provider, model = self._write(cluster_id, stakeholder_id)
        except Exception as exc:  # recorded, then re-raised so Modal shows the failure
            self._log(cluster_id, stakeholder_id, "error", None, None, started, f"{type(exc).__name__}: {str(exc)[:300]}")
            raise
        stored = self.db.update_where("dispatches", {"brief": brief}, {
            "cluster_id": f"eq.{cluster_id}", "stakeholder_id": f"eq.{stakeholder_id}", "brief": "is.null"})
        self._log(cluster_id, stakeholder_id, "ok" if stored else "skipped", provider, model, started)
        return "written" if stored else "already_written"

    def _write(self, cluster_id: str, stakeholder_id: str) -> tuple[dict, str | None, str | None]:
        ctx = self.db.rpc("brief_context", {"p_cluster": cluster_id, "p_stakeholder": stakeholder_id})
        st = ctx["stakeholder"]
        lang = st["lang"] if st["lang"] in ("es", "en") else "es"
        profile = st["profile"]
        system = brief_system(profile, lang, ctx.get("actions") or [])
        user = "Context JSON:\n" + _context_for_model(ctx)
        out, check, provider, model, retries = None, None, None, None, 0
        for attempt in range(2):
            try:
                res = self.router.structured(system, user, BriefLLMOutput, max_tokens=self.max_tokens)
            except (AllProvidersFailed, ValidationError):
                break
            out, provider, model = res.data, res.provider, res.model  # type: ignore[assignment]
            check = validate(out, ctx)  # type: ignore[arg-type]
            if check["ok"]:
                break
            retries = attempt + 1
            user += ("\n\nYour previous answer was rejected. Unknown citation ids: " + ", ".join(check["unknown_ids"])
                     + ". Actions not in the catalog: " + ", ".join(check["invalid_actions"])
                     + ". Use only ids and actions from the context. Answer again with the full JSON.")
        if out is None or not check or not check["ok"]:
            out, provider, model = template_brief(ctx, lang), None, None
            check = validate(out, ctx)
        known = allowed_ids(ctx)
        brief = {
            "schema_version": "1.0", "lang": lang, "profile": profile,
            "title": out.title, "body_markdown": out.body_markdown,
            "actions": [a.model_dump() for a in out.actions],
            "citations": [{"id": i, "kind": known[i][0], "ref": known[i][1]} for i in cited(out) if i in known],
            "validator": {**check, "retries": retries},
            "model": {"provider": provider, "model": model},
            "generated_at": datetime.now(UTC).isoformat(),
        }
        return brief, provider, model

    def _log(self, cluster_id, stakeholder_id, status, provider, model, started, error=None) -> None:
        self.db.insert("stage_runs", {"cluster_id": cluster_id, "stakeholder_id": stakeholder_id, "stage": "brief",
                                      "status": status, "provider": provider or "template", "model": model,
                                      "error": error, "latency_ms": int((time.monotonic() - started) * 1000)})


def _context_for_model(ctx: dict) -> str:
    """The context without internal uuids: the model only needs the citation ids."""
    slim = {
        "cluster": {k: v for k, v in ctx["cluster"].items() if k != "id"},
        "stakeholder": {k: v for k, v in ctx["stakeholder"].items() if k != "id"},
        "metrics": ctx.get("metrics"),
        "reports": [{k: v for k, v in r.items() if k != "id"} for r in ctx.get("reports", [])],
        "nearby": ctx.get("nearby", []),
        "actions": ctx.get("actions", []),
    }
    return json.dumps(slim, ensure_ascii=False, default=str)
