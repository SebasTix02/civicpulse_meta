"use client";

import { useEffect, useRef, useState } from "react";
import { t, type Lang } from "@/lib/i18n";
import { CloseIcon } from "./icons";

export type Shot = { src: string; caption?: string };

// Full-screen photo viewer: the whole photo, never cropped. Arrows, swipe and Escape work.
export function Lightbox({ lang, shots, start, onClose }: { lang: Lang; shots: Shot[]; start: number; onClose: () => void }) {
  const [i, setI] = useState(start);
  const touch = useRef<number | null>(null);
  const n = shots.length;
  const go = (d: number) => setI((x) => (x + d + n) % n);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
    };
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose, n]);

  const shot = shots[i];
  if (!shot) return null;
  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={t(lang, "photoViewer")} onClick={onClose}
      onTouchStart={(e) => (touch.current = e.touches[0].clientX)}
      onTouchEnd={(e) => {
        if (touch.current === null) return;
        const dx = e.changedTouches[0].clientX - touch.current;
        touch.current = null;
        if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
      }}>
      <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
        <span>{n > 1 ? `${i + 1} / ${n}` : ""}</span>
        <button className="icon-btn" aria-label={t(lang, "close")} onClick={onClose}><CloseIcon /></button>
      </div>
      <img src={shot.src} alt={shot.caption ?? ""} onClick={(e) => e.stopPropagation()} />
      {shot.caption ? <p className="lightbox-caption">{shot.caption}</p> : null}
      {n > 1 ? (
        <>
          <button className="lightbox-nav prev" aria-label={t(lang, "photoPrev")} onClick={(e) => { e.stopPropagation(); go(-1); }}>‹</button>
          <button className="lightbox-nav next" aria-label={t(lang, "photoNext")} onClick={(e) => { e.stopPropagation(); go(1); }}>›</button>
        </>
      ) : null}
    </div>
  );
}

// A photo shown whole (letterboxed, never cropped) with an expand button that opens the viewer.
export function PhotoFrame({ lang, src, alt, onOpen, className }: { lang: Lang; src: string; alt: string; onOpen: () => void; className?: string }) {
  return (
    <div className={`photo-frame ${className ?? ""}`}>
      <button className="photo-hit" onClick={onOpen} aria-label={t(lang, "photoExpand")}>
        <img src={src} alt={alt} />
      </button>
      <button className="expand" onClick={onOpen} aria-label={t(lang, "photoExpand")}>
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M11 3h4v4M7 15H3v-4M15 3l-5 5M3 15l5-5" />
        </svg>
      </button>
    </div>
  );
}

// A row of small photos; tapping one opens the viewer on it.
export function Thumbs({ lang, shots, onOpen }: { lang: Lang; shots: Shot[]; onOpen: (i: number) => void }) {
  if (shots.length < 2) return null;
  return (
    <div className="thumbs" role="list" aria-label={t(lang, "photoAll")}>
      {shots.map((s, i) => (
        <button key={s.src} role="listitem" onClick={() => onOpen(i)} aria-label={`${t(lang, "photoExpand")} ${i + 1}`}>
          <img src={s.src} alt="" loading="lazy" />
        </button>
      ))}
    </div>
  );
}
