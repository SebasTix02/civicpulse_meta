"use client";

import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "@/lib/errors";
import { t, type Key, type Lang } from "@/lib/i18n";
import { browserClient } from "@/lib/supabase";
import { ago } from "@/app/(public)/report/ReportStatus";
import { StatusPill } from "@/app/HomeMap";
import { Lightbox } from "@/app/Lightbox";
import { Login, photoUrl } from "../Panel";

const CITY = "ambato";
const REASONS = ["spam", "reused_photo", "personal_data", "inappropriate", "impossible_gps", "duplicate", "other"] as const;
// The flag reason suggests the hide reason.
const FLAG_TO_REASON: Record<string, (typeof REASONS)[number]> = {
  reused_photo: "reused_photo", impossible_gps: "impossible_gps", personal_data: "personal_data",
};

type Access = { moderate: boolean; catalog: boolean; audit: boolean };
type Flag = { id: string; reason: string; evidence: unknown; created_at: string };
type QueueRow = {
  id: string;
  short_ref: string;
  created_at: string;
  category_id: string | null;
  category_name: Partial<Record<Lang, string>> | null;
  summary_en: string | null;
  raw_text_redacted: string | null;
  severity: number | null;
  intake_status: string | null;
  moderation_status: "visible" | "hidden";
  moderation_reason: string | null;
  moderated_at: string | null;
  cluster_id: string | null;
  cluster_status: string | null;
  cluster_reports: number | null;
  device_trust: number;
  device_reports_24h: number;
  photo_public_path: string | null;
  flags: Flag[];
};
type CatRow = {
  id: string;
  family: string;
  status: "active" | "draft" | "retired";
  display_name: Partial<Record<Lang, string>>;
  domains: string[];
  reports_30d: number;
  open_cases: number;
};
type HistRow = { at: string; action: string; entity_id: string; after: { reason?: string; note?: string } | null; actor: string | null };
type Tab = "flagged" | "hidden" | "recent" | "catalog" | "history";

const TABS: { id: Tab; key: Key; needs: keyof Access | "any" }[] = [
  { id: "flagged", key: "admTabFlagged", needs: "moderate" },
  { id: "hidden", key: "admTabHidden", needs: "moderate" },
  { id: "recent", key: "admTabRecent", needs: "moderate" },
  { id: "catalog", key: "admTabCatalog", needs: "any" },
  { id: "history", key: "admTabHistory", needs: "any" },
];
const HINT: Partial<Record<Tab, Key>> = {
  flagged: "admHintFlagged", hidden: "admHintHidden", recent: "admHintRecent", catalog: "admHintCatalog",
};

function nm(lang: Lang, n: Partial<Record<Lang, string>> | null | undefined, fallback = "") {
  return n?.[lang] ?? n?.es ?? n?.en ?? fallback;
}

