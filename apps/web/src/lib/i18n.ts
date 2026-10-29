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
  reportCheckFailed: {
    es: "No pudimos revisar la foto ahora. Tu reporte quedó guardado y sigue contando.",
    en: "We could not check the photo right now. Your report is saved and still counts.",
  },
  reportStillChecking: {
    es: "La revisión está tardando más de lo normal. Puedes cerrar esta página: tu reporte ya está guardado.",
    en: "The check is taking longer than usual. You can close this page: your report is already saved.",
  },
  reportPublicPhoto: {
    es: "Así se verá tu foto en público: caras, personas y placas difuminadas.",
    en: "This is how your photo looks in public: faces, people and plates blurred.",
  },
  photoDark: { es: "La foto salió muy oscura.", en: "The photo came out too dark." },
  photoBright: { es: "La foto salió con demasiada luz.", en: "The photo came out too bright." },
  photoBlurry: { es: "La foto salió borrosa.", en: "The photo came out blurry." },
  photoRetakeHint: {
    es: "Con una foto clara la IA puede confirmar el problema. ¿La repites?",
    en: "With a clear photo the AI can confirm the problem. Take it again?",
  },
  photoRetake: { es: "Repetir foto", en: "Take it again" },
  photoSendAnyway: { es: "Enviar igual", en: "Send anyway" },
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

// What the photo check means for the citizen (intake_status). A photo never rejects a report.
const INTAKE: Record<string, Record<Lang, string>> = {
  REPORT_CONFIRMED_BY_PHOTO: { es: "La foto muestra el problema.", en: "The photo shows the problem." },
  ISSUE_VISIBLE_NOT_LOCALIZED_REQUIRES_REVIEW: {
    es: "Se ve el problema en la foto; una persona confirmará dónde está.",
    en: "The problem is visible; a person will confirm where it is.",
  },
  CANDIDATE_REGIONS_FOUND_REQUIRES_REVIEW: {
    es: "Encontramos posibles zonas del problema; una persona lo revisará.",
    en: "We found possible areas of the problem; a person will review it.",
  },
  NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO: {
    es: "No logramos verlo en esta foto. Tu reporte sigue contando; una foto más cercana ayuda.",
    en: "We could not see it in this photo. Your report still counts; a closer photo helps.",
  },
  NOT_VERIFIABLE_NO_VISUAL_SPEC: {
    es: "Este problema se confirma con más reportes de la zona, no con la foto.",
    en: "This problem is confirmed by more reports in the area, not by the photo.",
  },
  NOT_PHOTO_VERIFIABLE_CATEGORY: {
    es: "Este problema se confirma con más reportes de la zona, no con la foto.",
    en: "This problem is confirmed by more reports in the area, not by the photo.",
  },
};

export function intakeMessage(lang: Lang, status: string): string {
  return INTAKE[status]?.[lang] ?? INTAKE.NOT_VERIFIABLE_ISSUE_NOT_FOUND_IN_PHOTO[lang];
}
