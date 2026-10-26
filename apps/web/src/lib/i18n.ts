// UI strings in Spanish and English. Everything a model or the database sees stays English;
// only what a person reads is translated (plan §3.8).

export type Lang = "es" | "en";
export const LANGS: Lang[] = ["es", "en"];

export function pickLang(value: string | undefined | null): Lang {
  return value === "en" ? "en" : "es";
}

const dict = {
  appTagline: {
    es: "Reporta un problema de tu ciudad con una foto. La IA lo verifica; la ciudad lo cierra.",
    en: "Report a city problem with a photo. AI checks it; the city closes it.",
  },
  navMap: { es: "Mapa", en: "Map" },
  navReport: { es: "Reportar", en: "Report" },
  navSandbox: { es: "Probar (sandbox)", en: "Try it (sandbox)" },
  navPanel: { es: "Panel de actores", en: "Stakeholder panel" },
  mapTitle: { es: "Problemas validados en Ambato", en: "Validated problems in Ambato" },
  mapEmpty: { es: "Todavía no hay problemas validados.", en: "No validated problems yet." },
  mapUnavailable: { es: "El mapa no está disponible ahora.", en: "The map is not available right now." },
  reportTitle: { es: "Nuevo reporte", en: "New report" },
  reportText: { es: "¿Qué está pasando y dónde?", en: "What is happening and where?" },
  reportPhoto: { es: "Tomar foto", en: "Take photo" },
  reportSend: { es: "Enviar", en: "Send" },
  reportSending: { es: "Enviando…", en: "Sending…" },
  reportReceived: { es: "Recibido. Te avisamos cuando la IA lo revise.", en: "Received. We will let you know when the AI has checked it." },
  reportNeedsGps: { es: "Necesitamos tu ubicación para ubicar el problema.", en: "We need your location to place the problem." },
  sandboxTitle: { es: "Modo sandbox", en: "Sandbox mode" },
  sandboxBody: {
    es: "Prueba el ciclo completo desde cualquier lugar, sin cuenta. Tus datos quedan aislados y se borran cada noche.",
    en: "Try the full cycle from anywhere, no account. Your data stays isolated and is wiped every night.",
  },
  panelTitle: { es: "Panel de actores", en: "Stakeholder panel" },
  panelBody: { es: "Acceso solo por invitación.", en: "Invite-only access." },
  comingSoon: { es: "En construcción durante el hackathon.", en: "Being built during the hackathon." },
} satisfies Record<string, Record<Lang, string>>;

export type Key = keyof typeof dict;

export function t(lang: Lang, key: Key): string {
  return dict[key][lang];
}

// Pipeline status shown to citizens: one 8-step timeline (UX flows v0.2).
export const TIMELINE: { id: string; es: string; en: string }[] = [
  { id: "received", es: "Recibido", en: "Received" },
  { id: "spec_ready", es: "Entendido", en: "Understood" },
  { id: "vision_done", es: "Foto revisada", en: "Photo checked" },
  { id: "clustered", es: "En el mapa", en: "On the map" },
  { id: "validated", es: "Validado", en: "Validated" },
  { id: "acknowledged", es: "En atención", en: "Being handled" },
  { id: "resolution_pending", es: "Revisando arreglo", en: "Checking the fix" },
  { id: "resolved", es: "Resuelto", en: "Resolved" },
];
