import { cookies, headers } from "next/headers";
import { pickLang, type Lang } from "./i18n";

// Cookie set by the language switch wins; otherwise the browser's language.
export async function currentLang(): Promise<Lang> {
  const fromCookie = (await cookies()).get("lang")?.value;
  if (fromCookie) return pickLang(fromCookie);
  const accept = (await headers()).get("accept-language") ?? "";
  return pickLang(accept.slice(0, 2));
}
