"use client";

import { useEffect, useState } from "react";
import { browserClient } from "@/lib/supabase";

// Magic-link target (PKCE): the code verifier was stored by this browser when the link was
// requested, so the exchange happens here. Only same-origin /panel paths are followed.
export default function AuthCallback() {
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const next = params.get("next") ?? "/panel";
    const target = /^\/panel(\/.*)?$/.test(next) ? next : "/panel";
    if (!code) {
      setError(params.get("error_description") ?? "missing code");
      return;
    }
    browserClient()
      .auth.exchangeCodeForSession(code)
      .then(({ error: e }) => (e ? setError(e.message) : window.location.replace(target)));
  }, []);

  return <p className="muted">{error ? `Error: ${error}` : "…"}</p>;
}
