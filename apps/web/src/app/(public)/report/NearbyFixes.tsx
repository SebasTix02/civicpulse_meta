"use client";

// C8 "Revisar un arreglo" (UX flows §3.3, migration 30): fixes the AI sees as done, near the
// neighbour, that someone else photographed. Two neighbours confirm, or two deny.

import { useState } from "react";
import { errorMessage } from "@/lib/errors";
import { t, type Lang } from "@/lib/i18n";
import { browserClient, publicStorageBase } from "@/lib/supabase";

type Item = {
  resolution_id: string;
  category_name: { es?: string; en?: string } | null;
  before_public_path: string;
  after_public_path: string;
  distance_m: number;
  votes: { approvals: number; rejections: number; needed: number };
};

function pub(path: string) {
  return publicStorageBase ? `${publicStorageBase}/${path.replace(/^public\//, "")}` : "";
}

export function NearbyFixes({ lang }: { lang: Lang }) {
  const [state, setState] = useState<"idle" | "loading" | "done">("idle");
  const [items, setItems] = useState<Item[]>([]);
  const [canReview, setCanReview] = useState(true);
  const [done, setDone] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  async function find() {
    setState("loading");
    setError("");
    try {
      const pos = await new Promise<GeolocationPosition>((ok, no) =>
        navigator.geolocation.getCurrentPosition(ok, no, { enableHighAccuracy: true, timeout: 15000 }));
      const sb = browserClient();
      const { data: s } = await sb.auth.getSession();
      if (!s.session) {
        const { error: e } = await sb.auth.signInAnonymously();
        if (e) throw e;
      }
      const { data, error: e } = await sb.rpc("nearby_resolutions_to_review", { p_lat: pos.coords.latitude, p_lon: pos.coords.longitude });
      if (e) throw e;
      setItems((data?.items ?? []) as Item[]);
      setCanReview(Boolean(data?.can_review));
    } catch (e) {
      setError(e instanceof GeolocationPositionError ? t(lang, "reportGpsDenied") : errorMessage(lang, e));
    } finally {
      setState("done");
    }
  }

  async function vote(id: string, verdict: "approve" | "reject") {
    setError("");
    const { data, error: e } = await browserClient().rpc("review_community_resolution", { p_resolution_id: id, p_verdict: verdict });
    if (e) return setError(errorMessage(lang, e));
    setDone((d) => ({ ...d, [id]: t(lang, "fixThanks").replace("{a}", String(data.approvals)).replace("{n}", String(data.needed)) }));
  }

  return (
    <div className="card">
      <h2>{t(lang, "fixNearbyTitle")}</h2>
      {state !== "done" ? (
        <button className="secondary block" disabled={state === "loading"} onClick={find}>{t(lang, "fixNearbyFind")}</button>
      ) : !items.length ? (
        <p className="muted small">{t(lang, "fixNearbyNone")}</p>
      ) : (
        <>
          {!canReview ? <p className="notice warn small">{errorMessage(lang, "trust_too_low")}</p> : null}
          {items.map((it) => (
            <div key={it.resolution_id} className="fix-review">
              <b>{it.category_name?.[lang] ?? ""}</b>{" "}
              <span className="muted small">· {t(lang, "fixAway").replace("{m}", String(it.distance_m))} · {t(lang, "fixVotes")
                .replace("{a}", String(it.votes.approvals)).replace("{n}", String(it.votes.needed))}</span>
              <div className="fix-pair">
                <figure><img src={pub(it.before_public_path)} alt={t(lang, "resBefore")} /><figcaption>{t(lang, "resBefore")}</figcaption></figure>
                <figure><img src={pub(it.after_public_path)} alt={t(lang, "resAfterRaw")} /><figcaption>{t(lang, "resAfterRaw")}</figcaption></figure>
              </div>
              {done[it.resolution_id] ? (
                <p className="notice ok small" role="status">{done[it.resolution_id]}</p>
              ) : canReview ? (
                <>
                  <p className="small">{t(lang, "fixNearbyAsk")}</p>
                  <div className="row">
                    <button className="primary" onClick={() => vote(it.resolution_id, "approve")}>{t(lang, "fixYes")}</button>
                    <button className="secondary" onClick={() => vote(it.resolution_id, "reject")}>{t(lang, "fixNo")}</button>
                  </div>
                </>
              ) : null}
            </div>
          ))}
        </>
      )}
      {error ? <p className="notice warn small" role="alert">{error}</p> : null}
    </div>
  );
}
