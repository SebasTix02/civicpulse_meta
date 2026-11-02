// Voice report clip -> text (orchestrator POST /transcribe, Muse Voice Transcribe). This route only
// forwards the clip and the citizen's own session token, so the orchestrator URL stays server-side
// and the browser needs no CORS. Nothing is stored here or there.
export const dynamic = "force-dynamic";

const MAX_BYTES = 2_500_000;

export async function POST(req: Request) {
  const base = process.env.ORCHESTRATOR_URL?.trim().replace(/\/+$/, "");
  if (!base) return Response.json({ error: "voice_not_configured" }, { status: 503 });
  const auth = req.headers.get("authorization");
  if (!auth?.startsWith("Bearer ")) return Response.json({ error: "not_signed_in" }, { status: 401 });
  const body = await req.arrayBuffer();
  if (body.byteLength > MAX_BYTES) return Response.json({ error: "voice_too_long" }, { status: 413 });
  try {
    const res = await fetch(`${base}/transcribe`, {
      method: "POST",
      headers: { Authorization: auth, "Content-Type": "audio/wav" },
      body,
      signal: AbortSignal.timeout(55_000),
    });
    const data = await res.json().catch(() => ({ error: "voice_unavailable" }));
    return Response.json(data, { status: res.status });
  } catch {
    return Response.json({ error: "voice_unavailable" }, { status: 502 });
  }
}
