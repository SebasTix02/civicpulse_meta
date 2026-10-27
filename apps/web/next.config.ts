import type { NextConfig } from "next";

// NEXT_PUBLIC_* values are inlined into the browser bundle. A Supabase secret key there
// bypasses RLS for anyone who opens the site, so the build refuses it.
function assertPublicSupabaseKey(raw: string | undefined): void {
  const key = raw?.trim().replace(/^["']|["']$/g, "");
  if (!key) return;
  if (key.startsWith("sb_secret_")) {
    throw new Error("NEXT_PUBLIC_SUPABASE_ANON_KEY holds a Supabase SECRET key: use the publishable (sb_publishable_) or anon key, and rotate the secret one");
  }
  const payload = key.split(".")[1];
  if (payload) {
    try {
      const claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
      if (claims.role === "service_role") {
        throw new Error("NEXT_PUBLIC_SUPABASE_ANON_KEY holds the service_role key: use the anon key, and rotate the JWT secret");
      }
    } catch (err) {
      if (err instanceof Error && err.message.startsWith("NEXT_PUBLIC")) throw err;
    }
  }
}

assertPublicSupabaseKey(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY);

const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