// M1 (queue) and M3 (catalog) of the UX flows, the minimal version: flagged, hidden and recent
// reports with hide / keep / restore, the catalog's status, and the history of these actions.
export function Admin({ lang }: { lang: Lang }) {
  const [phase, setPhase] = useState<"loading" | "login" | "none" | "ready">("loading");
  const [access, setAccess] = useState<Access>({ moderate: false, catalog: false, audit: false });
  const [email, setEmail] = useState("");
  const [tab, setTab] = useState<Tab>("flagged");
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [cats, setCats] = useState<CatRow[]>([]);
  const [hist, setHist] = useState<HistRow[]>([]);
  const [counts, setCounts] = useState<Partial<Record<Tab, number>>>({});
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const load = useCallback(async (which: Tab) => {
    const sb = browserClient();
    setError("");
    if (which === "catalog") {
      const { data, error: e } = await sb.rpc("admin_catalog", { p_city: CITY });
      if (e) return setError(errorMessage(lang, e));
      setCats((data as CatRow[]) ?? []);
    } else if (which === "history") {
      const { data, error: e } = await sb.rpc("admin_history", { p_city: CITY });
      if (e) return setError(errorMessage(lang, e));
      setHist((data as HistRow[]) ?? []);
    } else {
      const { data, error: e } = await sb.rpc("moderation_queue", { p_city: CITY, p_view: which });
      if (e) return setError(errorMessage(lang, e));
      const rows = (data as QueueRow[]) ?? [];
      setQueue(rows);
      setCounts((c) => ({ ...c, [which]: rows.length }));
    }
  }, [lang]);

  useEffect(() => {
    void (async () => {
      const sb = browserClient();
      const { data: session } = await sb.auth.getSession();
      const user = session.session?.user;
      if (!user || user.is_anonymous) return setPhase("login");
      setEmail(user.email ?? "");
      const { data } = await sb.rpc("admin_access", { p_city: CITY });
      const a = (data as Access) ?? { moderate: false, catalog: false, audit: false };
      setAccess(a);
      if (!a.moderate && !a.catalog) return setPhase("none");
      const first: Tab = a.moderate ? "flagged" : "catalog";
      setTab(first);
      setPhase("ready");
      if (a.moderate) {
        const { data: f } = await sb.rpc("moderation_queue", { p_city: CITY, p_view: "flagged" });
        setCounts({ flagged: ((f as QueueRow[]) ?? []).length });
      }
    })();
  }, []);

  useEffect(() => {
    if (phase === "ready") void load(tab);
  }, [phase, tab, load]);

  async function signOut() {
    await browserClient().auth.signOut();
    setPhase("login");
  }

  if (phase === "loading") return <p className="muted">…</p>;
  if (phase === "login") return <Login lang={lang} />;
  if (phase === "none") {
    return (
      <div className="card login">
        <p className="notice warn">{t(lang, "admNoAccess")}</p>
        <button className="secondary" onClick={signOut}>{t(lang, "panelSignOut")}</button>
      </div>
    );
  }

  const tabs = TABS.filter((x) => x.needs === "any" || access[x.needs]);
  const hint = HINT[tab];
  return (
    <>
      <div className="panel-bar">
        <h1>{t(lang, "admTitle")}</h1>
        <span className="muted small">Ambato</span>
        <span className="spacer" />
        <span className="who">{email}</span>
        <a className="link" href="/panel">{t(lang, "admBack")}</a>
        <button className="link" onClick={() => void load(tab)}>{t(lang, "panelRefresh")}</button>
        <button className="link" onClick={signOut}>{t(lang, "panelSignOut")}</button>
      </div>
      <div className="tabs" role="tablist" aria-label={t(lang, "admTitle")}>
        {tabs.map((x) => (
          <button key={x.id} role="tab" aria-selected={x.id === tab} className="tab"
            onClick={() => { setDone(""); setTab(x.id); }}>
            {t(lang, x.key)}{counts[x.id] ? ` · ${counts[x.id]}` : ""}
          </button>
        ))}
      </div>
      {hint ? <p className="muted small">{t(lang, hint)}</p> : null}
      {done ? <p className="notice ok" role="status">{done}</p> : null}
      {error ? <p className="notice warn" role="alert">{error}</p> : null}

      {tab === "catalog" ? (
        <Catalog lang={lang} rows={cats} canEdit={access.catalog} onChanged={() => void load("catalog")} onError={setError} />
      ) : tab === "history" ? (
        <History lang={lang} rows={hist} />
      ) : queue.length === 0 ? (
        <p className="card muted">{t(lang, "admEmpty")}</p>
      ) : (
        <div className="adm-queue">
          {queue.map((r) => (
            <QueueCard key={r.id} lang={lang} row={r} onError={setError}
              onDone={(msg) => { setDone(msg); void load(tab); }} />
          ))}
        </div>
      )}
    </>
  );
}

