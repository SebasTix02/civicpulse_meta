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
  navDemo: { es: "Demo grabada", en: "Recorded demo" },
  replayTitle: { es: "Demo grabada", en: "Recorded demo" },
  replayIntro: {
    es: "Corridas reales del sistema, reproducidas paso a paso con sus fotos anonimizadas y sus resultados tal como salieron.",
    en: "Real runs of the system, played back step by step with their anonymized photos and their results as they came out.",
  },
  replayBanner: {
    es: "Estás viendo la reproducción de una corrida real del {date}. Nada de esto ocurre en vivo.",
    en: "You are watching a replay of a real run from {date}. Nothing here is live.",
  },
  replaySandboxNote: {
    es: "Corrió en el sandbox: los reportes marcados como simulados vienen del simulador.",
    en: "It ran in the sandbox: reports marked simulated come from the simulator.",
  },
  replayNone: { es: "Todavía no hay corridas grabadas.", en: "No runs recorded yet." },
  replayScenario: { es: "Escenario", en: "Scenario" },
  replayPlay: { es: "Reproducir", en: "Play" },
  replayPause: { es: "Pausa", en: "Pause" },
  replayStep: { es: "Paso siguiente", en: "Next step" },
  replayAgain: { es: "Ver de nuevo", en: "Play again" },
  replayDevice: { es: "dispositivo", en: "device" },
  replaySimulated: { es: "simulado", en: "simulated" },
  replayHidden: { es: "Filtrado por moderación", en: "Filtered by moderation" },
  replayChecking: { es: "Revisando la foto…", en: "Checking the photo…" },
  replayBriefFor: { es: "Brief para", en: "Brief for" },
  replayLog: { es: "Qué pasó (tiempos reales)", en: "What happened (real times)" },
  replayLogReport: { es: "Llega el reporte", en: "Report arrives" },
  replayLogCluster: { es: "Caso", en: "Case" },
  replayLogBrief: { es: "Brief escrito para", en: "Brief written for" },
  replayLogResSent: { es: "El actor sube la foto del arreglo", en: "The stakeholder uploads the photo of the fix" },
  replayLogResChecked: { es: "La IA compara antes y después", en: "The AI compares before and after" },
  replayLogApproved: { es: "Una persona aprueba el cierre", en: "A person approves the closure" },
  replayLogRejected: { es: "Una persona rechaza la foto", en: "A person rejects the photo" },
  replayStage_geofence: { es: "Ubicación verificada", en: "Location checked" },
  replayStage_phash: { es: "Foto repetida revisada", en: "Reused photo checked" },
  replayStage_spec: { es: "Texto entendido (Muse)", en: "Text understood (Muse)" },
  replayStage_sam_ground: { es: "SAM 3.1 segmenta la foto", en: "SAM 3.1 segments the photo" },
  replayStage_classify: { es: "Muse revisa los recortes", en: "Muse checks the crops" },
  replayStage_cluster: { es: "Agrupado en el mapa", en: "Grouped on the map" },
  replayStage_brief: { es: "Brief", en: "Brief" },
  replayStage_closure: { es: "Verificación del cierre", en: "Closure check" },
  demoLevel_1: {
    es: "La IA de visión no responde: los resultados quedan en revisión humana.",
    en: "The vision AI is not answering: results wait for human review.",
  },
  demoLevel_2: {
    es: "SAM en vivo está en pausa: las fotos nuevas esperan en cola.",
    en: "Live SAM is paused: new photos wait in a queue.",
  },
  demoLevel_3: {
    es: "El servicio de IA en vivo está en pausa. Puedes ver una corrida real grabada.",
    en: "The live AI service is paused. You can watch a recorded real run.",
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
  reportWaitingConsensus: {
    es: "Tu reporte ya está en un caso. El caso se valida y se envía a la entidad responsable cuando otros vecinos reportan lo mismo.",
    en: "Your report is now part of a case. The case is validated and sent to the responsible entity once other neighbours report the same thing.",
  },
  myReportsTitle: { es: "Mis reportes", en: "My reports" },
  myReportsHint: {
    es: "Se guardan en este navegador. Si borras sus datos o cambias de teléfono, ya no los verás aquí.",
    en: "Saved in this browser. If you clear its data or change phones, they will no longer show here.",
  },
  myReportsNew: { es: "Hacer otro reporte", en: "Make another report" },
  myReportsChanged: { es: "Novedad", en: "Updated" },
  myReportsNoText: { es: "(sin texto)", en: "(no text)" },
  reportActorSession: {
    es: "Tienes abierta una cuenta de entidad en este navegador.",
    en: "An entity account is signed in on this browser.",
  },
  reportActorHint: {
    es: "Las entidades no envían reportes ciudadanos. Cierra sesión para reportar como ciudadano (anónimo).",
    en: "Entities do not send citizen reports. Sign out to report as a citizen (anonymous).",
  },
  reportActorSignOut: { es: "Cerrar sesión y reportar", en: "Sign out and report" },
  reportNeedsGps: { es: "Necesitamos tu ubicación para ubicar el problema.", en: "We need your location to place the problem." },
  sandboxTitle: { es: "Modo sandbox", en: "Sandbox mode" },
  sandboxBody: {
    es: "Prueba el ciclo completo desde cualquier lugar, sin cuenta. Tus datos quedan aislados y se borran cada noche.",
    en: "Try the full cycle from anywhere, no account. Your data stays isolated and is wiped every night.",
  },
  panelTitle: { es: "Panel de actores", en: "Stakeholder panel" },
  panelBody: { es: "Acceso solo por invitación.", en: "Invite-only access." },
  comingSoon: { es: "En construcción durante el hackathon.", en: "Being built during the hackathon." },
  panelEmail: { es: "Correo de tu invitación", en: "Email of your invitation" },
  panelSendLink: { es: "Enviarme el enlace de acceso", en: "Send me the sign-in link" },
  panelLinkSent: {
    es: "Te enviamos un enlace de acceso. Ábrelo en este mismo navegador.",
    en: "We sent you a sign-in link. Open it in this same browser.",
  },
  panelNotInvited: {
    es: "Este correo no tiene invitación. Pide acceso al administrador.",
    en: "This email has no invitation. Ask the administrator for access.",
  },
  panelNoRole: {
    es: "Tu cuenta todavía no tiene un actor asignado.",
    en: "Your account has no stakeholder assigned yet.",
  },
  panelSignOut: { es: "Salir", en: "Sign out" },
  panelRefresh: { es: "Actualizar", en: "Refresh" },
  panelTabNew: { es: "Nuevos", en: "New" },
  panelTabAccepted: { es: "En atención", en: "In progress" },
  panelTabDone: { es: "Cerrados", en: "Closed" },
  panelEmpty: { es: "No hay casos aquí.", en: "No cases here." },
  panelReports: { es: "reportes", en: "reports" },
  panelDevices: { es: "dispositivos", en: "devices" },
  panelSandbox: { es: "Sandbox", en: "Sandbox" },
  panelBack: { es: "← Volver a la bandeja", en: "← Back to inbox" },
  panelActions: { es: "Acciones sugeridas", en: "Suggested actions" },
  panelEvidence: { es: "Reportes del caso", en: "Reports in this case" },
  panelOriginal: { es: "Ver texto original (datos personales ocultos)", en: "Show original text (personal data masked)" },
  panelSummary: { es: "Ver resumen", en: "Show summary" },
  panelAccept: { es: "Aceptar el caso", en: "Accept the case" },
  panelDecline: { es: "Rechazar", en: "Decline" },
  panelClose: { es: "Cerrar sin arreglo", en: "Close without a fix" },
  panelNote: { es: "Motivo (obligatorio)", en: "Reason (required)" },
  panelConfirm: { es: "Confirmar", en: "Confirm" },
  panelOnMap: { es: "Ver en el mapa", en: "See on the map" },
  panelBriefTemplate: {
    es: "Resumen automático: la IA no estaba disponible.",
    en: "Automatic summary: the AI was not available.",
  },
  panelBriefBy: { es: "Redactado por", en: "Written by" },
  panelPhotoOk: { es: "✓ foto verificada", en: "✓ photo verified" },
  panelStatus_sent: { es: "Nuevo", en: "New" },
  panelStatus_seen: { es: "Visto", en: "Seen" },
  panelStatus_accepted: { es: "Aceptado", en: "Accepted" },
  panelStatus_declined: { es: "Rechazado", en: "Declined" },
  panelStatus_closed: { es: "Cerrado", en: "Closed" },
  resTitle: { es: "Cierre con foto", en: "Closing with a photo" },
  resIntro: {
    es: "Cuando el arreglo esté hecho, sube una foto desde el mismo punto y ángulo que los reportes. La IA compara ambas fotos; la decisión final es de una persona.",
    en: "Once the fix is done, upload a photo from the same spot and angle as the reports. The AI compares both photos; a person makes the final call.",
  },
  resFrameLikeThis: {
    es: "Encuadra igual que esta foto del reporte: mismo punto, misma altura, mismo ángulo.",
    en: "Frame it like this report photo: same spot, same height, same angle.",
  },
  resGpsHint: {
    es: "Envíala desde el teléfono, en el lugar y con la ubicación activada: si el ángulo no coincide, la IA usa el GPS para comparar.",
    en: "Send it from your phone, on site, with location on: if the angle does not match, the AI uses GPS to compare.",
  },
  resByLocation: {
    es: "Comparado por ubicación GPS, no por alineación de las fotos: revisa que sea el mismo lugar.",
    en: "Compared by GPS location, not by aligning the photos: check that it is the same place.",
  },
  resPhoto: { es: "Foto después del arreglo", en: "Photo after the fix" },
  resSend: { es: "Enviar para verificación", en: "Send for verification" },
  resSending: { es: "Enviando…", en: "Sending…" },
  resChecking: { es: "La IA está comparando las fotos…", en: "The AI is comparing the photos…" },
  resAi: { es: "Resultado de la IA", en: "AI result" },
  resBefore: { es: "Antes", en: "Before" },
  resAfter: { es: "Después (alineada)", en: "After (aligned)" },
  resAfterRaw: { es: "Foto enviada", en: "Photo sent" },
  resApprove: { es: "Aprobar el cierre", en: "Approve the closure" },
  resReject: { es: "Rechazar la foto", en: "Reject the photo" },
  resNoteOverride: {
    es: "La IA no lo dio por resuelto: explica por qué apruebas igual (obligatorio)",
    en: "The AI did not mark it resolved: explain why you approve anyway (required)",
  },
  resNoteReject: { es: "Motivo (opcional)", en: "Reason (optional)" },
  resWaitReviewer: {
    es: "Un administrador de tu entidad revisará la foto.",
    en: "An administrator of your organization will review the photo.",
  },
  resOutcome_resolved: { es: "✓ Cierre aprobado", en: "✓ Closure approved" },
  resOutcome_rejected: { es: "Foto rechazada: puedes enviar otra.", en: "Photo rejected: you can send another." },
  resOverridden: { es: "aprobado en contra de la IA", en: "approved against the AI" },
  resDisclaimer: {
    es: "Apoyo visual para la inspección, no garantiza la reparación.",
    en: "Visual support for inspection, not a guarantee of repair.",
  },
  ai_pending: { es: "Verificando…", en: "Checking…" },
  ai_PERSISTENT_ISSUE: { es: "El problema sigue visible en la foto.", en: "The problem is still visible in the photo." },
  ai_RESOLVED_CANDIDATE_REQUIRES_HUMAN_REVIEW: {
    es: "Parece arreglado. Revisa las fotos antes de aprobar.",
    en: "Looks fixed. Check the photos before approving.",
  },
  ai_NOT_VERIFIABLE_REGION_NOT_VISIBLE: {
    es: "No se pudo ubicar el mismo lugar: toma la foto desde el punto y ángulo de los reportes.",
    en: "Could not find the same spot: take the photo from the reports' spot and angle.",
  },
  ai_NOT_VERIFIABLE_CONFUSER_OR_OCCLUSION: {
    es: "Algo tapa la zona (sombra, agua, un vehículo).",
    en: "Something covers the area (shadow, water, a vehicle).",
  },
  ai_NOT_VERIFIABLE_REPAIR_VS_CONFUSER_CONFLICT: {
    es: "No se distingue si es un parche o una mancha.",
    en: "Cannot tell a patch from a stain.",
  },
  ai_NOT_VERIFIABLE_CLASSIFIER_UNAVAILABLE: {
    es: "El modelo no respondió; decide con las fotos.",
    en: "The model did not answer; decide from the photos.",
  },
  ai_NOT_VERIFIABLE_INITIAL_ISSUE_NOT_CONFIRMED: {
    es: "La IA no encontró el problema en la foto de antes.",
    en: "The AI did not find the problem in the before photo.",
  },
  ai_NOT_VERIFIABLE_INSUFFICIENT_EVIDENCE: {
    es: "No hay evidencia suficiente para decir que está arreglado.",
    en: "Not enough evidence to say it is fixed.",
  },
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
