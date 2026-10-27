import { createBrowserClient } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";

// Values pasted into a hosting dashboard often carry quotes or spaces; anything that is not an
// http(s) URL counts as "not configured" instead of crashing the build.
function cleanUrl(raw: string | undefined): string | undefined {
  const value = raw?.trim().replace(/^["']|["']$/g, "");
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? value.replace(/\/+$/, "") : undefined;
  } catch {
    return undefined;
  }
}

const url = cleanUrl(process.env.NEXT_PUBLIC_SUPABASE_URL);
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY?.trim().replace(/^["']|["']$/g, "") || undefined;

export const supabaseConfigured = Boolean(url && anonKey);

// Browser: citizens use an anonymous Supabase session (devices.id = auth.uid()).
export function browserClient() {
  if (!url || !anonKey) throw new Error("Supabase env vars are not set");
  return createBrowserClient(url, anonKey);
}

// Server, no session: only public views (public_clusters).
export function publicServerClient() {
  if (!url || !anonKey) return null;
  return createClient(url, anonKey, { auth: { persistSession: false } });
}

export type PublicCluster = {
  id: string;
  city_id: string;
  domain_id: string;
  category_id: string;
  lat: number;
  lon: number;
  report_count: number;
  distinct_devices: number;
  priority_score: number;
  status: string;
  public_summary: { en?: string; es?: string } | null;
  validated_at: string | null;
  last_report_at: string | null;
  resolved_at: string | null;
};
