import { Fragment } from "react";
import type { Lang } from "@/lib/i18n";

export type Citation = { id: string; kind: "report" | "geo_feature" | "cluster" | "metric"; ref: string };
export type BriefVersion = { title: string; body_markdown: string; actions: Brief["actions"] };
export type Brief = {
  lang: Lang;
  translations?: Partial<Record<Lang, BriefVersion>>;
  title: string;
  body_markdown: string;
  actions: { action_id: string; rationale: string; citations: string[] }[];
  citations: Citation[];
  model: { provider: string | null; model: string | null };
};

// The brief in the reader's language when its translation exists (same citations, checked by
// the orchestrator), otherwise as written.
export function inLang(brief: Brief, lang: Lang): BriefVersion {
  return (brief.lang !== lang && brief.translations?.[lang]) || brief;
}

// The brief's markdown is plain text plus citations: paragraphs, "- " lists and **bold**. It is
// rendered as React text (never as HTML), with every [id] turned into a chip.
export function BriefText({ text, cites, inline: oneLine = false }: { text: string; cites: Map<string, Citation>; inline?: boolean }) {
  const inline = (line: string) =>
    line.split(/(\[[^\]]+\]|\*\*[^*]+\*\*)/g).map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) return <b key={i}>{part.slice(2, -2)}</b>;
      if (!part.startsWith("[")) return <Fragment key={i}>{part}</Fragment>;
      const ids = part.slice(1, -1).split(/[,;\s]+/).filter(Boolean);
      return ids.map((id) => {
        const c = cites.get(id);
        return c?.kind === "report" ? (
          <a key={`${i}-${id}`} className="cite" href={`#${id}`}>{id}</a>
        ) : (
          <span key={`${i}-${id}`} className="cite" title={c?.ref}>{id}</span>
        );
      });
    });
  if (oneLine) return <>{inline(text.replace(/\s+/g, " "))}</>;
  const blocks = text.split(/\n\s*\n/);
  return (
    <>
      {blocks.map((block, b) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length && lines.every((l) => /^\s*[-*] /.test(l))) {
          return <ul key={b}>{lines.map((l, i) => <li key={i}>{inline(l.replace(/^\s*[-*] /, ""))}</li>)}</ul>;
        }
        return <p key={b}>{inline(lines.join(" "))}</p>;
      })}
    </>
  );
}

