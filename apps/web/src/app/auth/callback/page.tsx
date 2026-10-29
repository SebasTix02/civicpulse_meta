"use client";

import { useEffect, useState } from "react";
import { browserClient } from "@/lib/supabase";

// Sign-in link target. Panel links use the implicit flow (tokens in the #hash), so they work in
// any browser; a ?code (PKCE) link is still accepted from the browser that asked for it. Only
// same-origin /panel paths are followed.
export default function AuthCallback() {
  const [error, setError] = useState("");

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const next = query.get("next") ?? "/panel";
    const target = /^\/panel(\/.*)?$/.test(next) ? next : "/panel";
    const supabase = browserClient();
    const done = ({ error: e }: { error: { message: string } | null }) =>
      e ? setError(e.message) : window.location.replace(target);

    const access_token = hash.get("access_token");
    const refresh_token = hash.get("refresh_token");
    const code = query.get("code");
    if (access_token && refresh_token) void supabase.auth.setSession({ access_token, refresh_token }).then(done);
    else if (code) void supabase.auth.exchangeCodeForSession(code).then(done);
    else setError(hash.get("error_description") ?? query.get("error_description") ?? "missing sign-in data");
  }, []);

  return <p className="muted">{error ? `Error: ${error}` : "…"}</p>;
}
