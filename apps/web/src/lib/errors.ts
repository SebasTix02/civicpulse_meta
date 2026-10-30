// Turns whatever a failed call throws into one sentence a person can act on.
// The database raises snake_case codes (raise exception 'rate_limited' ...); PostgREST hands them back
// as plain objects { code, message, details, hint }, not Error instances, so String(e) gave "[object Object]".
import type { Lang } from "./i18n";

type Msg = { es: string; en: string };

const CODES: Record<string, Msg> = {
  actors_cannot_capture: {
    es: "Esta sesión es de una cuenta de entidad. Los reportes ciudadanos se envían sin iniciar sesión.",
    en: "This session belongs to an entity account. Citizen reports are sent without signing in.",
  },
  not_a_citizen_session: {
    es: "Esta sesión no es de ciudadano. Cierra sesión e inténtalo otra vez.",
    en: "This is not a citizen session. Sign out and try again.",
  },
  not_authenticated: { es: "Tu sesión expiró. Recarga la página e inténtalo otra vez.", en: "Your session expired. Reload the page and try again." },
  forbidden: { es: "Tu cuenta no tiene permiso para hacer esto.", en: "Your account is not allowed to do this." },
  rate_limited: {
    es: "Enviaste varios reportes seguidos. Espera unos minutos antes de enviar otro.",
    en: "You sent several reports in a row. Wait a few minutes before sending another.",
  },
  device_not_found: { es: "No reconocimos este dispositivo. Recarga la página e inténtalo otra vez.", en: "We did not recognise this device. Reload the page and try again." },
  gps_required: { es: "Necesitamos tu ubicación para ubicar el problema.", en: "We need your location to place the problem." },
  gps_accuracy_required: { es: "No llegó la precisión del GPS. Inténtalo otra vez al aire libre.", en: "The GPS accuracy did not arrive. Try again outdoors." },
  gps_accuracy_too_low: {
    es: "Tu ubicación es demasiado imprecisa. Sal al aire libre, espera unos segundos y vuelve a intentarlo.",
    en: "Your location is too imprecise. Go outdoors, wait a few seconds and try again.",
  },
  outside_service_area: { es: "Ese lugar está fuera de la zona que cubre el servicio.", en: "That place is outside the area the service covers." },
  pin_too_far: { es: "El punto está demasiado lejos de donde estás. Acércalo a tu ubicación.", en: "The pin is too far from where you are. Move it closer to your location." },
  photo_required: { es: "Falta la foto.", en: "The photo is missing." },
  photo_not_found: { es: "La foto no terminó de subirse. Inténtalo otra vez.", en: "The photo did not finish uploading. Try again." },
  photo_path_mismatch: { es: "La foto no coincide con este envío. Inténtalo otra vez.", en: "The photo does not match this submission. Try again." },
  bad_phash: { es: "No pudimos leer la foto. Toma otra.", en: "We could not read the photo. Take another one." },
  invalid_nonce: { es: "El permiso de subida no es válido. Inténtalo otra vez.", en: "The upload permit is not valid. Try again." },
  nonce_expired: { es: "Pasó demasiado tiempo desde que tomaste la foto. Inténtalo otra vez.", en: "Too much time passed since you took the photo. Try again." },
  nonce_used: { es: "Esa foto ya se envió.", en: "That photo was already sent." },
  report_id_conflict: { es: "Este reporte ya se había enviado.", en: "This report was already sent." },
  report_not_found: { es: "No encontramos ese reporte.", en: "We could not find that report." },
  report_without_category: { es: "Ese reporte todavía no tiene categoría.", en: "That report has no category yet." },
  unknown_category: { es: "Categoría desconocida.", en: "Unknown category." },
  invalid_purpose: { es: "Solicitud no válida.", en: "Invalid request." },
  dispatch_not_found: { es: "Este caso ya no está asignado a tu entidad.", en: "This case is no longer assigned to your entity." },
  dispatch_not_accepted: { es: "Primero acepta el caso.", en: "Accept the case first." },
  invalid_transition: { es: "El caso cambió mientras lo veías. Recarga la bandeja.", en: "The case changed while you were looking at it. Reload the inbox." },
  already_decided: { es: "Alguien ya decidió esto. Recarga la bandeja.", en: "Someone already decided this. Reload the inbox." },
  cluster_not_awaiting_fix: { es: "Este caso no está esperando una foto de arreglo.", en: "This case is not waiting for a repair photo." },
  resolution_already_open: { es: "Ya hay una foto de arreglo en revisión para este caso.", en: "A repair photo is already under review for this case." },
  resolution_not_found: { es: "No encontramos esa foto de arreglo.", en: "We could not find that repair photo." },
  ai_pending: { es: "La IA todavía está revisando la foto. Espera un momento.", en: "The AI is still checking the photo. Wait a moment." },
  note_required: { es: "Escribe una nota explicando tu decisión.", en: "Write a note explaining your decision." },
  invalid_verdict: { es: "Decisión no válida.", en: "Invalid decision." },
  samples_not_available: { es: "Las fotos de ejemplo no están disponibles.", en: "The sample photos are not available." },
  sandbox_quota_exceeded: { es: "El sandbox llegó a su límite de hoy. Prueba la demo grabada.", en: "The sandbox reached today's limit. Try the recorded demo." },
  sandbox_session_not_found: { es: "Tu sesión de sandbox expiró. Empieza una nueva.", en: "Your sandbox session expired. Start a new one." },
  simulation_too_large: { es: "La simulación es demasiado grande.", en: "The simulation is too large." },
};

const NETWORK: Msg = { es: "No hay conexión. Revisa tu internet e inténtalo otra vez.", en: "No connection. Check your internet and try again." };
const GENERIC: Msg = { es: "Algo salió mal. Inténtalo otra vez en un momento.", en: "Something went wrong. Try again in a moment." };

function rawMessage(err: unknown): string {
  if (typeof err === "string") return err;
  if (err && typeof err === "object" && "message" in err && typeof err.message === "string") return err.message;
  return "";
}

/** The code the database raised, if any (also matches storage policy denials). */
export function errorCode(err: unknown): string | null {
  const m = rawMessage(err).trim();
  if (m in CODES) return m;
  return null;
}

export function errorMessage(lang: Lang, err: unknown): string {
  const code = errorCode(err);
  if (code) return CODES[code][lang];
  const m = rawMessage(err);
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(m)) return NETWORK[lang];
  if (/row-level security|unauthorized|jwt/i.test(m)) return CODES.forbidden[lang];
  // A message we wrote ourselves (new Error(t(...))) is already readable; anything else stays generic.
  if (err instanceof Error && m && !/^[a-z_]+$/.test(m) && !/^[A-Z0-9]{5}$/.test(m)) return m;
  if (m) console.warn("unmapped error", err);
  return GENERIC[lang];
}