function QueueCard({ lang, row, onDone, onError }: {
  lang: Lang; row: QueueRow; onDone: (msg: string) => void; onError: (msg: string) => void;
}) {
  const [hiding, setHiding] = useState(false);
  const [reason, setReason] = useState<string>(FLAG_TO_REASON[row.flags[0]?.reason ?? ""] ?? "spam");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [zoom, setZoom] = useState(false);
  const photo = photoUrl(row.photo_public_path);
  const text = row.raw_text_redacted || row.summary_en;

  async function act(action: "hide" | "keep" | "restore") {
    setBusy(true);
    const { data, error } = await browserClient().rpc("moderate_report", {
      p_report_id: row.id, p_action: action, p_reason: action === "hide" ? reason : null, p_note: note || null,
    });
    setBusy(false);
    if (error) return onError(errorMessage(lang, error));
    const out = data as { cluster_visible?: boolean | null };
    onDone(t(lang, `admDone_${action}` as Key) + (action === "hide" && out?.cluster_visible === false ? t(lang, "admCaseHidden") : ""));
  }

  return (
    <article className="card adm-item">
      <div className="adm-head">
        {photo ? (
          <button className="thumb-btn" onClick={() => setZoom(true)} aria-label={t(lang, "photoExpand")}>
            <img className="thumb" src={photo} alt="" />
          </button>
        ) : <span className="thumb" aria-hidden="true" />}
        <div className="adm-body">
          <div className="adm-title">
            <b>{nm(lang, row.category_name, row.category_id ?? "?")}</b>
            <span className="muted small">{row.short_ref} · {ago(lang, row.created_at)}</span>
          </div>
          <p className="adm-text">{text || <span className="muted">{t(lang, "admNoText")}</span>}</p>
          <div className="adm-pills">
            {row.flags.map((f) => <span key={f.id} className="pill flag">{t(lang, `admFlag_${f.reason}` as Key)}</span>)}
            {row.moderation_status === "hidden" && row.moderation_reason ? (
              <span className="pill outline">{t(lang, "admHiddenBecause").replace("{r}", t(lang, `admReason_${row.moderation_reason}` as Key))}</span>
            ) : null}
            {row.cluster_status ? <StatusPill lang={lang} status={row.cluster_status} /> : null}
          </div>
          <p className="muted small">
            {t(lang, "admDevice").replace("{t}", row.device_trust.toFixed(2)).replace("{n}", String(row.device_reports_24h))}
            {row.cluster_id ? ` · ${t(lang, "admCase").replace("{s}", row.cluster_id.slice(0, 8)).replace("{n}", String(row.cluster_reports ?? 0))}` : ""}
          </p>
        </div>
      </div>

      {hiding ? (
        <div className="adm-hide">
          <label className="field">
            <span className="label">{t(lang, "admReason")}</span>
            <select value={reason} onChange={(e) => setReason(e.target.value)}>
              {REASONS.map((x) => <option key={x} value={x}>{t(lang, `admReason_${x}` as Key)}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="label">{t(lang, "admNote")}</span>
            <input type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="row">
            <button className="danger" disabled={busy} onClick={() => void act("hide")}>{t(lang, "admConfirmHide")}</button>
            <button className="secondary" disabled={busy} onClick={() => setHiding(false)}>{t(lang, "admCancel")}</button>
          </div>
        </div>
      ) : (
        <div className="row adm-actions">
          {row.moderation_status === "hidden" ? (
            <button className="secondary" disabled={busy} onClick={() => void act("restore")}>{t(lang, "admRestore")}</button>
          ) : (
            <>
              <button className="danger" disabled={busy} onClick={() => setHiding(true)}>{t(lang, "admHide")}</button>
              {row.flags.length ? (
                <button className="secondary" disabled={busy} onClick={() => void act("keep")}>{t(lang, "admKeep")}</button>
              ) : null}
            </>
          )}
        </div>
      )}
      {zoom && photo ? <Lightbox lang={lang} shots={[{ src: photo }]} start={0} onClose={() => setZoom(false)} /> : null}
    </article>
  );
}

function Catalog({ lang, rows, canEdit, onChanged, onError }: {
  lang: Lang; rows: CatRow[]; canEdit: boolean; onChanged: () => void; onError: (msg: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  async function setStatus(c: CatRow, status: "active" | "retired") {
    if (status === "retired" && !window.confirm(t(lang, "admConfirmRetire").replace("{c}", nm(lang, c.display_name, c.id)))) return;
    setBusy(c.id);
    const { error } = await browserClient().rpc("set_category_status", { p_category: c.id, p_status: status });
    setBusy(null);
    if (error) return onError(errorMessage(lang, error));
    onChanged();
  }

  return (
    <>
      {!canEdit ? <p className="muted small">{t(lang, "admReadOnly")}</p> : null}
      <div className="table-wrap">
        <table className="adm-table">
          <thead>
            <tr>
              <th>{t(lang, "admCatName")}</th>
              <th>{t(lang, "admCatDomains")}</th>
              <th>{t(lang, "admCatUse")}</th>
              <th>{t(lang, "admCatState")}</th>
              {canEdit ? <th aria-label="actions" /> : null}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className={c.status === "retired" ? "retired" : undefined}>
                <td><b>{nm(lang, c.display_name, c.id)}</b><div className="muted small">{c.id}</div></td>
                <td className="small">{c.domains.join(", ")}</td>
                <td className="small">{t(lang, "admCatUseValue").replace("{r}", String(c.reports_30d)).replace("{c}", String(c.open_cases))}</td>
                <td><span className={`pill ${c.status === "active" ? "soft" : "outline"}`}>{t(lang, `admStatus_${c.status}` as Key)}</span></td>
                {canEdit ? (
                  <td>
                    {c.id === "other" ? null : c.status === "active" ? (
                      <button className="link" disabled={busy === c.id} onClick={() => void setStatus(c, "retired")}>{t(lang, "admRetire")}</button>
                    ) : (
                      <button className="link" disabled={busy === c.id} onClick={() => void setStatus(c, "active")}>{t(lang, "admActivate")}</button>
                    )}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function History({ lang, rows }: { lang: Lang; rows: HistRow[] }) {
  if (!rows.length) return <p className="card muted">{t(lang, "admEmpty")}</p>;
  return (
    <ol className="adm-history card">
      {rows.map((h, i) => (
        <li key={`${h.at}-${i}`}>
          <span className="muted small">{ago(lang, h.at)}</span>{" "}
          <b>{h.actor ?? "?"}</b> {t(lang, `admAction_${h.action.replace(".", "_")}` as Key)}{" "}
          <code>{h.action.startsWith("report.") ? `r_${h.entity_id.slice(0, 8)}` : h.entity_id}</code>
          {h.after?.reason ? ` · ${t(lang, `admReason_${h.after.reason}` as Key)}` : ""}
          {h.after?.note ? <span className="muted"> · “{h.after.note}”</span> : null}
        </li>
      ))}
    </ol>
  );
}
