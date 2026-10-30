"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { browserClient } from "@/lib/supabase";
import { Lightbox, PhotoFrame, Thumbs, type Shot } from "./Lightbox";
import { TIMELINE, t, type Lang } from "@/lib/i18n";
import type { PublicCluster } from "@/lib/supabase";
import { ClusterMap, STATUS_COLOR } from "./ClusterMap";
import { CameraIcon, CloseIcon } from "./icons";

const DAY = 86_400_000;

function photoOf(c: PublicCluster, base: string | null) {
  return c.photo_public_path && base ? `${base}/${c.photo_public_path.replace(/^public\//, "")}` : null;
}

export function StatusPill({ lang, status }: { lang: Lang; status: string }) {
  return (
    <span className="pill status" style={{ background: STATUS_COLOR[status] ?? "#6B6B66" }}>
      {TIMELINE.find((s) => s.id === status)?.[lang] ?? status}
    </span>
  );
}

// C2: what a neighbour sees when tapping a case.
function CaseSheet({ lang, c, photoBase, onClose }: { lang: Lang; c: PublicCluster; photoBase: string | null; onClose: () => void }) {
  const photo = photoOf(c, photoBase);
  const name = c.category_name?.[lang] ?? c.category_id;
  const since = c.validated_at ?? c.last_report_at;
  const days = since ? Math.max(0, Math.floor(((c.resolved_at ? Date.parse(c.resolved_at) : Date.now()) - Date.parse(since)) / DAY)) : null;
  const fmt = (iso: string) => new Date(iso).toLocaleDateString(lang === "es" ? "es-EC" : "en-US", { day: "numeric", month: "short" });
  const [shots, setShots] = useState<Shot[]>(photo ? [{ src: photo }] : []);
  const [viewing, setViewing] = useState<number | null>(null);

  // Every anonymized photo of the case (migration 19); the map row only carries the best one.
  useEffect(() => {
    setShots(photo ? [{ src: photo }] : []);
    if (!photoBase) return;
    void browserClient().rpc("public_case_photos", { p_cluster_id: c.id }).then(({ data }) => {
      const rows = (data as { photo_public_path: string; created_at: string }[] | null) ?? [];
      if (!rows.length) return;
      setShots(rows.map((r) => ({
        src: `${photoBase}/${r.photo_public_path.replace(/^public\//, "")}`,
        caption: new Date(r.created_at).toLocaleString(lang === "es" ? "es-EC" : "en-US", { dateStyle: "medium", timeStyle: "short" }),
      })));
    });
  }, [c.id, photo, photoBase, lang]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && viewing === null && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, viewing]);

  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <section className="sheet" role="dialog" aria-label={name}>
        <div className="grab" aria-hidden="true" />
        <div className="sheet-head">
          <div>
            <h2>{name}</h2>
            {c.validated_at ? <div className="muted small">{t(lang, "caseValidatedOn")} {fmt(c.validated_at)}</div> : null}
          </div>
          <button className="icon-btn" aria-label={t(lang, "close")} onClick={onClose}><CloseIcon /></button>
        </div>
        <div className="row">
          <StatusPill lang={lang} status={c.status} />
          {photo ? <span className="pill soft">{t(lang, "caseConfirmedByPhoto")}</span> : null}
        </div>
        {shots.length ? (
          <>
            <PhotoFrame lang={lang} src={shots[0].src} alt={t(lang, "reportPublicPhoto")} onOpen={() => setViewing(0)} />
            {shots.length > 1 ? <p className="muted small" style={{ margin: "-4px 0 6px" }}>{t(lang, "casePhotosCount").replace("{n}", String(shots.length))}</p> : null}
            <Thumbs lang={lang} shots={shots} onOpen={setViewing} />
          </>
        ) : null}
        {c.public_summary?.[lang] ?? c.public_summary?.en ? <p>{c.public_summary?.[lang] ?? c.public_summary?.en}</p> : null}
        <div className="stats">
          <div><b>{c.report_count}</b><span>{t(lang, "panelReports")}</span></div>
          <div><b>{c.distinct_devices}</b><span>{t(lang, "caseNeighbours")}</span></div>
          <div><b>{days === null ? "–" : `${days} ${t(lang, "daysShort")}`}</b><span>{t(lang, c.resolved_at ? "caseDaysToFix" : "caseDaysOpen")}</span></div>
        </div>
        {c.status !== "resolved" ? (
          <Link href="/report" className="secondary block">{t(lang, "caseMeToo")}</Link>
        ) : null}
      </section>
      {viewing !== null ? <Lightbox lang={lang} shots={shots} start={viewing} onClose={() => setViewing(null)} /> : null}
    </>
  );
}

export function HomeMap({ lang, clusters, photoBase }: { lang: Lang; clusters: PublicCluster[]; photoBase: string | null }) {
  const [category, setCategory] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const categories = useMemo(() => {
    const seen = new Map<string, string>();
    for (const c of clusters) if (!seen.has(c.category_id)) seen.set(c.category_id, c.category_name?.[lang] ?? c.category_id);
    return [...seen];
  }, [clusters, lang]);
  const shown = category ? clusters.filter((c) => c.category_id === category) : clusters;
  const current = clusters.find((c) => c.id === selected) ?? null;

  return (
    <>
      {categories.length > 1 ? (
        <div className="chips" role="group" aria-label={t(lang, "mapFilter")}>
          <button aria-pressed={category === null} onClick={() => setCategory(null)}>{t(lang, "mapAll")}</button>
          {categories.map(([id, name]) => (
            <button key={id} aria-pressed={category === id} onClick={() => setCategory(id)}>{name}</button>
          ))}
        </div>
      ) : null}
      <div className="map-shell">
        <ClusterMap lang={lang} clusters={clusters} category={category} selected={selected} onSelect={setSelected} />
        {clusters.length === 0 ? <div className="map-empty">{t(lang, "mapEmpty")}</div> : null}
        <div className="legend" aria-label={t(lang, "mapLegend")}>
          {Object.entries(STATUS_COLOR).map(([status, color]) => (
            <span key={status}><i style={{ background: color }} /> {TIMELINE.find((s) => s.id === status)?.[lang]}</span>
          ))}
        </div>
        {current ? <CaseSheet lang={lang} c={current} photoBase={photoBase} onClose={() => setSelected(null)} /> : null}
      </div>

      {shown.length ? (
        <div className="case-list">
          {shown.map((c) => {
            const photo = photoOf(c, photoBase);
            return (
              <button key={c.id} className="case-card" onClick={() => setSelected(c.id)}>
                {photo ? <img className="thumb" src={photo} alt="" loading="lazy" /> : <span className="thumb" aria-hidden="true" />}
                <span>
                  <b>{c.category_name?.[lang] ?? c.category_id}</b>
                  <StatusPill lang={lang} status={c.status} />
                  <span className="muted small" style={{ display: "block", marginTop: 4 }}>
                    {c.report_count} {t(lang, "panelReports")} · {c.distinct_devices} {t(lang, "caseNeighbours")}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="home-cta fixed">
        <Link href="/report" className="primary"><CameraIcon /> {t(lang, "navReport")}</Link>
        <Link href="/sandbox" className="secondary">{t(lang, "tryIt")}</Link>
      </div>
    </>
  );
}
