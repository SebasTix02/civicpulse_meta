import { createBrowserClient } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

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
