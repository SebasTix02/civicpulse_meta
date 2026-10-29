"use client";

import { useEffect } from "react";

// If Supabase falls back to the Site URL (a redirect not in its allow list), the sign-in tokens
// land on whatever page that is. Send them on to /auth/callback, which finishes the sign-in.
export function AuthForward() {
  useEffect(() => {
    const { pathname, hash, search } = window.location;
    if (pathname === "/auth/callback") return;
    if (/[#&]access_token=/.test(hash) || /[?&]code=/.test(search)) {
      window.location.replace(`/auth/callback${search}${hash}`);
    }
  }, []);
  return null;
}
